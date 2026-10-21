"""Comprador (determinista): presupuesto, ampliación, carga del bus y a quién se informa."""

from src.analisis import inventario_en_relevo
from src.compra import armar_orden, correos_requeridos, equipo_entrante
from src.config import DATA_DIR
from src.rag import normalizar_lista


def _skus(orden):
    return {i["sku"] for i in orden["items"]}


def test_la_orden_sale_del_inventario_y_no_de_la_lista():
    inventario = inventario_en_relevo("tarao", "2026-10-09")
    orden = armar_orden("tarao", inventario, {"A003": 2, "L005": 5}, ["A003"])
    assert "A003" not in _skus(orden)  # el detergente que queda alcanza para el turno
    detergente = next(o for o in orden["observaciones"] if o["producto"].startswith("Detergente"))
    assert "ya cubre el turno" in detergente["motivo"]
    assert orden["exceso_detectado_clp"] > 0


def test_detecta_la_lista_copiada_del_relevo_anterior():
    # La lista de Maqui del 15-10 es la misma del 01-10, copiada del chat.
    texto = (DATA_DIR / "listas_whatsapp" / "maqui_2026-10-15.txt").read_text(encoding="utf-8")
    tabla = normalizar_lista(texto, usar_llm=False).dropna(subset=["sku"])
    lista = tabla.groupby("sku")["cantidad"].sum().to_dict()
    orden = armar_orden("maqui", inventario_en_relevo("maqui", "2026-10-15"), lista, fecha="2026-10-15")
    assert orden["lista_copiada"]["fecha_anterior"] == "2026-10-01"
    assert orden["lista_copiada"]["porcentaje"] == 100
    assert any(a.startswith("Lista copiada") for a in orden["alertas"])


def test_una_lista_armada_de_nuevo_no_se_marca_como_copiada():
    orden = armar_orden("maqui", inventario_en_relevo("maqui", "2026-10-15"), {"L001": 1, "A007": 1}, fecha="2026-10-15")
    assert orden["lista_copiada"] is None


def test_avisa_sobregasto_de_la_lista_y_corrige_la_orden():
    orden = armar_orden("maqui", inventario_en_relevo("maqui", "2026-10-15"), {"A007": 40}, fecha="2026-10-15")
    assert orden["lista_pedida_clp"] > orden["presupuesto_clp"]
    assert any(a.startswith("Sobregasto") for a in orden["alertas"])
    assert orden["total_clp"] <= orden["presupuesto_clp"]  # la orden sale de la necesidad, no de la lista
    assert orden["ampliacion"] is None


def test_sin_inventario_se_compra_la_necesidad_completa_con_alerta():
    orden = armar_orden("maqui")
    assert orden["items"]
    assert any("no reportó inventario" in a for a in orden["alertas"])


def test_mantenimiento_que_supera_el_presupuesto_pide_ampliacion():
    # 8 galones de pintura ($119.920) más la compra del turno superan los $180.000 de Maqui.
    orden = armar_orden("maqui", lista_pedida={"M005": 8})
    assert orden["total_clp"] > orden["presupuesto_clp"]
    ampliacion = orden["ampliacion"]
    assert ampliacion["monto_clp"] == orden["total_clp"] - orden["presupuesto_clp"]
    assert ampliacion["items"][0]["producto"].startswith("Pintura")  # el mantenimiento la provoca
    assert ampliacion["justificacion_detallada"] == (ampliacion["porcentaje"] > 20)


def test_lo_opcional_se_recorta_antes_de_pedir_ampliacion():
    orden = armar_orden("maqui", lista_pedida={"M005": 8, "L025": 3})  # L025: agua en bidón, opcional
    assert "L025" not in _skus(orden)
    assert any(r["motivo"] == "opcional recortado por presupuesto" for r in orden["recortes"])
    assert all(i["prioridad"] != "opcional" for i in orden["items"])


def test_mantenimiento_repetido_de_una_lista_copiada_no_se_compra():
    orden = armar_orden("maqui", lista_pedida={"M002": 2}, sku_anteriores={"M002"})
    assert "M002" not in _skus(orden)
    assert "confirmar si el trabajo continúa" in orden["recortes"][0]["motivo"]


def test_en_bus_lo_no_esencial_mas_pesado_va_aparte():
    orden = armar_orden("tarao", inventario_en_relevo("tarao", "2026-10-09"))
    assert orden["carga_max_kg"] == 40
    aparte = [i for i in orden["items"] if i["transporte"] == "envío aparte"]
    assert aparte and all(i["prioridad"] != "esencial" for i in aparte)
    assert orden["peso_en_bus_kg"] <= 40 or orden["alertas"]


def test_correos_segun_traslado_y_ampliacion():
    bus = correos_requeridos(armar_orden("tarao", inventario_en_relevo("tarao", "2026-10-09")), "2026-10-09")
    assert [r["id"] for r in bus] == ["orden", "carga"]
    equipo = equipo_entrante("tarao", "2026-10-09").lower()
    assert bus[1]["para"] == [f"cuidador.tarao.{equipo}1@pampasur.example", f"cuidador.tarao.{equipo}2@pampasur.example"]

    vehiculo = correos_requeridos(armar_orden("maqui", lista_pedida={"M005": 8}), "2026-10-15")
    assert [r["id"] for r in vehiculo] == ["orden", "entrega"]
    assert "ampliación" in vehiculo[0]["proposito"]  # orden y ampliación en un solo correo
    assert vehiculo[0]["adjuntos"] == ["orden_compra.xlsx", "reporte_relevo.pdf"]
