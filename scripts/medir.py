"""Mide llamadas al LLM, tokens y segundos de relevos representativos (EP2, IE9).

Corre el grafo completo con Groq, aprueba todos los correos sin preguntar y usa
memoria y bandeja temporales: no toca .memoria/ ni outbox/ ni envía correos.

Uso:
  uv run python -m scripts.medir
"""

import os
import sys
import tempfile
import time
from contextvars import ContextVar
from pathlib import Path

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.tracers.context import register_configure_hook
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

import src.reportes
from src.config import DATA_DIR, GROQ_MODEL_TOOLS
from src.grafo import _correr, construir_grafo
from src.rag import _embeddings

CASOS = [
    ("tarao", "2026-10-09", "tarao_2026-10-09.txt", "bus, lista copiada, carga sobre el máximo"),
    ("maqui", "2026-10-15", "maqui_2026-10-15.txt", "vehículo, lista con productos de larga duración"),
    ("penasmo", "2026-10-07", None, "sin lista de WhatsApp: solo inventario"),
]


class Medidor(UsageMetadataCallbackHandler):
    """Suma tokens (como get_usage_metadata_callback) y además cuenta las llamadas al modelo."""

    def __init__(self):
        super().__init__()
        self.llamadas = 0

    def on_llm_end(self, response, **kwargs):
        self.llamadas += 1
        super().on_llm_end(response, **kwargs)


# Registrado como hook: también mide las llamadas de los agentes que corren dentro de los nodos.
_medidor: ContextVar[Medidor | None] = ContextVar("medidor_pampasur", default=None)
register_configure_hook(_medidor, inheritable=True)


def medir(centro: str, fecha: str, archivo: str | None) -> dict:
    app = construir_grafo(
        checkpointer=InMemorySaver(),
        store=InMemoryStore(index={"dims": 384, "embed": _embeddings(), "fields": ["texto"]}),
    )
    config = {"configurable": {"thread_id": f"medicion-{centro}-{fecha}"}}
    texto = (DATA_DIR / "listas_whatsapp" / archivo).read_text(encoding="utf-8") if archivo else None
    medidor = Medidor()
    marca = _medidor.set(medidor)
    inicio = time.perf_counter()
    try:
        pausa = _correr(app, {"centro": centro, "fecha": fecha, "texto_lista": texto}, config)
        pausas = 0
        while pausa is not None:
            pausas += 1
            aprobar = [{"type": "approve"} for _ in pausa.value["action_requests"]]
            pausa = _correr(app, Command(resume={"decisions": aprobar}), config)
    finally:
        _medidor.reset(marca)
    segundos = time.perf_counter() - inicio
    estado = app.get_state(config).values
    totales = list(medidor.usage_metadata.values())
    return {
        "llamadas": medidor.llamadas,
        "tokens_entrada": sum(u["input_tokens"] for u in totales),
        "tokens_salida": sum(u["output_tokens"] for u in totales),
        "segundos": segundos,
        "pausas": pausas,
        "correos": len([c for c in estado["correos"] if c["estado"] in ("guardado", "enviado")]),
        "bitacora": estado["bitacora"],
    }


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ["MODO_CORREO"] = "outbox"
    src.reportes.OUTBOX_DIR = Path(tempfile.mkdtemp(prefix="pampasur_medicion_"))
    print(f"Modelo de los agentes: {GROQ_MODEL_TOOLS}\n")
    print("| Caso | Descripción | Llamadas LLM | Tokens entrada | Tokens salida | Segundos | Pausas | Correos |")
    print("|---|---|---|---|---|---|---|---|")
    for centro, fecha, archivo, descripcion in CASOS:
        r = medir(centro, fecha, archivo)
        print(
            f"| {centro} {fecha} | {descripcion} | {r['llamadas']} | {_miles(r['tokens_entrada'])} | "
            f"{_miles(r['tokens_salida'])} | {r['segundos']:.1f} | {r['pausas']} | {r['correos']} |"
        )


if __name__ == "__main__":
    main()
