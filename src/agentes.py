"""Agentes LLM de la EP2: Analista y Correos (create_agent de LangChain 1.x).

No hay un agente "manager" que decida a quién delegar: el orden del relevo es
conocido (inventario → orden → análisis → adjuntos → correos) y vive en el grafo
(src/grafo.py), en código. Cada agente recibe una tarea acotada y solo las
herramientas de su rol:

- Analista: consulta y razonamiento. Explica la orden que calculó el Comprador
  (src/compra.py), la fundamenta con la política (RAG) y la memoria de relevos.
- Correos: escritura. Redacta y envía los correos con adjuntos por Gmail. Cada
  envío pasa por HumanInTheLoopMiddleware: una persona lo aprueba, lo edita o lo
  rechaza con un comentario (y el agente lo corrige) antes de que salga.

Uso del Analista como chat, con memoria de corto plazo entre preguntas:
  uv run python -m src.agentes
"""

import sys
import uuid

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ModelCallLimitMiddleware, ModelRetryMiddleware

from src.config import GROQ_MODEL_TOOLS, get_llm
from src.herramientas import HERRAMIENTAS_ANALISTA, ContextoCorreos, describir_correo, enviar_correo
from src.prompts import SISTEMA_ANALISTA, SISTEMA_CORREOS

# Topes por ejecución (contrato de iteraciones, IL2.4): Groq gratuito limita tokens por minuto.
MAX_LLAMADAS_ANALISTA = 6
MAX_LLAMADAS_CORREOS = 8


def _llm():
    return get_llm(GROQ_MODEL_TOOLS)


def _descripcion_correo(tool_call, state, runtime) -> str:
    return describir_correo(tool_call["args"], runtime.context)


def crear_analista(llm=None, checkpointer=None, store=None):
    return create_agent(
        llm or _llm(),
        tools=HERRAMIENTAS_ANALISTA,
        system_prompt=SISTEMA_ANALISTA,
        middleware=[
            ModelCallLimitMiddleware(run_limit=MAX_LLAMADAS_ANALISTA, exit_behavior="end"),
            # Groq devuelve a veces 429 (cuota por minuto) o 400 tool_use_failed: se reintenta.
            ModelRetryMiddleware(max_retries=2, on_failure="error"),
        ],
        checkpointer=checkpointer,
        store=store,
        name="analista",
    )


def crear_agente_correos(llm=None):
    return create_agent(
        llm or _llm(),
        tools=[enviar_correo],
        system_prompt=SISTEMA_CORREOS,
        context_schema=ContextoCorreos,
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "enviar_correo": {
                        "allowed_decisions": ["approve", "edit", "reject"],
                        "description": _descripcion_correo,
                    }
                }
            ),
            ModelCallLimitMiddleware(run_limit=MAX_LLAMADAS_CORREOS, exit_behavior="end"),
            ModelRetryMiddleware(max_retries=2, on_failure="error"),
        ],
        name="correos",
    )


def _chat() -> None:
    """Conversación con el Analista. El thread_id une las preguntas (memoria de corto plazo)."""
    from src.memoria import checkpointer, store

    sys.stdout.reconfigure(encoding="utf-8")
    analista = crear_analista(checkpointer=checkpointer(), store=store())
    config = {"configurable": {"thread_id": f"chat-{uuid.uuid4().hex[:8]}"}}
    print("Analista de abastecimiento Pampasur. Escribe 'salir' para terminar.\n")
    while (pregunta := input("Tú: ").strip()).lower() not in ("salir", "exit", ""):
        try:
            resultado = analista.invoke({"messages": [{"role": "user", "content": pregunta}]}, config)
        except Exception as error:  # 429 de cuota o 400 tool_use_failed de Groq tras los reintentos
            print(f"\n(No pude responder: {type(error).__name__}. Reformula la pregunta o espera un minuto.)\n")
            continue
        print(f"\nAnalista: {resultado['messages'][-1].content}\n")


if __name__ == "__main__":
    _chat()
