"""Grafo del relevo (EP2): orquesta el RAG, el Comprador y los agentes Analista y Correos.

Sin agente manager. El orden del proceso es conocido y está en el código; las
rutas condicionales deciden con datos del estado, no con un LLM:

  preparar ─┬─(llegó lista)──► interpretar ─┐
            └─(sin lista)───────────────────┴─► comprar ─► analizar ─► documentos
                                                                           │
                      ┌──────────────(hay LLM)─────────────────────────────┤
                      ▼                                                    │(sin LLM)
                   correos ──(faltan correos y queda intento)──► correos   ▼
                      │──(faltan y no quedan intentos)──► correos_plantilla
                      └──(completo)──► registrar ◄──────────────────┘

- preparar, comprar, documentos, registrar: deterministas (sin tokens).
- interpretar: RAG híbrido de la EP1; el LLM solo resuelve las líneas dudosas.
- analizar: agente Analista (consulta + razonamiento). Si falla, resumen sin LLM.
- correos: agente Correos (escritura). Cada envío espera aprobación humana.
- correos_plantilla: plan B sin LLM para lo que el agente no alcanzó a enviar.

Uso:
  uv run python -m src.grafo --centro tarao --fecha 2026-10-09 --lista data/listas_whatsapp/tarao_2026-10-09.txt
"""

import json
import operator
import sys
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from src import correo, memoria
from src.agentes import crear_agente_correos, crear_analista
from src.analisis import inventario_en_relevo, skus_relevo_anterior
from src.compra import armar_orden, correos_requeridos, pesos
from src.config import hay_groq
from src.datos import cargar_centros
from src.herramientas import ContextoCorreos, describir_correo
from src.rag import normalizar_lista
from src.reportes import ORDEN_XLSX, REPORTE_PDF, carpeta_relevo, orden_excel, reporte_pdf

MAX_INTENTOS_CORREO = 2
ESTADOS_LISTOS = ("guardado", "enviado")


class EstadoRelevo(TypedDict, total=False):
    # Entrada
    centro: str
    fecha: str  # AAAA-MM-DD
    texto_lista: str | None
    # preparar
    inventario: dict[str, float]
    sku_anteriores: list[str]
    recuerdos: list[str]
    preferencias: list[str]
    # interpretar
    lista_pedida: dict[str, int]
    lineas_revisar: list[str]
    # comprar
    orden: dict
    # analizar
    analisis: str
    # documentos
    carpeta: str
    correos_requeridos: list[dict]
    # correos
    correos: Annotated[list[dict], operator.add]
    comentarios_revisor: Annotated[list[str], operator.add]
    intentos_correo: int
    # registrar
    resumen: str
    # Qué hizo cada nodo y por qué (evidencia de las decisiones, IE6)
    bitacora: Annotated[list[str], operator.add]


# --- Nodos deterministas ---


def preparar(state: EstadoRelevo, runtime: Runtime) -> dict:
    centro, fecha = state["centro"], state["fecha"]
    if centro not in cargar_centros().index:
        raise ValueError(f"Centro desconocido: {centro}")
    inventario = inventario_en_relevo(centro, fecha)
    recuerdos, prefs = [], []
    if runtime.store is not None:
        recuerdos = memoria.recordar(runtime.store, centro, "compras innecesarias, ampliaciones y correos rechazados")
        prefs = memoria.preferencias(runtime.store, f"correos del relevo de {centro}")
    return {
        "inventario": inventario,
        "sku_anteriores": sorted(skus_relevo_anterior(centro, fecha)),
        "recuerdos": recuerdos,
        "preferencias": prefs,
        "intentos_correo": 0,
        "bitacora": [
            f"preparar: inventario del turno saliente con {len(inventario)} productos; "
            f"{len(recuerdos)} relevos anteriores y {len(prefs)} preferencias recuperados de la memoria."
        ],
    }


def ruta_lista(state: EstadoRelevo) -> Literal["interpretar", "comprar"]:
    return "interpretar" if state.get("texto_lista") else "comprar"


def interpretar(state: EstadoRelevo) -> dict:
    tabla = normalizar_lista(state["texto_lista"])
    validos = tabla.dropna(subset=["sku"])
    lista = {sku: int(c) for sku, c in validos.groupby("sku")["cantidad"].sum().items()}
    revisar = tabla.loc[tabla["sku"].isna(), "linea"].tolist()
    con_llm = int((tabla["metodo"] == "llm").sum())
    return {
        "lista_pedida": lista,
        "lineas_revisar": revisar,
        "bitacora": [
            f"interpretar: {len(tabla)} líneas de WhatsApp → {len(lista)} productos del catálogo "
            f"({con_llm} resueltas por el LLM); {len(revisar)} sin producto autorizado: {', '.join(revisar) or '-'}."
        ],
    }


