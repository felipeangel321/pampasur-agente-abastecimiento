"""Rol Comprador: arma la orden del relevo dentro del presupuesto y la carga (EP2).

No usa LLM a propósito. Aquí se deciden cantidades y montos, y esa decisión tiene
que ser auditable y repetible: la misma entrada da siempre la misma orden. Los
agentes LLM (src/agentes.py) explican y comunican esta orden, pero no la calculan.

Cada decisión que toma queda escrita en `decisiones`, para que el informe y los
correos muestren por qué la orden es distinta de la lista que llegó por WhatsApp.
"""

import pandas as pd

from src.config import CARGA_MAX_KG_POR_PERSONA, DIAS_TURNO, UMBRAL_LISTA_COPIADA
from src.analisis import comparar_con_relevo_anterior, evaluar_lista, lista_recomendada
from src.datos import cargar_catalogo, cargar_centros, cargar_contactos, presupuesto_relevo


def pesos(n: float) -> str:
    return f"${n:,.0f}".replace(",", ".")


def _item(sku: str, cantidad: int, motivo: str) -> dict:
    prod = cargar_catalogo().loc[sku]
    return {
        "sku": sku,
        "producto": prod["producto"],
        "categoria": prod["categoria"],
        "prioridad": prod["prioridad"],
        "cantidad": int(cantidad),
        "precio_clp": int(prod["precio_clp"]),
        "subtotal_clp": int(cantidad * prod["precio_clp"]),
        "peso_kg": round(float(cantidad * prod["peso_kg"]), 2),
        "transporte": "con el turno",
        "motivo": motivo,
    }


def _total(items: dict[str, dict], campo: str = "subtotal_clp") -> float:
    return sum(i[campo] for i in items.values())


def _viveres_aseo(items: dict[str, dict]) -> float:
    """Lo que cuenta para el presupuesto: todo menos el mantenimiento, que va como extra."""
    return sum(i["subtotal_clp"] for i in items.values() if i["categoria"] != "mantenimiento")


def montos(orden: dict) -> str:
    """Víveres y aseo contra el presupuesto y, aparte, el extra de mantenimiento si lo hay."""
    texto = f"{pesos(orden['viveres_aseo_clp'])} de un presupuesto de {pesos(orden['presupuesto_clp'])}"
    if extra := orden["extra_mantenimiento"]:
        texto += f", más un extra de mantenimiento de {pesos(extra['monto_clp'])}"
    return texto


