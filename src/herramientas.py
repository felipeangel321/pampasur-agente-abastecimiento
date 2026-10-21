"""Herramientas de los agentes (EP2, IE1).

Envuelven funciones deterministas de src/analisis.py, src/compra.py, src/rag.py y
src/correo.py. El agente decide cuándo usarlas; el cálculo lo hacen ellas.

- Consulta (Analista): consultar_centro, consultar_inventario, revisar_producto,
  historial_producto, buscar_politica, recordar_relevos.
- Razonamiento sobre datos (Analista): calcular_orden, que corre el Comprador.
- Escritura (Correos): enviar_correo, con aprobación humana antes de ejecutarse.

Las salidas son texto corto a propósito: cada línea que devuelven se paga en tokens
en la siguiente llamada al modelo.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from langchain.tools import ToolRuntime, tool

from src import correo
from src.analisis import inventario_en_relevo, necesidad_turno, turnos_que_cubre
from src.compra import armar_orden, pesos
from src.datos import cargar_catalogo, cargar_centros, cargar_historial, presupuesto_relevo
from src.memoria import recordar
from src.rag import _normalizar, buscar_politicas, buscar_productos, formatear_contexto


def _centro_id(centro: str) -> str:
    """Acepta 'Tarao', 'Centro Tarao', 'tarao' o 'Chiloé' y devuelve el id ('tarao')."""
    texto = _normalizar(centro).removeprefix("centro ").strip()
    for cid, c in cargar_centros().iterrows():
        if texto in (cid, _normalizar(c["nombre"]).removeprefix("centro "), *_normalizar(c["ubicacion"]).split()):
            return cid
    raise ValueError(f"No conozco el centro '{centro}'. Centros: {', '.join(cargar_centros().index)}")


def _sku(producto: str) -> str:
    doc, _, _ = buscar_productos(producto, k=1)[0]
    return doc.metadata["sku"]


# --- Consulta ---


@tool
def consultar_centro(centro: str) -> str:
    """Dotación, presupuesto por relevo y traslado de un centro (por ejemplo 'Tarao')."""
    cid = _centro_id(centro)
    c = cargar_centros().loc[cid]
    traslado = "bus (los cuidadores llevan los víveres)" if c["traslado"] == "bus" else "vehículo del prevencionista"
    return (
        f"{c['nombre']} ({c['ubicacion']}): {c['cuidadores_por_turno']} cuidador(es) por turno, "
        f"presupuesto {pesos(presupuesto_relevo(cid))} por relevo, traslado en {traslado}."
    )


@tool
def consultar_inventario(centro: str, fecha: str) -> str:
    """Lo que dejó en bodega el turno saliente en el relevo de esa fecha (AAAA-MM-DD)."""
    inventario = inventario_en_relevo(_centro_id(centro), fecha)
    if not inventario:
        return f"No hay reporte de inventario para {centro} el {fecha}."
    nombres = cargar_catalogo()["producto"]
    return "Unidades del catálogo que quedan: " + "; ".join(
        f"{nombres[s]}: {c:g} unidades" for s, c in sorted(inventario.items()) if c > 0
    )


@tool
def revisar_producto(producto: str, centro: str) -> str:
    """Duración de un producto en un centro: cuánto se necesita por turno y cuántos turnos rinde 1 unidad."""
    cid, sku = _centro_id(centro), _sku(producto)
    p = cargar_catalogo().loc[sku]
    if pd.isna(p["rendimiento_dias"]):
        return f"{p['producto']} ({sku}) es de mantenimiento: se compra por solicitud, indicando el trabajo."
    necesidad = necesidad_turno(cid, prioridades=("esencial", "normal", "opcional"))[sku]
    # La regla va con su cita: así el modelo no tiene que recordarla (ni inventarla).
    return (
        f"{p['producto']} ({sku}), prioridad {p['prioridad']}, {pesos(p['precio_clp'])}: en {centro} se "
        f"necesitan {necesidad:.2f} unidades por turno y 1 unidad rinde {turnos_que_cubre(cid, sku, 1):.1f} turnos "
        f"(dato {p['fuente_dato']}). Regla: se compra solo lo que falta para el turno que entra, descontando "
        "el inventario [politica_abastecimiento.md > Cómo se arma la lista de compra]."
    )


@tool
def historial_producto(producto: str, centro: str, relevos: int = 6) -> str:
    """Cuánto se pidió de un producto en los últimos relevos de un centro y si la lista era copiada."""
    cid, sku = _centro_id(centro), _sku(producto)
    hist = cargar_historial()
    hist = hist[hist["centro"] == cid]
    fechas = sorted(hist["fecha_relevo"].unique())[-relevos:]
    lineas = []
    for fecha in fechas:
        relevo = hist[hist["fecha_relevo"] == fecha]
        compra = relevo.loc[relevo["sku"] == sku, "cantidad"].sum()
        copiada = " (lista copiada)" if relevo["lista_copiada"].iloc[0] else ""
        lineas.append(f"{pd.Timestamp(fecha):%Y-%m-%d}: {compra:g}{copiada}")
    return f"{cargar_catalogo().loc[sku, 'producto']} en {centro}: " + "; ".join(lineas)


@tool
def buscar_politica(pregunta: str) -> str:
    """Busca en la política de abastecimiento y el protocolo de relevo. Devuelve fragmentos con su cita."""
    return formatear_contexto(buscar_politicas(pregunta, k=3))


@tool
def recordar_relevos(centro: str, consulta: str, runtime: ToolRuntime) -> str:
    """Memoria de largo plazo: relevos anteriores del centro parecidos a la consulta (decisiones, rechazos)."""
    if runtime.store is None:
        return "La memoria de largo plazo no está disponible en esta ejecución."
    recuerdos = recordar(runtime.store, _centro_id(centro), consulta)
    return "\n".join(recuerdos) or "No hay relevos anteriores guardados para este centro."


# --- Razonamiento sobre datos ---


@tool
def calcular_orden(centro: str, fecha: str) -> str:
    """Orden de compra recomendada para un relevo (AAAA-MM-DD) a partir del inventario del turno saliente."""
    cid = _centro_id(centro)
    orden = armar_orden(cid, inventario_en_relevo(cid, fecha))
    productos = ", ".join(f"{i['producto']} x{i['cantidad']}" for i in orden["items"][:12])
    return (
        f"Total {pesos(orden['total_clp'])} de {pesos(orden['presupuesto_clp'])}; "
        f"{len(orden['items'])} productos, {orden['peso_kg']:.1f} kg. Principales: {productos}. "
        f"Decisiones: {' '.join(orden['decisiones'])} Alertas: {' '.join(orden['alertas']) or 'ninguna'}"
    )


HERRAMIENTAS_ANALISTA = [
    consultar_centro, consultar_inventario, revisar_producto, historial_producto,
    buscar_politica, recordar_relevos, calcular_orden,
]


# --- Escritura ---


@dataclass
class ContextoCorreos:
    """Lo fija el grafo, no el modelo: dónde están los adjuntos del relevo y qué
    alertas tiene que recibir sí o sí el Responsable de compras."""

    carpeta: str
    alertas: list[str] = field(default_factory=list)
    reciben_alertas: list[str] = field(default_factory=list)


def cuerpo_final(args: dict, contexto: ContextoCorreos | None = None) -> str:
    """El cuerpo que redactó el modelo más las alertas del sistema, agregadas por código.

    Una lista copiada o un sobregasto no pueden depender de que el modelo se acuerde de
    mencionarlos: se agregan siempre al correo de quien tiene que recibirlos.
    """
    cuerpo = args.get("cuerpo", "")
    if contexto and contexto.alertas and set(args.get("para", [])) & set(contexto.reciben_alertas):
        cuerpo += "\n\nAlertas del sistema:\n" + "\n".join(f"- {a}" for a in contexto.alertas)
    return cuerpo


def describir_correo(args: dict, contexto: ContextoCorreos | None = None) -> str:
    """Lo que ve el revisor humano antes de aprobar un correo: exactamente lo que se enviará."""
    return (
        f"Para: {', '.join(args.get('para', []))}\n"
        f"Asunto: {args.get('asunto', '')}\n"
        f"Adjuntos: {', '.join(args.get('adjuntos', [])) or 'ninguno'}\n\n"
        f"{cuerpo_final(args, contexto)}"
    )


@tool
def enviar_correo(
    para: list[str], asunto: str, cuerpo: str, adjuntos: list[str], runtime: ToolRuntime[ContextoCorreos]
) -> str:
    """Envía un correo del relevo. `adjuntos` son nombres de archivo del relevo (por ejemplo 'orden_compra.xlsx').
    Una persona aprueba, edita o rechaza el correo antes de que salga."""
    carpeta = Path(runtime.context.carpeta)
    rutas = [carpeta / Path(nombre).name for nombre in adjuntos]  # .name: solo archivos del relevo
    cuerpo = cuerpo_final({"para": para, "cuerpo": cuerpo}, runtime.context)
    try:
        registro = correo.enviar(para, asunto, cuerpo, rutas, carpeta)
    except ValueError as error:
        return json.dumps({"estado": "rechazado_por_validacion", "detalle": str(error)}, ensure_ascii=False)
    return json.dumps(registro, ensure_ascii=False)
