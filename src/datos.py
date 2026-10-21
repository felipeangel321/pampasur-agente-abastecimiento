"""Carga de los datos del caso (CSV en data/)."""

from functools import lru_cache

import pandas as pd

from src.config import DATA_DIR, PRESUPUESTO_POR_TRABAJADOR


@lru_cache
def _leer(ruta_relativa: str, fechas: tuple[str, ...] = ()) -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / ruta_relativa, encoding="utf-8", parse_dates=list(fechas))


def cargar_centros() -> pd.DataFrame:
    return _leer("centros.csv", ("primer_relevo",)).set_index("id").copy()


def cargar_contactos() -> pd.DataFrame:
    return _leer("contactos.csv").copy()


def cargar_catalogo() -> pd.DataFrame:
    """Catálogo Lider (fuente externa) unido a la duración de cada producto (fuente interna)."""
    catalogo = _leer("externos/catalogo_lider.csv")
    duracion = _leer("internos/duracion_productos.csv")
    df = catalogo.merge(duracion, on="sku", how="left").set_index("sku")
    df["perecible"] = df["perecible"].eq("si")
    return df.copy()


def cargar_historial() -> pd.DataFrame:
    return _leer("historial_compras.csv", ("fecha_relevo",)).copy()


def cargar_inventarios() -> pd.DataFrame:
    return _leer("inventarios.csv", ("fecha_relevo",)).copy()


def presupuesto_relevo(centro_id: str) -> int:
    personas = int(cargar_centros().loc[centro_id, "cuidadores_por_turno"])
    return personas * PRESUPUESTO_POR_TRABAJADOR
