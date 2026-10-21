"""Reglas de duración: detectan compras anticipadas y cantidades excesivas."""

from pytest import approx

from src.analisis import evaluar_lista, productos_mas_sobrecomprados, resumen_por_centro, turnos_que_cubre


def _fila(ev, sku):
    return ev.set_index("sku").loc[sku]


def test_detergente_dura_tres_turnos_para_un_cuidador():
    assert turnos_que_cubre("maqui", "A003", 1) == approx(3)
    assert turnos_que_cubre("tarao", "A003", 1) == approx(1.5)


def test_detergente_con_stock_pedido_otra_vez_es_exceso():
    ev = evaluar_lista("maqui", {"A003": 1}, inventario={"A003": 0.7}, sku_comprados_relevo_anterior={"A003"})
    fila = _fila(ev, "A003")
    assert fila["recomendada"] == 0
    assert fila["exceso"] == 1
    assert "ya cubre el turno" in fila["motivo"]
    assert "relevos consecutivos" in fila["motivo"]


def test_cinco_kilos_de_azucar_es_cantidad_excesiva():
    ev = evaluar_lista("maqui", {"L005": 5})
    fila = _fila(ev, "L005")
    assert "cantidad excesiva" in fila["motivo"]
    assert fila["exceso"] == 4  # necesita 1 kg por turno
    assert fila["exceso_clp"] == 4 * 1390


def test_lista_ajustada_no_tiene_observaciones():
    ev = evaluar_lista("maqui", {"L001": 2, "A007": 1})
    assert ev["exceso"].sum() == 0
    assert (ev["motivo"] == "").all()


def test_historial_muestra_el_problema():
    resumen = resumen_por_centro()
    assert len(resumen) == 5
    assert (resumen["listas_copiadas_pct"] > 0).all()
    assert (resumen["exceso_total_clp"] > 0).all()
    # Los hábitos observados (detergente y azúcar) aparecen entre los más sobrecomprados.
    top = set(productos_mas_sobrecomprados(top=5)["sku"])
    assert {"A003", "L005"} <= top
