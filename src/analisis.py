"""Reglas de duración de productos: cuánto se necesita por turno y qué compras sobran.

Estas funciones no usan LLM: son el cálculo determinista que después el agente
(EP2) expone como herramientas.
"""

import math

import pandas as pd

from src.config import DIAS_TURNO, TOLERANCIA_EXCESO, UMBRAL_CANTIDAD_EXCESIVA
from src.datos import cargar_catalogo, cargar_centros, cargar_historial, cargar_inventarios, presupuesto_relevo

PRIORIDADES_BASE = ("esencial", "normal")


def _factor(escala: str, personas: int) -> int:
    return personas if escala == "persona" else 1


def necesidad_turno(centro_id: str, prioridades=PRIORIDADES_BASE) -> pd.Series:
    """Unidades de cada producto que consume el centro en un turno de 14 días."""
    personas = int(cargar_centros().loc[centro_id, "cuidadores_por_turno"])
    cat = cargar_catalogo()
    cat = cat[cat["prioridad"].isin(prioridades) & cat["rendimiento_dias"].notna()]
    factores = cat["escala"].map(lambda e: _factor(e, personas))
    return (factores * DIAS_TURNO / cat["rendimiento_dias"]).rename("necesidad")


def turnos_que_cubre(centro_id: str, sku: str, cantidad: float) -> float | None:
    """Cuántos turnos alcanza una cantidad de un producto en ese centro (None si no aplica)."""
    prod = cargar_catalogo().loc[sku]
    if pd.isna(prod["rendimiento_dias"]):
        return None
    personas = int(cargar_centros().loc[centro_id, "cuidadores_por_turno"])
    consumo_turno = _factor(prod["escala"], personas) * DIAS_TURNO / prod["rendimiento_dias"]
    return cantidad / consumo_turno


def evaluar_lista(
    centro_id: str,
    lista: dict[str, float],
    inventario: dict[str, float] | None = None,
    sku_comprados_relevo_anterior: set[str] | None = None,
) -> pd.DataFrame:
    """Compara una lista pedida contra la necesidad real del turno.

    Devuelve una fila por producto con la cantidad recomendada, el exceso en
    unidades y pesos, y el motivo de cada observación.
    """
    inventario = inventario or {}
    anteriores = sku_comprados_relevo_anterior or set()
    cat = cargar_catalogo()
    necesidad = necesidad_turno(centro_id, prioridades=("esencial", "normal", "opcional"))

    filas = []
    for sku, pedida in lista.items():
        prod = cat.loc[sku]
        inv = float(inventario.get(sku, 0))
        motivos = []
        if pd.isna(prod["rendimiento_dias"]):
            # Mantenimiento: no tiene consumo periódico, se evalúa por solicitud.
            nec, recomendada, exceso = None, pedida, 0
        else:
            nec = float(necesidad.get(sku, 0))
            recomendada = max(0, math.ceil(nec - inv))
            tope_aceptable = max(0, math.ceil(nec * TOLERANCIA_EXCESO - inv))
            exceso = max(0, pedida - tope_aceptable)
            if inv >= nec and pedida > 0:
                motivos.append(f"el inventario ({inv:g}) ya cubre el turno")
            if nec > 0 and pedida > UMBRAL_CANTIDAD_EXCESIVA * math.ceil(nec):
                cubre = turnos_que_cubre(centro_id, sku, pedida)
                motivos.append(f"cantidad excesiva: {pedida:g} alcanza para {cubre:.1f} turnos")
            cubre_uno = turnos_que_cubre(centro_id, sku, 1)
            if sku in anteriores and cubre_uno and cubre_uno >= 2 and exceso > 0:
                motivos.append(f"larga duración (1 unidad ≈ {cubre_uno:.1f} turnos) pedida en relevos consecutivos")
        filas.append(
            {
                "sku": sku,
                "producto": prod["producto"],
                "categoria": prod["categoria"],
                "prioridad": prod["prioridad"],
                "pedida": pedida,
                "inventario": inv,
                "necesidad": None if nec is None else round(nec, 2),
                "recomendada": recomendada,
                "exceso": exceso,
                "precio_clp": int(prod["precio_clp"]),
                "exceso_clp": int(exceso * prod["precio_clp"]),
                "motivo": "; ".join(motivos),
            }
        )
    return pd.DataFrame(filas)


def lista_recomendada(centro_id: str, inventario: dict[str, float] | None = None) -> pd.DataFrame:
    """Lista de compra sugerida a partir del inventario que deja el turno saliente."""
    inventario = inventario or {}
    cat = cargar_catalogo()
    nec = necesidad_turno(centro_id)
    filas = []
    for sku, n in nec.items():
        cantidad = max(0, math.ceil(n - inventario.get(sku, 0)))
        if cantidad:
            prod = cat.loc[sku]
            filas.append(
                {
                    "sku": sku,
                    "producto": prod["producto"],
                    "categoria": prod["categoria"],
                    "prioridad": prod["prioridad"],
                    "cantidad": cantidad,
                    "precio_clp": int(prod["precio_clp"]),
                    "subtotal_clp": int(cantidad * prod["precio_clp"]),
                    "peso_kg": round(cantidad * prod["peso_kg"], 2),
                }
            )
    return pd.DataFrame(filas)


# --- Análisis del historial (dimensiona el problema para el informe) ---


def inventario_en_relevo(centro_id: str, fecha) -> dict[str, float]:
    """Inventario que reportó el turno saliente en ese relevo."""
    inv = cargar_inventarios()
    filas = inv[(inv["centro"] == centro_id) & (inv["fecha_relevo"] == pd.Timestamp(fecha))]
    return dict(zip(filas["sku"], filas["cantidad_restante"]))


