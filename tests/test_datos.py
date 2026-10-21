"""Integridad de los datos del caso."""

from src.analisis import lista_recomendada, necesidad_turno
from src.datos import cargar_catalogo, cargar_centros, presupuesto_relevo


def test_cinco_centros_y_catorce_cuidadores():
    centros = cargar_centros()
    assert set(centros.index) == {"maqui", "chaiquen", "penasmo", "porcelana", "tarao"}
    assert centros["cuidadores_total"].sum() == 14
    assert (centros["cuidadores_total"] == centros["cuidadores_por_turno"] * 2).all()


def test_presupuesto_por_trabajador():
    assert presupuesto_relevo("maqui") == 180_000
    assert presupuesto_relevo("tarao") == 360_000


def test_solo_porcelana_y_tarao_viajan_en_bus():
    centros = cargar_centros()
    assert set(centros.index[centros["traslado"] == "bus"]) == {"porcelana", "tarao"}


def test_todo_producto_del_catalogo_tiene_duracion():
    cat = cargar_catalogo()
    assert cat.index.is_unique
    assert cat["prioridad"].notna().all()
    consumibles = cat[cat["categoria"] != "mantenimiento"]
    assert consumibles["rendimiento_dias"].gt(0).all()


def test_lista_recomendada_cabe_en_el_presupuesto():
    # Sin inventario (peor caso), la canasta base de cada centro no supera su presupuesto.
    for centro in cargar_centros().index:
        assert lista_recomendada(centro)["subtotal_clp"].sum() <= presupuesto_relevo(centro)


def test_necesidad_escala_con_cuidadores():
    # El detergente es "por persona": un centro de 2 cuidadores necesita el doble.
    assert necesidad_turno("tarao")["A003"] == 2 * necesidad_turno("maqui")["A003"]
    # La sal es "por centro": no cambia con la dotación.
    assert necesidad_turno("tarao")["L006"] == necesidad_turno("maqui")["L006"]
