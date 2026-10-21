"""Adjuntos del relevo: orden de compra en Excel y reporte en PDF (EP2).

Se generan sin LLM a partir de la orden de src/compra.py; el análisis que escribe
el agente Analista se agrega al PDF como texto.
"""

import unicodedata
from pathlib import Path

import pandas as pd
from fpdf import FPDF

from src.compra import pesos
from src.config import OUTBOX_DIR

ORDEN_XLSX = "orden_compra.xlsx"
REPORTE_PDF = "reporte_relevo.pdf"


def carpeta_relevo(centro_id: str, fecha: str) -> Path:
    carpeta = OUTBOX_DIR / f"{centro_id}_{fecha}"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _copia(orden: dict) -> str:
    copia = orden.get("lista_copiada")
    if not copia:
        return "no" if orden["lista_pedida_clp"] else "sin lista"
    return f"sí: {copia['porcentaje']:g}% igual al {copia['fecha_anterior']}"


def orden_excel(orden: dict, fecha: str, carpeta: Path) -> Path:
    """Hojas: Orden (lo que se compra), Observaciones (lo pedido de más) y Resumen."""
    ruta = carpeta / ORDEN_XLSX
    items = pd.DataFrame(orden["items"]).rename(
        columns={
            "sku": "SKU", "producto": "Producto", "categoria": "Categoría", "prioridad": "Prioridad",
            "cantidad": "Cantidad", "precio_clp": "Precio unitario", "subtotal_clp": "Subtotal",
            "peso_kg": "Peso (kg)", "transporte": "Transporte", "motivo": "Motivo",
        }
    )
    resumen = pd.DataFrame(
        [
            ("Centro", orden["nombre_centro"]),
            ("Fecha del relevo", fecha),
            ("Cuidadores por turno", orden["personas"]),
            ("Presupuesto", orden["presupuesto_clp"]),
            ("Total de la orden", orden["total_clp"]),
            ("Total de la lista de WhatsApp", orden["lista_pedida_clp"] or "sin lista"),
            ("Lista copiada del relevo anterior", _copia(orden)),
            ("Compras innecesarias detectadas", orden["exceso_detectado_clp"]),
            ("Ampliación solicitada", orden["ampliacion"]["monto_clp"] if orden["ampliacion"] else 0),
            ("Peso total (kg)", orden["peso_kg"]),
        ],
        columns=["Dato", "Valor"],
    )
    with pd.ExcelWriter(ruta, engine="openpyxl") as excel:
        items.to_excel(excel, sheet_name="Orden", index=False)
        pd.DataFrame(orden["observaciones"] or [{"motivo": "sin observaciones"}]).to_excel(
            excel, sheet_name="Observaciones", index=False
        )
        resumen.to_excel(excel, sheet_name="Resumen", index=False)
        for hoja in excel.sheets.values():
            for columna in hoja.columns:
                ancho = max(len(str(c.value or "")) for c in columna)
                hoja.column_dimensions[columna[0].column_letter].width = min(ancho + 2, 60)
    return ruta


# Las fuentes base de PDF solo traen latin-1: se reemplaza lo que el LLM o el texto
# puedan traer fuera de ese rango (guiones y espacios especiales, ≈, viñetas, emojis).
_REEMPLAZOS = {"≈": "~", "×": "x", "…": "...", "•": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "→": "->"}


def _latin1(texto: str) -> str:
    texto = "".join(
        " " if unicodedata.category(c) == "Zs" else "-" if unicodedata.category(c) == "Pd" else _REEMPLAZOS.get(c, c)
        for c in texto
    )
    return texto.encode("latin-1", "replace").decode("latin-1")


def reporte_pdf(orden: dict, fecha: str, analisis: str, carpeta: Path) -> Path:
    ruta = carpeta / REPORTE_PDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def titulo(texto: str, tamano: int = 12) -> None:
        pdf.set_font("Helvetica", "B", tamano)
        pdf.multi_cell(0, 8, _latin1(texto), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)

    def parrafo(texto: str) -> None:
        pdf.multi_cell(0, 5, _latin1(texto), new_x="LMARGIN", new_y="NEXT")

    titulo(f"Reporte de abastecimiento - {orden['nombre_centro']} - relevo {fecha}", 14)
    parrafo(
        f"{orden['personas']} cuidador(es) por turno. Traslado: "
        f"{'bus' if orden['traslado'] == 'bus' else 'vehículo del prevencionista'}."
    )
    pdf.ln(2)

    titulo("Resumen")
    filas = [
        ("Presupuesto del relevo", pesos(orden["presupuesto_clp"])),
        ("Total de la orden", pesos(orden["total_clp"])),
        ("Lista de WhatsApp", pesos(orden["lista_pedida_clp"]) if orden["lista_pedida_clp"] else "no llegó"),
        ("Lista copiada del relevo anterior", _copia(orden)),
        ("Compras innecesarias evitadas", pesos(orden["exceso_detectado_clp"])),
        ("Ampliación solicitada", pesos(orden["ampliacion"]["monto_clp"]) if orden["ampliacion"] else "no"),
        ("Peso total", f"{orden['peso_kg']:.1f} kg"),
    ]
    with pdf.table(col_widths=(60, 40), text_align=("LEFT", "RIGHT"), first_row_as_headings=False) as tabla:
        for dato, valor in filas:
            fila = tabla.row()
            fila.cell(_latin1(dato))
            fila.cell(_latin1(valor))
    pdf.ln(3)

    titulo("Decisiones del sistema")
    for decision in orden["decisiones"]:
        parrafo(f"- {decision}")
    for alerta in orden["alertas"]:
        parrafo(f"- ALERTA: {alerta}")
    pdf.ln(2)

    if analisis:
        titulo("Análisis del agente")
        parrafo(analisis)
        pdf.ln(2)

    if orden["observaciones"]:
        titulo("Productos pedidos de más en la lista")
        with pdf.table(col_widths=(55, 15, 20, 110), text_align=("LEFT", "RIGHT", "RIGHT", "LEFT")) as tabla:
            encabezado = tabla.row()
            for columna in ("Producto", "Pedido", "Exceso", "Motivo"):
                encabezado.cell(columna)
            for o in sorted(orden["observaciones"], key=lambda o: -o["exceso_clp"]):
                fila = tabla.row()
                fila.cell(_latin1(o["producto"]))
                fila.cell(f"{o['pedida']:g}")
                fila.cell(_latin1(pesos(o["exceso_clp"])))
                fila.cell(_latin1(o["motivo"]))

    pdf.output(str(ruta))
    return ruta
