"""Configuración central: rutas, constantes del negocio y clientes de LLM/embeddings."""

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ / ".env")

DATA_DIR = RAIZ / "data"
INDICES_DIR = RAIZ / ".indices"
OUTBOX_DIR = RAIZ / "outbox"
MEMORIA_DIR = RAIZ / ".memoria"  # checkpoints y memoria de largo plazo (SQLite)

# --- Reglas del negocio (ver data/internos/politica_abastecimiento.md) ---
DIAS_TURNO = 14
PRESUPUESTO_POR_TRABAJADOR = 180_000
TOLERANCIA_EXCESO = 1.2  # se acepta comprar hasta un 20% sobre la necesidad
UMBRAL_CANTIDAD_EXCESIVA = 1.5  # pedir más de 1,5× la necesidad debe justificarse
UMBRAL_AMPLIACION_DETALLADA = 0.2  # (supuesto) sobre 20% la ampliación se justifica en detalle
UMBRAL_LISTA_COPIADA = 0.9  # 90% de la lista igual al relevo anterior (calibrado en el historial)
CARGA_MAX_KG_POR_PERSONA = 20  # (supuesto) lo que lleva cada cuidador en el bus

# --- Correo (ver src/correo.py) ---
GMAIL_CREDENCIALES = RAIZ / "credentials.json"  # cliente OAuth de escritorio (Google Cloud)
GMAIL_TOKEN = RAIZ / "gmail_token.json"  # lo crea scripts/autenticar_gmail.py

# --- Modelos ---
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_MODEL_TOOLS = os.getenv("GROQ_MODEL_TOOLS", "openai/gpt-oss-20b")
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)


def hay_groq() -> bool:
    return bool(os.getenv("GROQ_API_KEY", "").strip())


def modo_correo() -> str:
    """outbox: solo guarda los .eml en outbox/; gmail: además los envía con la API de Gmail."""
    return os.getenv("MODO_CORREO", "outbox").strip().lower()


def get_llm(modelo: str | None = None, temperature: float = 0):
    from langchain_groq import ChatGroq

    return ChatGroq(model=modelo or GROQ_MODEL, temperature=temperature, reasoning_effort="low")


def get_embeddings():
    from langchain_huggingface import HuggingFaceEmbeddings

    # Vectores normalizados: el producto interno es la similitud coseno (0 a 1).
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL, encode_kwargs={"normalize_embeddings": True}
    )