def skus_relevo_anterior(centro_id: str, fecha) -> set[str]:
    """Productos comprados en el relevo inmediatamente anterior a esa fecha."""
    hist = cargar_historial()
    previas = hist[(hist["centro"] == centro_id) & (hist["fecha_relevo"] < pd.Timestamp(fecha))]
    if previas.empty:
        return set()
    return set(previas.loc[previas["fecha_relevo"] == previas["fecha_relevo"].max(), "sku"])


def comparar_con_relevo_anterior(centro_id: str, fecha, lista: dict[str, float]) -> dict | None:
    """Qué parte de la lista pide lo mismo (producto y cantidad) que la compra del relevo anterior.

    Una lista copiada del chat del turno anterior coincide en todo. En el historial, las
    listas copiadas coinciden 100% y las armadas de nuevo como máximo un 91%.
    """
    hist = cargar_historial()
    previas = hist[(hist["centro"] == centro_id) & (hist["fecha_relevo"] < pd.Timestamp(fecha))]
    if previas.empty or not lista:
        return None
    ultima = previas["fecha_relevo"].max()
    anterior = previas[previas["fecha_relevo"] == ultima]
    anterior = dict(zip(anterior["sku"], anterior["cantidad"]))
    iguales = sum(1 for sku, cantidad in lista.items() if anterior.get(sku) == cantidad)
    return {
        "fecha_anterior": f"{ultima:%Y-%m-%d}",
        "iguales": iguales,
        "total": len(lista),
        "porcentaje": round(100 * iguales / len(lista), 1),
    }


def historial_evaluado() -> pd.DataFrame:
    """Cada compra histórica evaluada contra el inventario y la necesidad de ese relevo."""
    hist = cargar_historial()
    inv = cargar_inventarios()
    claves = ["centro", "fecha_relevo", "sku"]
    df = hist.merge(inv[claves + ["cantidad_restante"]], on=claves, how="left")
    df["cantidad_restante"] = df["cantidad_restante"].fillna(0)

    evaluaciones = []
    for (centro, fecha), grupo in df.groupby(["centro", "fecha_relevo"], sort=True):
        previas = df[(df["centro"] == centro) & (df["fecha_relevo"] < fecha)]
        sku_previos = set()
        if not previas.empty:
            ultima = previas["fecha_relevo"].max()
            sku_previos = set(previas.loc[previas["fecha_relevo"] == ultima, "sku"])
        ev = evaluar_lista(
            centro,
            dict(zip(grupo["sku"], grupo["cantidad"])),
            dict(zip(grupo["sku"], grupo["cantidad_restante"])),
            sku_previos,
        )
        # Mantenimiento repetido en una lista copiada: todo es exceso.
        copiada = bool(grupo["lista_copiada"].iloc[0])
        mant = ev["categoria"].eq("mantenimiento")
        if copiada and mant.any():
            ev.loc[mant, "exceso"] = ev.loc[mant, "pedida"]
            ev.loc[mant, "exceso_clp"] = ev.loc[mant, "pedida"] * ev.loc[mant, "precio_clp"]
            ev.loc[mant, "motivo"] = "mantenimiento repetido por lista copiada"
        ev["centro"], ev["fecha_relevo"], ev["lista_copiada"] = centro, fecha, copiada
        evaluaciones.append(ev)
    return pd.concat(evaluaciones, ignore_index=True)


def resumen_por_centro() -> pd.DataFrame:
    """Gasto, exceso y merma por centro en todo el historial."""
    ev = historial_evaluado()
    ev["gasto_clp"] = ev["pedida"] * ev["precio_clp"]
    relevos = ev.groupby(["centro", "fecha_relevo"]).agg(
        gasto_clp=("gasto_clp", "sum"),
        exceso_clp=("exceso_clp", "sum"),
        lista_copiada=("lista_copiada", "first"),
    ).reset_index()
    relevos["presupuesto_clp"] = relevos["centro"].map(presupuesto_relevo)
    relevos["sobre_presupuesto"] = relevos["gasto_clp"] > relevos["presupuesto_clp"]

    inv = cargar_inventarios()
    precios = cargar_catalogo()["precio_clp"]
    inv["merma_clp"] = inv["merma"] * inv["sku"].map(precios)

    resumen = relevos.groupby("centro").agg(
        relevos=("fecha_relevo", "count"),
        listas_copiadas_pct=("lista_copiada", "mean"),
        relevos_sobre_presupuesto=("sobre_presupuesto", "sum"),
        gasto_total_clp=("gasto_clp", "sum"),
        gasto_promedio_relevo_clp=("gasto_clp", "mean"),
        exceso_total_clp=("exceso_clp", "sum"),
    )
    resumen["merma_perecibles_clp"] = inv.groupby("centro")["merma_clp"].sum()
    resumen["exceso_pct_del_gasto"] = resumen["exceso_total_clp"] / resumen["gasto_total_clp"]
    resumen["listas_copiadas_pct"] = (resumen["listas_copiadas_pct"] * 100).round(1)
    resumen["exceso_pct_del_gasto"] = (resumen["exceso_pct_del_gasto"] * 100).round(1)
    return resumen.round(0)


def productos_mas_sobrecomprados(top: int = 10) -> pd.DataFrame:
    """Productos que más plata se llevan en compras innecesarias."""
    ev = historial_evaluado()
    ev = ev[ev["exceso"] > 0]
    agg = ev.groupby(["sku", "producto"]).agg(
        veces_con_exceso=("exceso", "count"),
        unidades_exceso=("exceso", "sum"),
        exceso_clp=("exceso_clp", "sum"),
    )
    return agg.sort_values("exceso_clp", ascending=False).head(top).reset_index()
