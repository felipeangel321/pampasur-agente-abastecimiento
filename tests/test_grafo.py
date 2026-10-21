"""Grafo del relevo de punta a punta con un modelo falso (sin Groq, sin tokens).

El modelo falso entrega respuestas en orden: primero la del Analista y después las
del agente de Correos. Así se prueban la aprobación humana, el rechazo con
comentario, la memoria entre relevos y el plan B, de forma repetible.
"""

import email
from email import policy

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from src import grafo
from src.config import DATA_DIR

ORDEN = ["compras@pampasur.example"]
CARGA = ["cuidador.tarao.b1@pampasur.example", "cuidador.tarao.b2@pampasur.example"]


class ModeloFalso(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _envio(id_llamada, para, asunto, adjuntos=("orden_compra.xlsx",)):
    args = {"para": para, "asunto": asunto, "cuerpo": "Hola, va la orden.", "adjuntos": list(adjuntos)}
    return {"name": "enviar_correo", "args": args, "id": id_llamada}


def _app(respuestas, store=None):
    llm = ModeloFalso(messages=iter(respuestas))
    return grafo.construir_grafo(llm=llm, checkpointer=InMemorySaver(), store=store)


def _store():
    # Embedding de juguete (3 dimensiones) para no cargar el modelo de la EP1.
    def embed(textos):
        return [[float("rechaz" in t.lower()), float("tarao" in t.lower()), 1.0] for t in textos]

    return InMemoryStore(index={"dims": 3, "embed": embed, "fields": ["texto"]})


@pytest.fixture(autouse=True)
def outbox_temporal(tmp_path, monkeypatch):
    monkeypatch.setattr("src.reportes.OUTBOX_DIR", tmp_path)
    monkeypatch.setenv("MODO_CORREO", "outbox")


ENTRADA = {"centro": "tarao", "fecha": "2026-10-09", "texto_lista": None}


def test_relevo_completo_con_aprobacion(tmp_path):
    store = _store()
    app = _app(
        [
            AIMessage(content="Análisis: se evitó comprar detergente."),
            AIMessage(content="", tool_calls=[
                _envio("c1", ORDEN, "Orden Tarao", ["orden_compra.xlsx", "reporte_relevo.pdf"]),
                _envio("c2", CARGA, "Qué llevan en el bus"),
            ]),
            AIMessage(content="Listo: 2 correos."),
        ],
        store,
    )
    config = {"configurable": {"thread_id": "t1"}}
    resultado = app.invoke(ENTRADA, config, version="v2")

    # Se detiene antes de enviar: el revisor ve los dos correos.
    solicitudes = resultado.interrupts[0].value["action_requests"]
    assert [s["args"]["para"] for s in solicitudes] == [ORDEN, CARGA]
    assert not list(tmp_path.rglob("*.eml"))  # nada salió todavía

    resultado = app.invoke(Command(resume={"decisions": [{"type": "approve"}, {"type": "approve"}]}), config, version="v2")
    final = resultado.value
    assert [c["estado"] for c in final["correos"]] == ["guardado", "guardado"]
    carpeta = tmp_path / "tarao_2026-10-09"
    assert {p.name for p in carpeta.iterdir()} >= {"orden_compra.xlsx", "reporte_relevo.pdf"}
    assert len(list(carpeta.glob("*.eml"))) == 2
    assert "Correos aprobados: 2; rechazados: 0" in final["resumen"]
    # Memoria de largo plazo: el relevo queda guardado para el siguiente.
    assert store.get(("relevos", "tarao"), "2026-10-09").value["texto"] == final["resumen"]


def test_lista_copiada_llega_al_responsable_aunque_el_modelo_no_la_mencione(tmp_path, monkeypatch):
    monkeypatch.setattr("src.rag.hay_groq", lambda: False)
    responsable, prevencion = ["compras@pampasur.example"], ["prevencion@pampasur.example"]
    app = _app(
        [
            AIMessage(content="Análisis."),
            AIMessage(content="", tool_calls=[_envio("c1", responsable, "Orden"), _envio("c2", prevencion, "Entrega")]),
            AIMessage(content="Listo."),
        ]
    )
    config = {"configurable": {"thread_id": "t6"}}
    texto = (DATA_DIR / "listas_whatsapp" / "maqui_2026-10-15.txt").read_text(encoding="utf-8")
    resultado = app.invoke({"centro": "maqui", "fecha": "2026-10-15", "texto_lista": texto}, config, version="v2")

    orden, entrega = resultado.interrupts[0].value["action_requests"]
    assert "Alertas del sistema" in orden["description"] and "Lista copiada" in orden["description"]
    assert "Alertas del sistema" not in entrega["description"]  # solo al Responsable

    app.invoke(Command(resume={"decisions": [{"type": "approve"}, {"type": "approve"}]}), config, version="v2")
    emls = sorted((tmp_path / "maqui_2026-10-15").glob("*.eml"))
    assert [e.name[:3] for e in emls] == ["01_", "02_"]  # numeración sin choques aunque se envíen en paralelo
    mensajes = [email.message_from_bytes(e.read_bytes(), policy=policy.default) for e in emls]
    al_responsable = next(m for m in mensajes if m["To"] == responsable[0])
    cuerpo = al_responsable.get_body(("plain",)).get_content()
    assert "Lista copiada: 39 de 39 productos" in cuerpo  # lo aprobado es lo que se envía


def test_rechazo_con_comentario_el_agente_corrige_y_se_recuerda():
    store = _store()
    app = _app(
        [
            AIMessage(content="Análisis."),
            AIMessage(content="", tool_calls=[_envio("c1", ORDEN, "Orden"), _envio("c2", CARGA, "Bus")]),
            AIMessage(content="", tool_calls=[_envio("c3", CARGA, "Bus: 19,8 kg por persona")]),
            AIMessage(content="Listo."),
        ],
        store,
    )
    config = {"configurable": {"thread_id": "t2"}}
    app.invoke(ENTRADA, config, version="v2")
    comentario = "Pon los kilos por persona en el asunto"
    resultado = app.invoke(
        Command(resume={"decisions": [{"type": "approve"}, {"type": "reject", "message": comentario}]}),
        config, version="v2",
    )
    # El agente reescribió el correo rechazado y vuelve a pedir aprobación.
    assert resultado.interrupts[0].value["action_requests"][0]["args"]["asunto"] == "Bus: 19,8 kg por persona"
    final = app.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config, version="v2").value
    assert sorted(c["estado"] for c in final["correos"]) == ["guardado", "guardado", "rechazado"]
    assert final["comentarios_revisor"] == [comentario]

    # En el relevo siguiente, el comentario llega como preferencia al agente de Correos.
    siguiente = _app([AIMessage(content="Análisis.")], store)
    estado = siguiente.invoke({**ENTRADA, "fecha": "2026-10-23"}, {"configurable": {"thread_id": "t3"}}, version="v2")
    valores = siguiente.get_state({"configurable": {"thread_id": "t3"}}).values
    assert comentario in valores["preferencias"]
    assert valores["recuerdos"] and "2026-10-09" in valores["recuerdos"][0]
    assert estado.interrupts  # el relevo siguiente también espera aprobación