def armar_orden(
    centro_id: str,
    inventario: dict[str, float] | None = None,
    lista_pedida: dict[str, float] | None = None,
    sku_anteriores: set[str] | list[str] | None = None,
    fecha: str | None = None,
) -> dict:
    """Orden de compra del relevo a partir del inventario del turno saliente.

    Pasos, en orden de prioridad (política de abastecimiento):
    1. Base: necesidad del turno menos inventario (esencial y normal).
    2. Lista de WhatsApp, si llegó: se agregan los opcionales que hacen falta y el
       mantenimiento nuevo; el resto de lo pedido de más queda como observación.
       Se avisa si la lista es copia del relevo anterior o si supera el presupuesto.
    3. Presupuesto de víveres y aseo: es un tope que nunca se supera; se recorta lo
       opcional y después lo normal, nunca lo esencial. El mantenimiento es ocasional
       y va aparte, como un extra que aprueba el Responsable de compras.
    4. Carga (solo centros con bus): lo no esencial más pesado va en envío aparte.
    """
    centro = cargar_centros().loc[centro_id]
    personas = int(centro["cuidadores_por_turno"])
    presupuesto = presupuesto_relevo(centro_id)
    inventario = inventario or {}
    anteriores = set(sku_anteriores or ())
    decisiones, alertas, recortes, observaciones = [], [], [], []

    # 1. Necesidad del turno menos inventario
    if not inventario:
        alertas.append("El turno saliente no reportó inventario: se compra la necesidad completa del turno.")
    items = {
        f["sku"]: _item(f["sku"], f["cantidad"], "necesidad del turno menos inventario")
        for f in lista_recomendada(centro_id, inventario).to_dict("records")
    }
    decisiones.append(
        f"Se calculó la necesidad de {DIAS_TURNO} días para {personas} cuidador(es) y se descontó "
        f"el inventario reportado: {len(items)} productos por comprar."
    )

    # 2. Contraste con la lista de WhatsApp
    total_pedido = exceso_detectado = 0
    copia = None
    if lista_pedida:
        ev = evaluar_lista(centro_id, lista_pedida, inventario, anteriores)
        subtotales = ev["pedida"] * ev["precio_clp"]
        total_pedido = int(subtotales.sum())
        pedido_viveres_aseo = int(subtotales[ev["categoria"] != "mantenimiento"].sum())
        for f in ev.to_dict("records"):
            sku = f["sku"]
            if f["prioridad"] == "opcional" and f["recomendada"] > 0:
                cantidad = min(int(f["pedida"]), int(f["recomendada"]))
                items[sku] = _item(sku, cantidad, "opcional pedido en la lista")
            elif f["categoria"] == "mantenimiento":
                if sku in anteriores:
                    f["exceso"], f["exceso_clp"] = f["pedida"], int(f["pedida"] * f["precio_clp"])
                    f["motivo"] = "mantenimiento repetido del relevo anterior: confirmar si el trabajo continúa"
                    recortes.append({"producto": f["producto"], "cantidad": f["pedida"], "motivo": f["motivo"]})
                else:
                    items[sku] = _item(sku, f["pedida"], "mantenimiento solicitado (indicar el trabajo)")
            if f["exceso"] > 0 and not f["motivo"]:
                f["motivo"] = "pedido sobre la necesidad del turno (+20% de tolerancia)"
            if f["motivo"]:
                observaciones.append(
                    {k: f[k] for k in ("producto", "pedida", "inventario", "recomendada", "exceso", "exceso_clp", "motivo")}
                )
        exceso_detectado = int(sum(o["exceso_clp"] for o in observaciones))
        no_pedidos = [i["producto"] for s, i in items.items() if s not in lista_pedida]
        decisiones.append(
            f"La lista de WhatsApp sumaba {pesos(total_pedido)}; {len(observaciones)} productos venían "
            f"de más o repetidos ({pesos(exceso_detectado)} en compras innecesarias)."
        )
        if no_pedidos:
            decisiones.append(
                f"Se agregan {len(no_pedidos)} productos que no venían en la lista pero el inventario "
                f"no alcanza para el turno: {', '.join(no_pedidos[:5])}{'…' if len(no_pedidos) > 5 else ''}."
            )
        if pedido_viveres_aseo > presupuesto:
            alertas.append(
                f"Sobregasto en la lista: pedía {pesos(pedido_viveres_aseo)} en víveres y aseo, sobre el "
                f"presupuesto de {pesos(presupuesto)}. La orden se corrigió a lo que el turno necesita."
            )
        comparacion = comparar_con_relevo_anterior(centro_id, fecha, lista_pedida) if fecha else None
        if comparacion and comparacion["porcentaje"] >= 100 * UMBRAL_LISTA_COPIADA:
            copia = comparacion
            alertas.append(
                f"Lista copiada: {copia['iguales']} de {copia['total']} productos ({copia['porcentaje']:g}%) "
                f"se piden igual que en el relevo del {copia['fecha_anterior']}, sin considerar el inventario."
            )
    else:
        decisiones.append("No llegó lista de WhatsApp: la orden sale solo del inventario y la duración de cada producto.")

    # 3. Presupuesto de víveres y aseo: es un tope. Se recorta lo opcional, después lo normal, nunca lo esencial
    for prioridad in ("opcional", "normal"):
        candidatos = sorted(
            (i for i in items.values() if i["prioridad"] == prioridad), key=lambda i: -i["subtotal_clp"]
        )
        for item in candidatos:
            if _viveres_aseo(items) <= presupuesto:
                break
            del items[item["sku"]]
            recortes.append({"producto": item["producto"], "cantidad": item["cantidad"], "motivo": f"{prioridad} recortado por presupuesto"})
            decisiones.append(f"Se recorta {item['producto']} ({prioridad}) para no pasar el presupuesto.")
    if _viveres_aseo(items) > presupuesto:
        alertas.append(
            f"Lo esencial ya suma {pesos(_viveres_aseo(items))}, sobre el presupuesto de {pesos(presupuesto)}: "
            "revisar el inventario reportado antes de comprar."
        )

    # El mantenimiento es ocasional (pintura, arreglos, luz o agua): va como extra, fuera del presupuesto
    mantenimiento = [i for i in items.values() if i["categoria"] == "mantenimiento"]
    extra = None
    if mantenimiento:
        extra = {
            "monto_clp": int(sum(i["subtotal_clp"] for i in mantenimiento)),
            "items": [{k: i[k] for k in ("producto", "cantidad", "subtotal_clp")} for i in mantenimiento],
        }
        decisiones.append(
            f"El mantenimiento solicitado ({pesos(extra['monto_clp'])}) va como extra, fuera del presupuesto: "
            "lo aprueba el Responsable de compras en el mismo correo de la orden."
        )

    # 4. Carga en bus: lo no esencial más pesado se manda aparte
    carga_max = CARGA_MAX_KG_POR_PERSONA * personas if centro["traslado"] == "bus" else None
    if carga_max is not None:
        en_bus = _total(items, "peso_kg")
        if en_bus > carga_max:
            for item in sorted(items.values(), key=lambda i: -i["peso_kg"]):
                if en_bus <= carga_max:
                    break
                if item["prioridad"] != "esencial":
                    item["transporte"] = "envío aparte"
                    en_bus -= item["peso_kg"]
            aparte = [i["producto"] for i in items.values() if i["transporte"] == "envío aparte"]
            decisiones.append(
                f"La compra pesa {_total(items, 'peso_kg'):.1f} kg y en el bus caben {carga_max} kg: "
                f"{len(aparte)} productos no esenciales van en envío aparte (supuesto: encomienda)."
            )
            if en_bus > carga_max:
                alertas.append(
                    f"Solo lo esencial ya pesa {en_bus:.1f} kg (máximo {carga_max} kg en el bus): "
                    "coordinar transporte adicional."
                )

    filas = sorted(items.values(), key=lambda i: (i["categoria"], i["producto"]))
    return {
        "centro": centro_id,
        "nombre_centro": centro["nombre"],
        "personas": personas,
        "traslado": centro["traslado"],
        "presupuesto_clp": int(presupuesto),
        "viveres_aseo_clp": int(_viveres_aseo(items)),
        "total_clp": int(_total(items)),
        "peso_kg": round(_total(items, "peso_kg"), 1),
        "carga_max_kg": carga_max,
        "peso_en_bus_kg": round(sum(i["peso_kg"] for i in filas if i["transporte"] == "con el turno"), 1),
        "items": filas,
        "lista_pedida_clp": total_pedido,
        "lista_copiada": copia,
        "exceso_detectado_clp": exceso_detectado,
        "observaciones": observaciones,
        "recortes": recortes,
        "extra_mantenimiento": extra,
        "alertas": alertas,
        "decisiones": decisiones,
    }


