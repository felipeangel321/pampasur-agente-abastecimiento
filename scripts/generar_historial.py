"""Genera el historial SIMULADO de relevos (abril a octubre de 2026).

Reproduce el problema descrito por Pampasur:
- Los cuidadores copian la lista del turno anterior desde WhatsApp (PROB_COPIA).
- Cuando no copian, revisan la bodega a medias y redondean hacia arriba.
- Hay hábitos fijos que se repiten siempre (HABITOS): detergente para ropa todos
  los turnos aunque dura ~3, y 5 kg de azúcar por lista.

Salidas: data/historial_compras.csv, data/inventarios.csv y data/listas_whatsapp/*.txt.
Cuando lleguen listas reales, se reemplazan estos archivos por los reales anonimizados.

Uso:  uv run python -m scripts.generar_historial
"""

import math
import random
import sys

import pandas as pd

from src.analisis import necesidad_turno
from src.config import DATA_DIR, DIAS_TURNO
from src.datos import cargar_catalogo, cargar_centros

SEMILLA = 42
FECHA_FIN = pd.Timestamp("2026-10-20")
PROB_COPIA = 0.55
PROB_MANTENIMIENTO = 0.15
LISTAS_WHATSAPP_POR_CENTRO = 3

# Hábitos observados por Felipe: cantidades fijas que aparecen en cada lista.
HABITOS = {
    "maqui": {"A003": 1, "L005": 5},
    "chaiquen": {"A003": 1},
    "penasmo": {"A003": 1, "L006": 2},
    "porcelana": {"A003": 1, "L005": 5},
    "tarao": {"A003": 2, "L005": 5},
}

# Gustos de cada centro: multiplican el consumo esperado (supuesto, para que no sean idénticos).
GUSTOS = {
    "maqui": {"P002": 0.4, "L018": 1.3},
    "chaiquen": {"L019": 1.8, "L018": 0.5},
    "penasmo": {"P003": 1.3, "L012": 0.3},
    "porcelana": {"P001": 1.3, "L016": 0.5},
    "tarao": {"L018": 0.4, "L019": 2.0, "P010": 1.4},
}


def _linea_whatsapp(prod: pd.Series, cantidad: int, rng: random.Random) -> str:
    alias = prod["sinonimos"].split("|")[0]
    if prod["unidad"] == "1 kg":
        return rng.choice([f"{cantidad} kg {alias}", f"{alias} {cantidad} kilos", f"- {cantidad}kg {alias}"])
    return rng.choice([f"{cantidad} {alias}", f"{alias} x{cantidad}", f"- {alias} {cantidad}"])


def simular_centro(centro_id: str, rng: random.Random) -> tuple[list, list, list]:
    cat = cargar_catalogo()
    centro = cargar_centros().loc[centro_id]
    nec = necesidad_turno(centro_id)
    mantenimiento = cat.index[cat["categoria"] == "mantenimiento"].tolist()

    compras, inventarios, listas = [], [], []
    inventario: dict[str, float] = {}
    merma: dict[str, float] = {}
    lista_anterior, cuerpo_anterior = None, None
    fecha = centro["primer_relevo"]

    while fecha <= FECHA_FIN:
        # 1. El turno saliente reporta lo que queda en bodega y lo que se perdió.
        for sku in sorted(set(inventario) | set(merma)):
            restante, perdido = round(inventario.get(sku, 0), 1), round(merma.get(sku, 0), 1)
            if restante > 0 or perdido > 0:
                inventarios.append(
                    {
                        "centro": centro_id,
                        "fecha_relevo": fecha.date(),
                        "sku": sku,
                        "producto": cat.loc[sku, "producto"],
                        "cantidad_restante": restante,
                        "merma": perdido,
                    }
                )

        # 2. La lista: copiada del chat o armada revisando la bodega a medias.
        copiada = lista_anterior is not None and rng.random() < PROB_COPIA
        if copiada:
            lista, cuerpo = dict(lista_anterior), cuerpo_anterior
        else:
            lista = {}
            for sku, n in nec.items():
                q = math.ceil(max(0, n * rng.uniform(1.0, 1.25) - 0.5 * inventario.get(sku, 0)))
                if q:
                    lista[sku] = q
            lista.update(HABITOS.get(centro_id, {}))
            if rng.random() < PROB_MANTENIMIENTO:
                for sku in rng.sample(mantenimiento, k=rng.randint(1, 2)):
                    lista[sku] = rng.randint(1, 2)
            cuerpo = [_linea_whatsapp(cat.loc[s], q, rng) for s, q in lista.items()]
        listas.append((fecha, cuerpo))

        # 3. Compra en Lider.
        for sku, q in lista.items():
            precio = int(cat.loc[sku, "precio_clp"])
            compras.append(
                {
                    "centro": centro_id,
                    "fecha_relevo": fecha.date(),
                    "sku": sku,
                    "producto": cat.loc[sku, "producto"],
                    "categoria": cat.loc[sku, "categoria"],
                    "cantidad": q,
                    "precio_unitario_clp": precio,
                    "subtotal_clp": q * precio,
                    "lista_copiada": copiada,
                }
            )

        # 4. Consumo durante los 14 días.
        merma = {}
        for sku in set(inventario) | set(lista):
            disponible = inventario.get(sku, 0) + lista.get(sku, 0)
            prod = cat.loc[sku]
            if prod["categoria"] == "mantenimiento":
                # Si el pedido era real se usa en el trabajo; si venía copiado queda guardado.
                consumo = 0 if copiada else lista.get(sku, 0)
            else:
                consumo = nec.get(sku, 0) * GUSTOS.get(centro_id, {}).get(sku, 1.0) * rng.uniform(0.8, 1.1)
            resto = disponible - min(disponible, consumo)
            if prod["perecible"]:
                if resto > 0.05:
                    merma[sku] = resto
                inventario[sku] = 0
            else:
                inventario[sku] = resto
        inventario = {s: q for s, q in inventario.items() if q > 0.05}

        lista_anterior, cuerpo_anterior = lista, cuerpo
        fecha += pd.Timedelta(days=DIAS_TURNO)

    return compras, inventarios, listas


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    rng = random.Random(SEMILLA)
    carpeta_wsp = DATA_DIR / "listas_whatsapp"
    for viejo in carpeta_wsp.glob("*.txt"):
        viejo.unlink()

    todas_compras, todos_inventarios = [], []
    for centro_id, centro in cargar_centros().iterrows():
        compras, inventarios, listas = simular_centro(centro_id, rng)
        todas_compras += compras
        todos_inventarios += inventarios
        for fecha, cuerpo in listas[-LISTAS_WHATSAPP_POR_CENTRO:]:
            texto = "\n".join([f"Lista {centro['nombre']} relevo {fecha:%d/%m}", *cuerpo]) + "\n"
            (carpeta_wsp / f"{centro_id}_{fecha:%Y-%m-%d}.txt").write_text(texto, encoding="utf-8")

    pd.DataFrame(todas_compras).to_csv(DATA_DIR / "historial_compras.csv", index=False, encoding="utf-8")
    pd.DataFrame(todos_inventarios).to_csv(DATA_DIR / "inventarios.csv", index=False, encoding="utf-8")
    print(f"{len(todas_compras)} líneas de compra, {len(todos_inventarios)} líneas de inventario.")


if __name__ == "__main__":
    main()