def comprar(state: EstadoRelevo) -> dict:
    orden = armar_orden(
        state["centro"], state["inventario"], state.get("lista_pedida"), state["sku_anteriores"], state["fecha"]
    )
    if state.get("lineas_revisar"):
        orden["alertas"].append(
            f"Líneas de la lista que no se compran (fuera del catálogo autorizado o no reconocidas): "
            f"{', '.join(state['lineas_revisar'])}."
        )
    return {
        "orden": orden,
        "bitacora": [f"comprar: {d}" for d in orden["decisiones"]] + [f"comprar: ⚠ {a}" for a in orden["alertas"]],
    }


def documentos(state: EstadoRelevo) -> dict:
    carpeta = carpeta_relevo(state["centro"], state["fecha"])
    orden_excel(state["orden"], state["fecha"], carpeta)
    reporte_pdf(state["orden"], state["fecha"], state.get("analisis", ""), carpeta)
    requeridos = correos_requeridos(state["orden"], state["fecha"])
    return {
        "carpeta": str(carpeta),
        "correos_requeridos": requeridos,
        "bitacora": [
            f"documentos: {ORDEN_XLSX} y {REPORTE_PDF} en {carpeta.name}/; correos a enviar: "
            + "; ".join(f"{r['rol']} ({r['proposito']})" for r in requeridos)
        ],
    }


def registrar(state: EstadoRelevo, runtime: Runtime) -> dict:
    """Cierra el relevo: lo resume y lo guarda en la memoria de largo plazo."""
    orden = state["orden"]
    listos = [c for c in state.get("correos", []) if c["estado"] in ESTADOS_LISTOS]
    rechazados = [c for c in state.get("correos", []) if c["estado"] == "rechazado"]
    mayores = sorted(orden["observaciones"], key=lambda o: -o["exceso_clp"])[:3]
    texto = (
        f"Relevo {state['fecha']} de {orden['nombre_centro']}: orden de {pesos(orden['total_clp'])} "
        f"(presupuesto {pesos(orden['presupuesto_clp'])}). Compras innecesarias evitadas: "
        f"{pesos(orden['exceso_detectado_clp'])}"
        + (f", sobre todo {', '.join(o['producto'] for o in mayores)}" if mayores else "")
        + ". "
        + (f"Ampliación solicitada: {pesos(orden['ampliacion']['monto_clp'])}. " if orden["ampliacion"] else "")
        + (f"Alertas: {' '.join(orden['alertas'])} " if orden["alertas"] else "")
        + f"Correos aprobados: {len(listos)}; rechazados: {len(rechazados)}."
        + (f" Comentarios del revisor: {' | '.join(state['comentarios_revisor'])}" if state.get("comentarios_revisor") else "")
    )
    if runtime.store is not None:
        memoria.guardar_relevo(
            runtime.store, state["centro"], state["fecha"], texto,
            {"total_clp": orden["total_clp"], "exceso_clp": orden["exceso_detectado_clp"]},
        )
        for comentario in state.get("comentarios_revisor", []):
            memoria.guardar_preferencia(runtime.store, comentario, state["centro"])
    return {"resumen": texto, "bitacora": ["registrar: relevo guardado en la memoria de largo plazo."]}


# --- Tareas de los agentes (lo que ve el LLM) ---


