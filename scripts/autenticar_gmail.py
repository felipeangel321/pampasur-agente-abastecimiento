"""Autoriza al agente a enviar correos con tu cuenta de Gmail (se corre una sola vez).

Antes: deja credentials.json (cliente OAuth "App de escritorio" de Google Cloud) en
la raíz del proyecto. Los pasos están en docs/arquitectura_agente.md > Gmail.

Abre el navegador para el login de Google y guarda gmail_token.json. Con --probar
envía además un correo de prueba a esa dirección.

Uso:
  uv run python -m scripts.autenticar_gmail
  uv run python -m scripts.autenticar_gmail --probar tu_correo@gmail.com
"""

import argparse
import base64
import sys
from email.message import EmailMessage

from src.correo import autenticar, servicio_gmail


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--probar", metavar="CORREO", help="envía un correo de prueba a esta dirección")
    args = parser.parse_args()

    print(f"Token guardado en {autenticar().name}")
    if args.probar:
        mensaje = EmailMessage()
        mensaje["To"], mensaje["Subject"] = args.probar, "[Pampasur] Prueba del agente de correos"
        mensaje.set_content("Si llega este correo, el agente ya puede enviar por la API de Gmail.")
        crudo = base64.urlsafe_b64encode(bytes(mensaje)).decode()
        enviado = servicio_gmail().users().messages().send(userId="me", body={"raw": crudo}).execute()
        print(f"Correo de prueba enviado (id {enviado['id']})")


if __name__ == "__main__":
    main()