def test_si_el_agente_no_envia_se_reintenta_y_luego_plan_b():
    app = _app(
        [
            AIMessage(content="Análisis."),
            AIMessage(content="Ya redacté los correos."),  # intento 1: no llamó a la herramienta
            AIMessage(content="Listo."),  # intento 2: tampoco
        ]
    )
    config = {"configurable": {"thread_id": "t4"}}
    resultado = app.invoke(ENTRADA, config, version="v2")
    borradores = resultado.interrupts[0].value["action_requests"]
    assert [b["args"]["para"] for b in borradores] == [ORDEN, CARGA]  # plantillas para los dos

    final = app.invoke(Command(resume={"decisions": [{"type": "approve"}, {"type": "reject"}]}), config, version="v2").value
    assert [c["estado"] for c in final["correos"]] == ["guardado", "rechazado"]
    assert any("correos (intento 2)" in b for b in final["bitacora"])
    assert any("plan B" in b for b in final["bitacora"])


def test_sin_llm_el_relevo_igual_se_procesa(monkeypatch):
    monkeypatch.setattr(grafo, "hay_groq", lambda: False)
    monkeypatch.setattr("src.rag.hay_groq", lambda: False)
    app = grafo.construir_grafo(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t5"}}
    lista = "Lista Tarao\ndetergente x2\nazucar 5 kilos\n2 six de cerveza"
    resultado = app.invoke({**ENTRADA, "texto_lista": lista}, config, version="v2")
    valores = app.get_state(config).values
    assert valores["lineas_revisar"] == ["2 six de cerveza"]
    assert valores["analisis"].startswith("La orden suma")
    assert len(resultado.interrupts[0].value["action_requests"]) == 2