def _datos_orden(state: EstadoRelevo) -> str:
    o = state["orden"]
    lineas = [
        f"Relevo del {o['nombre_centro']} (id {o['centro']}) del {state['fecha']}. {o['personas']} cuidador(es), "
        f"traslado en {'bus' if o['traslado'] == 'bus' else 'vehículo del prevencionista'}.",
        f"Orden calculada: {pesos(o['total_clp'])} de {pesos(o['presupuesto_clp'])} de presupuesto; "
        f"{len(o['items'])} productos; {o['peso_kg']:.1f} kg en total.",
    ]
    if o["lista_pedida_clp"]:
        lineas.append(
            f"La lista de WhatsApp sumaba {pesos(o['lista_pedida_clp'])}. "
            f"Compras innecesarias evitadas: {pesos(o['exceso_detectado_clp'])}."
        )
    mayores = sorted(o["observaciones"], key=lambda x: -x["exceso_clp"])[:5]
    if mayores:
        lineas.append("Pedidos de más con mayor monto:")
        lineas += [
            f"- {x['producto']}: pidió {x['pedida']:g}, inventario {x['inventario']:g}, recomendado "
            f"{x['recomendada']:g}, exceso {pesos(x['exceso_clp'])} ({x['motivo']})"
            for x in mayores
        ]
    if a := o["ampliacion"]:
        lineas.append(
            f"Ampliación necesaria: {pesos(a['monto_clp'])} ({a['porcentaje']}% del presupuesto); "
            f"justificación detallada: {'sí' if a['justificacion_detallada'] else 'no'}. Ítems que la provocan: "
            + "; ".join(f"{i['producto']} {pesos(i['subtotal_clp'])} ({i['motivo']})" for i in a["items"])
        )
    if o["carga_max_kg"] is not None:
        aparte = [i["producto"] for i in o["items"] if i["transporte"] == "envío aparte"]
        lineas.append(
            f"En el bus van {o['peso_en_bus_kg']:.1f} kg (máximo {o['carga_max_kg']} kg)"
            + (f"; en envío aparte: {', '.join(aparte)}." if aparte else ".")
        )
    lineas += [f"Decisión del sistema: {d}" for d in o["decisiones"]]
    lineas += [f"Alerta: {x}" for x in o["alertas"]]
    return "\n".join(lineas)


def _tarea_analista(state: EstadoRelevo) -> str:
    recuerdos = "\n".join(f"- {r}" for r in state.get("recuerdos", [])) or "- (sin relevos anteriores guardados)"
    return (
        f"{_datos_orden(state)}\n\nMemoria de relevos anteriores del centro:\n{recuerdos}\n\n"
        "Escribe el análisis de este relevo para el Responsable de compras."
    )


def _resumen_sin_llm(state: EstadoRelevo) -> str:
    o = state["orden"]
    mayores = sorted(o["observaciones"], key=lambda x: -x["exceso_clp"])[:3]
    texto = f"La orden suma {pesos(o['total_clp'])} de un presupuesto de {pesos(o['presupuesto_clp'])}."
    if mayores:
        texto += f" Se evitaron {pesos(o['exceso_detectado_clp'])} en compras innecesarias, principalmente: " + "; ".join(
            f"{x['producto']} ({x['motivo']})" for x in mayores
        ) + "."
    if o["ampliacion"]:
        texto += f" Se solicita una ampliación de {pesos(o['ampliacion']['monto_clp'])}."
    return texto + "".join(f" {a}" for a in o["alertas"])


def _detalle_correo(req: dict, o: dict) -> str:
    """Los datos que necesita cada correo, separados para que el agente no los mezcle."""
    if req["id"] == "carga":
        en_bus = sorted((i for i in o["items"] if i["transporte"] == "con el turno"), key=lambda i: -i["peso_kg"])
        aparte = [i["producto"] for i in o["items"] if i["transporte"] == "envío aparte"]
        return (
            f"LLEVAN en el bus {len(en_bus)} productos, {o['peso_en_bus_kg']:.1f} kg "
            f"({o['peso_en_bus_kg'] / o['personas']:.1f} kg por persona). Los más pesados que llevan: "
            + ", ".join(f"{i['producto']} ({i['peso_kg']:g} kg)" for i in en_bus[:5])
            + (f". NO llevan (van en envío aparte): {', '.join(aparte)}." if aparte else ".")
        )
    if req["id"] == "entrega":
        return f"{len(o['items'])} productos, {o['peso_kg']:.1f} kg en total."
    aparte = [i["producto"] for i in o["items"] if i["transporte"] == "envío aparte"]
    return (
        f"Total {pesos(o['total_clp'])} de {pesos(o['presupuesto_clp'])}; compras innecesarias evitadas "
        f"{pesos(o['exceso_detectado_clp'])}. Ampliación: "
        + (f"{pesos(o['ampliacion']['monto_clp'])} ({o['ampliacion']['porcentaje']}%), pedir aprobación."
           if o["ampliacion"] else "no se necesita, no pidas aprobación de presupuesto.")
        + (f" Coordinar el envío aparte de: {', '.join(aparte)}." if aparte else "")
        + (" Las alertas del sistema se agregan solas al final de este correo: no las repitas." if o["alertas"] else "")
    )


