"""Memoria del agente (EP2, IE3 e IE4).

Corto plazo: checkpointer SQLite de LangGraph. Cada relevo es un hilo
(thread_id = "<centro>-<fecha>") y su estado completo se guarda en cada paso,
incluida la pausa para aprobar los correos: se puede cerrar el programa y retomar
la aprobación después, sin repetir el análisis ni gastar tokens otra vez.

Largo plazo (semántica): SqliteStore de LangGraph con índice vectorial (sqlite-vec)
y los mismos embeddings locales del RAG. Guarda:
- ("relevos", <centro>): un resumen de cada relevo cerrado (qué se compró, qué se
  evitó, qué correos se aprobaron o rechazaron);
- ("preferencias", "correos"): los comentarios del revisor al rechazar un correo,
  para que el agente de correos los aplique en los relevos siguientes.
Se recupera por similitud al preparar el relevo siguiente y desde la herramienta
`recordar_relevos` del agente Analista.
"""

import sqlite3
from datetime import datetime

from langgraph.store.base import BaseStore

from src.config import MEMORIA_DIR

NS_PREFERENCIAS = ("preferencias", "correos")


def ns_relevos(centro_id: str) -> tuple[str, str]:
    return ("relevos", centro_id)


def _conectar(nombre: str) -> sqlite3.Connection:
    MEMORIA_DIR.mkdir(exist_ok=True)
    return sqlite3.connect(MEMORIA_DIR / nombre, check_same_thread=False, isolation_level=None)


def checkpointer():
    from langgraph.checkpoint.sqlite import SqliteSaver

    saver = SqliteSaver(_conectar("checkpoints.sqlite"))
    saver.setup()
    return saver


def store() -> BaseStore:
    from langgraph.store.sqlite import SqliteStore

    from src.rag import _embeddings

    embeddings = _embeddings()
    almacen = SqliteStore(
        _conectar("memoria_largo_plazo.sqlite"),
        index={"dims": len(embeddings.embed_query("dimensión")), "embed": embeddings, "fields": ["texto"]},
    )
    almacen.setup()
    return almacen


def recordar(almacen: BaseStore, centro_id: str, consulta: str, k: int = 3) -> list[str]:
    """Relevos anteriores del centro más parecidos a la consulta, del más al menos similar."""
    return [f"[{r.key}] {r.value['texto']}" for r in almacen.search(ns_relevos(centro_id), query=consulta, limit=k)]


def preferencias(almacen: BaseStore, consulta: str, k: int = 3) -> list[str]:
    return [r.value["texto"] for r in almacen.search(NS_PREFERENCIAS, query=consulta, limit=k)]


def guardar_relevo(almacen: BaseStore, centro_id: str, fecha: str, texto: str, datos: dict) -> None:
    almacen.put(ns_relevos(centro_id), fecha, {"texto": texto, **datos})


def guardar_preferencia(almacen: BaseStore, texto: str, centro_id: str) -> None:
    clave = datetime.now().strftime("%Y%m%d%H%M%S%f")
    almacen.put(NS_PREFERENCIAS, clave, {"texto": texto, "centro": centro_id})
