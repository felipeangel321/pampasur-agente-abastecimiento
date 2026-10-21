"""Correo: bandeja local, validación de destinatarios, redirección y adjuntos (sin red)."""

import email
from email import policy

import pytest

from src import correo
from src.compra import armar_orden
from src.reportes import orden_excel, reporte_pdf


@pytest.fixture
def adjunto(tmp_path):
    ruta = tmp_path / "orden_compra.xlsx"
    ruta.write_bytes(b"excel")
    return ruta


def test_modo_outbox_guarda_el_eml_con_adjunto(tmp_path, adjunto, monkeypatch):
    monkeypatch.setenv("MODO_CORREO", "outbox")
    registro = correo.enviar(["compras@pampasur.example"], "Orden Maqui", "Hola", [adjunto], tmp_path)
    assert registro["estado"] == "guardado"
    mensaje = email.message_from_bytes((tmp_path / registro["eml"]).read_bytes(), policy=policy.default)
    assert mensaje["To"] == "compras@pampasur.example"
    assert [p.get_filename() for p in mensaje.iter_attachments()] == ["orden_compra.xlsx"]


def test_rechaza_destinatarios_fuera_de_contactos(tmp_path, adjunto):
    with pytest.raises(ValueError, match="no autorizados"):
        correo.enviar(["atacante@example.com"], "x", "y", [adjunto], tmp_path)


def test_modo_gmail_redirige_contactos_ficticios(tmp_path, adjunto, monkeypatch):
    enviados = []

    class ServicioFalso:
        def users(self):
            return self

        def messages(self):
            return self

        def send(self, userId, body):
            enviados.append(body["raw"])
            return self

        def execute(self):
            return {"id": "abc123"}

    monkeypatch.setenv("MODO_CORREO", "gmail")
    monkeypatch.setenv("CORREO_REDIRIGIR_A", "felipe@prueba.cl")
    monkeypatch.setattr(correo, "servicio_gmail", ServicioFalso)
    registro = correo.enviar(["compras@pampasur.example"], "Orden Maqui", "Hola", [adjunto], tmp_path)
    assert registro["estado"] == "enviado" and registro["id"] == "abc123"
    assert registro["enviado_a"] == ["felipe@prueba.cl"]
    assert len(enviados) == 1


def test_modo_gmail_sin_redireccion_no_envia_a_contactos_ficticios(tmp_path, adjunto, monkeypatch):
    monkeypatch.setenv("MODO_CORREO", "gmail")
    monkeypatch.setenv("CORREO_REDIRIGIR_A", "")
    registro = correo.enviar(["compras@pampasur.example"], "Orden", "Hola", [adjunto], tmp_path)
    assert registro["estado"] == "error" and "CORREO_REDIRIGIR_A" in registro["detalle"]
    assert (tmp_path / registro["eml"]).exists()  # queda en outbox para reenviarlo


def test_reportes_excel_y_pdf(tmp_path):
    orden = armar_orden("maqui", lista_pedida={"M005": 8, "L005": 5})
    excel = orden_excel(orden, "2026-10-15", tmp_path)
    pdf = reporte_pdf(orden, "2026-10-15", "Análisis — 2026‑10‑09, 60 kg ≈ fuera de latin-1 ✓", tmp_path)
    import pandas as pd

    hojas = pd.read_excel(excel, sheet_name=None)
    assert set(hojas) == {"Orden", "Observaciones", "Resumen"}
    assert len(hojas["Orden"]) == len(orden["items"])
    assert pdf.read_bytes().startswith(b"%PDF")