def _tarea_correos(state: EstadoRelevo, pendientes: list[dict]) -> str:
    o = state["orden"]
    lista = "\n".join(
        f"{n}. Para: {', '.join(r['para'])} ({r['rol']}). Propósito: {r['proposito']}. "
        f"Adjuntos: {', '.join(r['adjuntos'])}.\n   Datos para este correo: {_detalle_correo(r, o)}"
        for n, r in enumerate(pendientes, 1)
    )
    prefs = "\n".join(f"- {p}" for p in state.get("preferencias", [])) or "- (ninguna)"
    reintento = ""
    if state.get("intentos_correo"):
        reintento = "\nATENCIÓN: en el intento anterior estos correos no se enviaron. Envíalos ahora con enviar_correo.\n"
    return (
        f"{_datos_orden(state)}\n\n"
        f"Análisis del Analista:\n{state.get('analisis', '')}\n\n"
        f"Preferencias del revisor en relevos anteriores:\n{prefs}\n{reintento}\n"
        f"Correos a enviar:\n{lista}"
    )


def _pendientes(state: EstadoRelevo) -> list[dict]:
    """Correos requeridos que todavía no salieron ni fueron descartados por el revisor."""
    resueltos = set()
    for c in state.get("correos", []):
        if c["estado"] in ESTADOS_LISTOS or (c["estado"] == "rechazado" and not c.get("comentario")):
            resueltos |= set(c["para"])
    return [r for r in state.get("correos_requeridos", []) if not set(r["para"]) <= resueltos]


def _leer_correos(mensajes: list) -> tuple[list[dict], list[str]]:
    """Registros de envío y comentarios del revisor a partir de los mensajes del agente."""
    llamadas = {
        tc["id"]: tc["args"] for m in mensajes if isinstance(m, AIMessage) for tc in (m.tool_calls or [])
    }
    registros, comentarios = [], []
    for m in mensajes:
        if not isinstance(m, ToolMessage) or m.name != "enviar_correo":
            continue
        args = llamadas.get(m.tool_call_id, {})
        if m.status == "error" and m.content.startswith("User rejected"):
            # HumanInTheLoopMiddleware: "User rejected the tool call ... with reason: <comentario>"
            comentario = m.content.split("with reason:", 1)[1].strip() if "with reason:" in m.content else ""
            registros.append(
                {"para": [d.lower() for d in args.get("para", [])], "asunto": args.get("asunto", ""),
                 "estado": "rechazado", "comentario": comentario}
            )
            comentarios += [comentario] if comentario else []
            continue
        # Si el revisor editó el correo, el middleware antepone un aviso al JSON del registro.
        try:
            registros.append(json.loads(m.content[m.content.find("{"):]))
        except ValueError:
            registros.append(
                {"para": args.get("para", []), "asunto": args.get("asunto", ""), "estado": "error", "detalle": m.content}
            )
    return registros, comentarios


def _plantilla(req: dict, state: EstadoRelevo) -> dict:
    o = state["orden"]
    lineas = ["Hola,", ""]
    if req["id"] == "orden":
        lineas.append(
            f"Adjunto la orden de compra del relevo del {o['nombre_centro']} del {state['fecha']}: "
            f"{pesos(o['total_clp'])} de un presupuesto de {pesos(o['presupuesto_clp'])}."
        )
        if o["exceso_detectado_clp"]:
            lineas.append(f"Se evitaron {pesos(o['exceso_detectado_clp'])} en compras innecesarias de la lista de WhatsApp.")
        if a := o["ampliacion"]:
            lineas.append(
                f"Se solicita aprobar una ampliación de {pesos(a['monto_clp'])} ({a['porcentaje']}%) por: "
                + "; ".join(f"{i['producto']} ({i['motivo']})" for i in a["items"]) + ". Responda este correo para aprobarla."
            )
        lineas += [f"Atención: {a}" for a in o["alertas"]]
        lineas.append("El detalle y las observaciones están en los adjuntos.")
    elif req["id"] == "carga":
        lineas.append(f"Para el relevo del {state['fecha']} llevan en el bus unos {o['peso_en_bus_kg'] / o['personas']:.1f} kg por persona.")
        lineas.append("La lista completa va en el Excel adjunto (columna Transporte: 'con el turno').")
    else:
        lineas.append(f"El relevo del {o['nombre_centro']} es el {state['fecha']}. La compra pesa {o['peso_kg']:.1f} kg; la orden va adjunta.")
    lineas += ["", "Agente de abastecimiento Pampasur"]
    return {
        "para": req["para"],
        "asunto": f"[Pampasur] {req['proposito']} - {o['nombre_centro']} {state['fecha']}",
        "cuerpo": "\n".join(lineas),
        "adjuntos": req["adjuntos"],
    }


