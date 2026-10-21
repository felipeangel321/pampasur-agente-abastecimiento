"""Pipeline RAG. Los tests sin marca usan solo embeddings locales; los 'llm' llaman a Groq."""

import pandas as pd
import pytest

from src.config import hay_groq
from src.rag import (
    buscar_politicas,
    buscar_productos,
    es_coincidencia_confiable,
    interpretar_linea,
    normalizar_lista,
    responder,
)


@pytest.mark.parametrize(
    "texto, sku",
    [
        ("confort", "A007"),
        ("nescafe", "L018"),
        ("toalla nova", "A008"),
        ("quix", "A002"),
        ("detergente", "A003"),
        ("azucar", "L005"),
        ("asucar", "L005"),  # error de tipeo
        ("hallulla", "P001"),
        ("tallarines", "L002"),
        ("pan molde", "P002"),
        ("nylon orilladora", "M004"),
        ("arroz", "L001"),
        ("cloro", "A001"),
    ],
)
def test_jerga_de_whatsapp_llega_al_producto_correcto(texto, sku):
    candidatos = buscar_productos(texto, k=5)
    assert candidatos[0][0].metadata["sku"] == sku
    assert es_coincidencia_confiable(candidatos)


@pytest.mark.parametrize(
    "texto",
    [
        "cerveza",  # fuera del catálogo
        "cigarros",  # fuera del catálogo
        "algo pa lavar la loza",  # descripción: la decide el LLM
        "papel",  # ambiguo: higiénico o toalla
    ],
)
def test_casos_dudosos_no_se_aceptan_sin_llm(texto):
    assert not es_coincidencia_confiable(buscar_productos(texto, k=5))


@pytest.mark.parametrize(
    "pregunta, cita_esperada",
    [
        ("¿Cuál es el presupuesto por trabajador por turno?", "politica_abastecimiento.md"),
        ("¿Quién lleva los víveres a Hornopirén?", "Porcelana"),
        ("¿Cuántos turnos dura el detergente para ropa?", "duracion_productos.csv > A003"),
        ("¿Quién aprueba una ampliación de presupuesto?", "Ampliación"),
    ],
)
def test_recupera_el_documento_correcto(pregunta, cita_esperada):
    citas = [d.metadata["cita"] for d in buscar_politicas(pregunta, k=4)]
    assert any(cita_esperada in c for c in citas), citas


def test_pregunta_compuesta_trae_producto_y_centro():
    citas = [d.metadata["cita"] for d in buscar_politicas("¿Cuánto dura el detergente y cuánto puede gastar Tarao?")]
    assert "duracion_productos.csv > A003" in citas
    assert "centros.csv > Centro Tarao" in citas


def test_reconoce_centro_por_localidad():
    citas = [d.metadata["cita"] for d in buscar_politicas("¿Cómo llegan los víveres a Chiloé?")]
    assert "centros.csv > Centro Tarao" in citas


@pytest.mark.parametrize(
    "linea, texto, cantidad, unidad",
    [
        ("3 kg pan", "pan", 3, "kg"),
        ("cloro x2", "cloro", 2, None),
        ("- azucar 5 kilos", "azucar", 5, "kilos"),
        ("papas 4 kg", "papas", 4, "kg"),
        ("confort", "confort", 1, None),
    ],
)
def test_interpreta_lineas(linea, texto, cantidad, unidad):
    info = interpretar_linea(linea)
    assert (info["texto"], info["cantidad_texto"], info["unidad_texto"]) == (texto, cantidad, unidad)


def test_normaliza_lista_sin_llm():
    texto = "Lista Maqui relevo 01/10\n3 kg pan\npapas 4 kg\nconfort x2\n1 six cerveza\n"
    tabla = normalizar_lista(texto, usar_llm=False)
    reconocidos = tabla.dropna(subset=["sku"])
    assert reconocidos.set_index("sku")["cantidad"].to_dict() == {"P001": 3, "P010": 2, "A007": 2}
    assert tabla.loc[tabla["sku"].isna(), "metodo"].tolist() == ["revisar"]


@pytest.mark.parametrize(
    "linea, sku, paquetes",
    [
        ("40 rollos de confort", "A007", 4),  # paquetes de 12 rollos
        ("confort x2", "A007", 2),  # sin "rollos": ya son paquetes
        ("200 bolsitas de te", "L019", 2),  # Té 100 bolsitas
    ],
)
def test_convierte_unidades_sueltas_a_paquetes(linea, sku, paquetes):
    fila = normalizar_lista(linea, usar_llm=False).iloc[0]
    assert (fila["sku"], fila["cantidad"]) == (sku, paquetes)


@pytest.mark.llm
@pytest.mark.skipif(not hay_groq(), reason="falta GROQ_API_KEY")
def test_respuesta_cita_sus_fuentes():
    resultado = responder("¿Cuál es el presupuesto del Centro Tarao por relevo?")
    assert "360.000" in resultado["respuesta"]
    assert "[" in resultado["respuesta"]  # cita entre corchetes


@pytest.mark.llm
@pytest.mark.skipif(not hay_groq(), reason="falta GROQ_API_KEY")
def test_llm_resuelve_descripciones_y_rechaza_no_autorizados():
    tabla = normalizar_lista("algo pa lavar la loza\n2 six de cerveza", usar_llm=True)
    assert tabla["sku"].tolist()[0] == "A002"
    assert tabla["metodo"].tolist()[0] == "llm"
    assert pd.isna(tabla["sku"].tolist()[1])
