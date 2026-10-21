"""Envío de correos con la API de Gmail (OAuth de escritorio) o a la bandeja local.

Modo (variable MODO_CORREO en .env):
- outbox (por defecto): el correo se guarda como .eml en outbox/<centro>_<fecha>/
  y no sale nada. Sirve para probar el agente sin credenciales de Google.
- gmail: además de guardarlo, se envía con la API de Gmail usando gmail_token.json
  (se crea una vez con `uv run python -m scripts.autenticar_gmail`).

Mínimo privilegio:
- El permiso que se pide a Google es solo gmail.send: el agente no puede leer el buzón.
- Solo se aceptan destinatarios de data/contactos.csv. Si el LLM inventa una dirección
  (o un texto malicioso se la sugiere), el envío se rechaza.
- Los contactos del caso son ficticios (@pampasur.example). En modo gmail se redirigen
  a CORREO_REDIRIGIR_A, y el asunto indica a quién iba dirigido el correo.
"""

import base64
import mimetypes
import os
import re
import threading
from email.message import EmailMessage
from pathlib import Path

from src.config import GMAIL_CREDENCIALES, GMAIL_TOKEN, modo_correo
from src.datos import cargar_contactos

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
DOMINIO_FICTICIO = ".example"
_CANDADO_EML = threading.Lock()


def destinatarios_validos() -> set[str]:
    return set(cargar_contactos()["email"].str.lower())


def construir_mensaje(para: list[str], asunto: str, cuerpo: str, adjuntos: list[Path]) -> EmailMessage:
    mensaje = EmailMessage()
    mensaje["To"] = ", ".join(para)
    mensaje["Subject"] = asunto
    mensaje.set_content(cuerpo)
    for ruta in adjuntos:
        tipo, _ = mimetypes.guess_type(ruta.name)
        principal, secundario = (tipo or "application/octet-stream").split("/")
        mensaje.add_attachment(ruta.read_bytes(), maintype=principal, subtype=secundario, filename=ruta.name)
    return mensaje


def _redirigir(para: list[str], asunto: str) -> tuple[list[str], str]:
    """Cambia los destinatarios ficticios por CORREO_REDIRIGIR_A (solo modo gmail)."""
    if not any(d.endswith(DOMINIO_FICTICIO) for d in para):
        return para, asunto
    destino = os.getenv("CORREO_REDIRIGIR_A", "").strip()
    if not destino:
        raise RuntimeError(
            "Los contactos son ficticios (@pampasur.example): define CORREO_REDIRIGIR_A en .env "
            "con tu correo para recibir las pruebas."
        )
    return [destino], f"[PRUEBA para {', '.join(para)}] {asunto}"


def servicio_gmail():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not GMAIL_TOKEN.exists():
        raise RuntimeError("Falta gmail_token.json. Corre una vez: uv run python -m scripts.autenticar_gmail")
    credenciales = Credentials.from_authorized_user_file(str(GMAIL_TOKEN), SCOPES)
    if not credenciales.valid:
        if not credenciales.refresh_token:
            raise RuntimeError("El token de Gmail venció. Corre de nuevo: uv run python -m scripts.autenticar_gmail")
        credenciales.refresh(Request())
        GMAIL_TOKEN.write_text(credenciales.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=credenciales, cache_discovery=False)


def autenticar() -> Path:
    """Login de Google en el navegador (una sola vez); guarda el token para los envíos."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not GMAIL_CREDENCIALES.exists():
        raise RuntimeError(
            f"Falta {GMAIL_CREDENCIALES.name}: descárgalo de Google Cloud Console "
            "(cliente OAuth tipo 'App de escritorio') y déjalo en la raíz del proyecto."
        )
    flujo = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CREDENCIALES), SCOPES)
    credenciales = flujo.run_local_server(port=0)
    GMAIL_TOKEN.write_text(credenciales.to_json(), encoding="utf-8")
    return GMAIL_TOKEN


def _guardar_eml(carpeta: Path, asunto: str, mensaje: EmailMessage) -> Path:
    # El agente envía varios correos en paralelo: sin el candado, dos quedarían con el mismo número.
    with _CANDADO_EML:
        numero = len(list(carpeta.glob("*.eml"))) + 1
        slug = re.sub(r"[^a-z0-9]+", "_", asunto.lower())[:50].strip("_")
        eml = carpeta / f"{numero:02d}_{slug}.eml"
        eml.write_bytes(bytes(mensaje))
    return eml


def enviar(para: list[str], asunto: str, cuerpo: str, adjuntos: list[Path], carpeta: Path) -> dict:
    """Guarda el correo en la carpeta del relevo y, en modo gmail, lo envía.

    Devuelve un registro con el estado: "enviado" (salió por Gmail), "guardado"
    (solo outbox) o "error" (el .eml queda guardado igual, para reintentar).
    """
    para = [d.strip().lower() for d in para if d.strip()]
    desconocidos = sorted(set(para) - destinatarios_validos())
    if not para or desconocidos:
        raise ValueError(f"Destinatarios no autorizados: {desconocidos or 'ninguno'}. Usa solo data/contactos.csv.")
    faltan = [r.name for r in adjuntos if not r.exists()]
    if faltan:
        raise ValueError(f"No existen los adjuntos: {faltan}")

    mensaje = construir_mensaje(para, asunto, cuerpo, adjuntos)
    eml = _guardar_eml(carpeta, asunto, mensaje)
    registro = {"para": para, "asunto": asunto, "adjuntos": [r.name for r in adjuntos], "eml": eml.name}

    if modo_correo() != "gmail":
        return {**registro, "estado": "guardado", "id": None}
    try:
        reales, asunto_real = _redirigir(para, asunto)
        del mensaje["To"], mensaje["Subject"]
        mensaje["To"], mensaje["Subject"] = ", ".join(reales), asunto_real
        crudo = base64.urlsafe_b64encode(bytes(mensaje)).decode()
        enviado = servicio_gmail().users().messages().send(userId="me", body={"raw": crudo}).execute()
    except Exception as error:  # el correo queda en outbox para reenviarlo a mano
        return {**registro, "estado": "error", "id": None, "detalle": f"{type(error).__name__}: {error}"}
    return {**registro, "estado": "enviado", "id": enviado["id"], "enviado_a": reales}