# --- Grafo ---


def construir_grafo(llm=None, checkpointer=None, store=None):
    """llm=None usa Groq si hay GROQ_API_KEY; sin clave, el grafo corre con el plan B sin LLM."""
    usar_llm = llm is not None or hay_groq()

    def analizar(state: EstadoRelevo) -> dict:
        if not usar_llm:
            return {"analisis": _resumen_sin_llm(state), "bitacora": ["analizar: sin LLM, resumen determinista."]}
        try:
            resultado = crear_analista(llm).invoke({"messages": [{"role": "user", "content": _tarea_analista(state)}]})
            texto = (resultado["messages"][-1].content or "").strip()
            herramientas = [m.name for m in resultado["messages"] if isinstance(m, ToolMessage)]
        except GraphBubbleUp:
            raise
        except Exception as error:  # cuota de Groq agotada, error de red: el relevo no se detiene
            return {
                "analisis": _resumen_sin_llm(state),
                "bitacora": [f"analizar: el Analista falló ({type(error).__name__}); se usa el resumen determinista."],
            }
        if not texto or "limits exceeded" in texto:
            return {"analisis": _resumen_sin_llm(state), "bitacora": ["analizar: el Analista no terminó; resumen determinista."]}
        return {
            "analisis": texto,
            "bitacora": [f"analizar: el Analista usó {len(herramientas)} herramientas ({', '.join(herramientas) or 'ninguna'})."],
        }

    def correos(state: EstadoRelevo) -> dict:
        pendientes = _pendientes(state)
        intento = state.get("intentos_correo", 0) + 1
        try:
            responsable = next(r["para"] for r in state["correos_requeridos"] if r["id"] == "orden")
            resultado = crear_agente_correos(llm).invoke(
                {"messages": [{"role": "user", "content": _tarea_correos(state, pendientes)}]},
                context=ContextoCorreos(
                    carpeta=state["carpeta"], alertas=state["orden"]["alertas"], reciben_alertas=responsable
                ),
            )
        except GraphBubbleUp:
            raise  # la pausa para aprobar no es un error
        except Exception as error:
            return {"intentos_correo": MAX_INTENTOS_CORREO, "bitacora": [f"correos: el agente falló ({type(error).__name__})."]}
        registros, comentarios = _leer_correos(resultado["messages"])
        resumen = ", ".join(f"{', '.join(r['para'])}: {r['estado']}" for r in registros) or "ningún envío"
        return {
            "correos": registros,
            "comentarios_revisor": comentarios,
            "intentos_correo": intento,
            "bitacora": [f"correos (intento {intento}): {resumen}."],
        }

    def ruta_correos(state: EstadoRelevo) -> Literal["correos", "correos_plantilla", "registrar"]:
        if not _pendientes(state):
            return "registrar"
        if usar_llm and state.get("intentos_correo", 0) < MAX_INTENTOS_CORREO:
            return "correos"
        return "correos_plantilla"

    def correos_plantilla(state: EstadoRelevo) -> dict:
        """Plan B sin LLM: plantillas fijas, con la misma aprobación humana que el agente."""
        borradores = [_plantilla(r, state) for r in _pendientes(state)]
        respuesta = interrupt(
            {
                "action_requests": [
                    {"name": "enviar_correo", "args": b, "description": describir_correo(b)} for b in borradores
                ],
                "review_configs": [{"action_name": "enviar_correo", "allowed_decisions": ["approve", "edit", "reject"]}],
            }
        )
        carpeta, registros, comentarios = Path(state["carpeta"]), [], []
        for borrador, decision in zip(borradores, respuesta["decisions"]):
            if decision["type"] == "reject":
                registros.append({"para": borrador["para"], "asunto": borrador["asunto"], "estado": "rechazado",
                                  "comentario": decision.get("message", "")})
                comentarios += [decision["message"]] if decision.get("message") else []
                continue
            args = decision["edited_action"]["args"] if decision["type"] == "edit" else borrador
            registros.append(
                correo.enviar(args["para"], args["asunto"], args["cuerpo"], [carpeta / a for a in args["adjuntos"]], carpeta)
            )
        return {
            "correos": registros,
            "comentarios_revisor": comentarios,
            "bitacora": [f"correos_plantilla: {len(borradores)} correos con plantilla (plan B sin LLM)."],
        }

    g = StateGraph(EstadoRelevo)
    g.add_node("preparar", preparar)
    g.add_node("interpretar", interpretar)
    g.add_node("comprar", comprar)
    g.add_node("analizar", analizar)
    g.add_node("documentos", documentos)
    g.add_node("correos", correos)
    g.add_node("correos_plantilla", correos_plantilla)
    g.add_node("registrar", registrar)

    g.add_edge(START, "preparar")
    g.add_conditional_edges("preparar", ruta_lista, ["interpretar", "comprar"])
    g.add_edge("interpretar", "comprar")
    g.add_edge("comprar", "analizar")
    g.add_edge("analizar", "documentos")
    g.add_conditional_edges("documentos", ruta_correos, ["correos", "correos_plantilla", "registrar"])
    g.add_conditional_edges("correos", ruta_correos, ["correos", "correos_plantilla", "registrar"])
    g.add_edge("correos_plantilla", "registrar")
    g.add_edge("registrar", END)
    return g.compile(checkpointer=checkpointer, store=store)