# --- A quién se le informa (protocolo de relevo) ---


def equipo_entrante(centro_id: str, fecha) -> str:
    """Equipo (A o B) que entra en ese relevo. (supuesto) El equipo A entró en el primer relevo."""
    primer = cargar_centros().loc[centro_id, "primer_relevo"]
    relevos = (pd.Timestamp(fecha) - primer).days // DIAS_TURNO
    return "A" if relevos % 2 == 0 else "B"


def _correo_de(rol: str) -> str:
    contactos = cargar_contactos()
    return contactos.loc[contactos["rol"] == rol, "email"].iloc[0]


def correos_requeridos(orden: dict, fecha) -> list[dict]:
    """Correos que el relevo necesita, según el traslado del centro y el mantenimiento.

    - Siempre: la orden al Responsable de compras (con el extra de mantenimiento, si
      lo hay, en el mismo correo, porque es la misma persona que lo aprueba).
    - Centro con vehículo: aviso de entrega al prevencionista.
    - Centro con bus: a los cuidadores entrantes, qué lleva cada uno.
    """
    centro = orden["centro"]
    proposito = "Orden de compra del relevo"
    if orden["extra_mantenimiento"]:
        proposito += f" y aprobación del extra de mantenimiento por {pesos(orden['extra_mantenimiento']['monto_clp'])}"
    requeridos = [
        {
            "id": "orden",
            "para": [_correo_de("responsable_compras")],
            "rol": "Responsable de compras",
            "proposito": proposito,
            "adjuntos": ["orden_compra.xlsx", "reporte_relevo.pdf"],
        }
    ]
    if orden["traslado"] == "bus":
        contactos = cargar_contactos()
        equipo = equipo_entrante(centro, fecha)
        entrantes = contactos[
            (contactos["rol"] == "cuidador")
            & (contactos["centro"] == centro)
            & contactos["nombre"].str.contains(f" {equipo}\\d", regex=True)
        ]
        por_persona = orden["peso_en_bus_kg"] / orden["personas"]
        requeridos.append(
            {
                "id": "carga",
                "para": entrantes["email"].tolist(),
                "rol": f"Cuidadores entrantes (equipo {equipo})",
                "proposito": f"Qué víveres llevan en el bus: unos {por_persona:.1f} kg por persona",
                "adjuntos": ["orden_compra.xlsx"],
            }
        )
    else:
        requeridos.append(
            {
                "id": "entrega",
                "para": [_correo_de("prevencionista")],
                "rol": "Prevencionista",
                "proposito": f"Coordinar la entrega en vehículo: {orden['peso_kg']:.1f} kg de víveres",
                "adjuntos": ["orden_compra.xlsx"],
            }
        )
    return requeridos