# --- CLI: corre un relevo y pide la aprobación de los correos en la terminal ---


def _revisar(accion: dict) -> dict | None:
    """Pregunta al revisor por un correo. None = salir y retomar después."""
    print("\n" + "─" * 70 + f"\n{accion['description']}\n" + "─" * 70)
    while True:
        opcion = input("[a]probar  [r]echazar con comentario  [d]escartar  [s]alir y retomar después: ").strip().lower()
        if opcion == "a":
            return {"type": "approve"}
        if opcion == "r":
            comentario = input("Comentario para el agente (qué corregir): ").strip()
            return {"type": "reject", "message": comentario or "Corrige el correo."}
        if opcion == "d":
            return {"type": "reject"}
        if opcion == "s":
            return None


def _correr(app, entrada, config) -> object | None:
    """Ejecuta hasta el final o hasta la próxima pausa; muestra la bitácora de cada nodo."""
    pausa = None
    for paso in app.stream(entrada, config, stream_mode="updates"):
        for nodo, cambios in paso.items():
            if nodo == "__interrupt__":
                pausa = cambios[0]
            else:
                for linea in (cambios or {}).get("bitacora", []):
                    print(f"  · {linea}")
    return pausa


def _main() -> None:
    import argparse
    import uuid

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Agente de abastecimiento Pampasur: procesa un relevo")
    parser.add_argument("--centro", required=True, help="id del centro: maqui, chaiquen, penasmo, porcelana, tarao")
    parser.add_argument("--fecha", required=True, help="fecha del relevo, AAAA-MM-DD")
    parser.add_argument("--lista", type=Path, help="archivo .txt con la lista de WhatsApp (opcional)")
    parser.add_argument("--nuevo", action="store_true", help="procesar de nuevo un relevo ya cerrado (otro hilo)")
    args = parser.parse_args()

    app = construir_grafo(checkpointer=memoria.checkpointer(), store=memoria.store())
    hilo = f"{args.centro}-{args.fecha}" + (f"-{uuid.uuid4().hex[:6]}" if args.nuevo else "")
    config = {"configurable": {"thread_id": hilo}}
    estado = app.get_state(config)

    if estado.interrupts:
        print(f"Hilo {hilo}: hay correos esperando aprobación de una ejecución anterior. Se retoma.")
        pausa = estado.interrupts[0]
    elif estado.values and not estado.next:
        print(f"El relevo {hilo} ya se procesó:\n{estado.values.get('resumen', '')}\nUsa --nuevo para repetirlo.")
        return
    else:
        print(f"Procesando relevo {hilo} (modo de correo: {correo.modo_correo()})")
        entrada = {"centro": args.centro, "fecha": args.fecha,
                   "texto_lista": args.lista.read_text(encoding="utf-8") if args.lista else None}
        pausa = _correr(app, entrada, config)

    while pausa is not None:
        decisiones = []
        for accion in pausa.value["action_requests"]:
            decision = _revisar(accion)
            if decision is None:
                print(f"\nPausado. Para retomar: uv run python -m src.grafo --centro {args.centro} --fecha {args.fecha}")
                return
            decisiones.append(decision)
        pausa = _correr(app, Command(resume={"decisions": decisiones}), config)

    final = app.get_state(config).values
    print(f"\n{final['resumen']}\n\nAnálisis:\n{final['analisis']}\n\nArchivos en {final['carpeta']}")


if __name__ == "__main__":
    _main()
