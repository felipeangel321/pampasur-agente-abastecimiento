"""Pipeline RAG (EP1): documentos internos de Pampasur + catálogo externo de Lider.

Dos índices FAISS con embeddings locales:
- politicas: política de abastecimiento, protocolo de relevo, fichas de centros y
  fichas de duración de productos (fuentes internas) + textos externos opcionales.
- catalogo: un documento por producto del catálogo Lider (fuente externa); sirve
  para traducir lo que escriben los cuidadores ("confort", "nescafe") a un SKU.

Uso:
  uv run python -m src.rag "¿Cuánto es el presupuesto de Tarao?"
  uv run python -m src.rag --lista data/listas_whatsapp/maqui_2026-10-15.txt
"""

import math
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import pandas as pd
from langchain_community.vectorstores import FAISS
from langchain_community.vectorstores.utils import DistanceStrategy
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from src.config import DATA_DIR, DIAS_TURNO, INDICES_DIR, get_embeddings, get_llm, hay_groq
from src.datos import cargar_catalogo, cargar_centros, presupuesto_relevo
from src.prompts import PROMPT_INTERPRETAR_LINEA, PROMPT_RAG, LineaInterpretada

# Puntajes mínimos para aceptar un producto sin consultar al LLM (calibrados en tests/test_rag.py).
# El coseno de MiniLM da ~0,6 a productos equivocados con frases largas, por eso exige más.
UMBRAL_LEXICO = 0.75
UMBRAL_SEMANTICO = 0.75
# Si el segundo candidato queda a menos de esto del primero, la línea es ambigua ("papel").
MARGEN_EMPATE = 0.05


def _pesos(n: int) -> str:
    return f"${n:,.0f}".replace(",", ".")


# --- Construcción de documentos ---


def _docs_politicas() -> list[Document]:
    por_titulo = MarkdownHeaderTextSplitter([("#", "titulo"), ("##", "seccion")])
    en_trozos = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)

    docs = []
    for ruta in sorted((DATA_DIR / "internos").glob("*.md")):
        for d in por_titulo.split_text(ruta.read_text(encoding="utf-8")):
            titulo, seccion = d.metadata.get("titulo", ""), d.metadata.get("seccion", "")
            d.page_content = f"{titulo} — {seccion}\n{d.page_content}"
            d.metadata = {"fuente": "interna", "cita": f"{ruta.name} > {seccion or titulo}"}
            docs.append(d)
    docs = en_trozos.split_documents(docs)

    for _, c in cargar_centros().iterrows():
        if c["traslado"] == "bus":
            traslado = (
                f"Los trabajadores viajan en bus; Pampasur compra los pasajes (referencia {_pesos(c['pasaje_bus_ida_clp'])} "
                "por persona por tramo, supuesto) y los propios trabajadores llevan los víveres."
            )
        else:
            traslado = (
                "El prevencionista lleva al trabajador y los víveres en vehículo "
                f"(aprox. {c['km_ida_desde_base']} km por trayecto desde la base, supuesto)."
            )
        texto = (
            f"{c['nombre']} está en {c['ubicacion']}. Tiene {c['cuidadores_por_turno']} cuidador(es) por turno "
            f"y {c['cuidadores_total']} trabajadores en total (turnos 14x14). Presupuesto de víveres por relevo: "
            f"{_pesos(presupuesto_relevo(c.name))}. {traslado}"
        )
        docs.append(Document(texto, metadata={"fuente": "interna", "cita": f"centros.csv > {c['nombre']}"}))

    cat = cargar_catalogo()
    for sku, p in cat[cat["rendimiento_dias"].notna()].iterrows():
        base = "por persona" if p["escala"] == "persona" else "en el centro"
        texto = (
            f"Duración de {p['producto']}: 1 unidad rinde {p['rendimiento_dias']:g} días {base} "
            f"(≈{p['rendimiento_dias'] / DIAS_TURNO:.1f} turnos de 14 días"
            f"{' para 1 cuidador' if p['escala'] == 'persona' else ''}). "
            f"Prioridad: {p['prioridad']}. Dato {p['fuente_dato']}."
        )
        if isinstance(p["nota"], str):
            texto += f" {p['nota']}"
        docs.append(Document(texto, metadata={"fuente": "interna", "cita": f"duracion_productos.csv > {sku}"}))

    # Textos externos opcionales (normativa, guías), si se agregan a data/externos/.
    for ruta in sorted([*(DATA_DIR / "externos").glob("*.md"), *(DATA_DIR / "externos").glob("*.txt")]):
        docs += en_trozos.create_documents(
            [ruta.read_text(encoding="utf-8")], metadatas=[{"fuente": "externa", "cita": ruta.name}]
        )
    return docs


def _docs_catalogo() -> list[Document]:
    return [
        Document(
            f"{p['producto']}. Categoría: {p['categoria']}. También le dicen: {p['sinonimos'].replace('|', ', ')}.",
            metadata={"fuente": "externa", "cita": f"catalogo_lider.csv > {sku}", "sku": sku},
        )
        for sku, p in cargar_catalogo().iterrows()
    ]


# --- Índices (se reconstruyen solos si cambian los datos) ---


@lru_cache
def _embeddings():
    return get_embeddings()


def _fuentes() -> list[Path]:
    return [DATA_DIR / "centros.csv", *DATA_DIR.glob("internos/*"), *DATA_DIR.glob("externos/*")]


def _cargar_o_construir(nombre: str, construir) -> FAISS:
    ruta = INDICES_DIR / nombre
    archivo = ruta / "index.faiss"
    actualizado = archivo.exists() and archivo.stat().st_mtime >= max(f.stat().st_mtime for f in _fuentes())
    if actualizado:
        # El pickle lo generó este mismo proyecto; no se cargan índices de terceros.
        return FAISS.load_local(
            str(ruta), _embeddings(), allow_dangerous_deserialization=True,
            distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT,
        )
    indice = FAISS.from_documents(construir(), _embeddings(), distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT)
    indice.save_local(str(ruta))
    return indice


@lru_cache
def indice_politicas() -> FAISS:
    return _cargar_o_construir("politicas", _docs_politicas)


@lru_cache
def indice_catalogo() -> FAISS:
    return _cargar_o_construir("catalogo", _docs_catalogo)


# --- Recuperación y generación ---


def _centros_mencionados(consulta: str) -> list[str]:
    """Nombres de los centros que la consulta menciona por nombre o por localidad."""
    texto = f" {_normalizar(consulta)} "
    mencionados = []
    for _, c in cargar_centros().iterrows():
        claves = {_normalizar(c["nombre"].removeprefix("Centro ")), *_normalizar(c["ubicacion"]).split()}
        if any(f" {clave} " in texto for clave in claves if len(clave) >= 4):
            mencionados.append(c["nombre"])
    return mencionados


def buscar_politicas(consulta: str, k: int = 4) -> list[Document]:
    """Búsqueda semántica + fichas de los centros nombrados en la consulta.

    Las ~60 fichas de duración ocupan el top-k en cuanto la pregunta nombra un producto;
    si además pregunta por un centro, su ficha se agrega siempre al contexto.
    """
    indice = indice_politicas()
    docs = indice.similarity_search(consulta, k=k)
    citas = {d.metadata["cita"] for d in docs}
    por_cita = {d.metadata["cita"]: d for d in indice.docstore._dict.values()}
    for nombre in _centros_mencionados(consulta):
        cita = f"centros.csv > {nombre}"
        if cita not in citas:
            docs.insert(0, por_cita[cita])
    return docs


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", sin_tildes)).strip()


@lru_cache
def _sinonimos() -> tuple[tuple[str, str], ...]:
    """Pares (sku, término normalizado) con el nombre del producto y sus sinónimos."""
    pares = []
    for sku, p in cargar_catalogo().iterrows():
        for termino in [p["producto"], *p["sinonimos"].split("|")]:
            pares.append((sku, _normalizar(termino)))
    return tuple(pares)


def _coincidencia_lexica(texto: str) -> dict[str, float]:
    """Puntaje léxico por SKU: exacto 1,0; sinónimo contenido en el texto 0,9;
    texto contenido en un sinónimo 0,75; error de tipeo ("asucar") 0,95 × parecido;
    palabras sueltas dentro de un sinónimo ("loza" en "lavalozas") hasta 0,5, que no
    alcanza para aceptar solo pero asegura que el producto llegue como candidato al LLM."""
    consulta = _normalizar(texto)
    palabras = set(consulta.split())
    palabras_largas = [p for p in palabras if len(p) >= 4]
    puntajes: dict[str, float] = {}
    for sku, termino in _sinonimos():
        terminos = set(termino.split())
        if consulta == termino:
            puntaje = 1.0
        elif terminos <= palabras:
            puntaje = 0.9
        elif palabras <= terminos:
            puntaje = 0.75
        elif min(len(consulta), len(termino)) >= 4:
            parecido = SequenceMatcher(None, consulta, termino).ratio()
            puntaje = parecido * 0.95 if parecido >= 0.8 else 0
        else:
            puntaje = 0
        if not puntaje and palabras_largas:
            dentro = sum(p in termino for p in palabras_largas)
            puntaje = 0.5 * dentro / len(palabras_largas)
        if puntaje > puntajes.get(sku, 0):
            puntajes[sku] = puntaje
    return puntajes


def buscar_productos(texto: str, k: int = 3) -> list[tuple[Document, float, str]]:
    """Búsqueda híbrida en el catálogo: léxica sobre sinónimos + semántica con embeddings.

    Los embeddings fallan con palabras sueltas ("azucar" queda cerca de "mermelada"),
    y la búsqueda léxica no entiende descripciones ("algo pa lavar la loza"). Por cada
    producto se queda el mayor puntaje (0 a 1) y su origen: "lexica" o "semantica".
    """
    indice = indice_catalogo()
    resultados = {
        d.metadata["sku"]: (d, float(s), "semantica")
        for d, s in indice.similarity_search_with_score(texto, k=k)
    }
    docs_por_sku = {d.metadata["sku"]: d for d in indice.docstore._dict.values()}
    lexicos = _coincidencia_lexica(texto)
    for sku, puntaje in lexicos.items():
        if puntaje > resultados.get(sku, (None, 0, None))[1]:
            resultados[sku] = (docs_por_sku[sku], puntaje, "lexica")
    ordenados = sorted(resultados.values(), key=lambda r: r[1], reverse=True)
    mejores = ordenados[:k]
    # Las coincidencias léxicas que quedaron fuera del top-k se agregan al final (máx. 3).
    extra = [r for r in ordenados[k:] if lexicos.get(r[0].metadata["sku"], 0) > 0][:3]
    return mejores + extra


def es_coincidencia_confiable(candidatos: list[tuple[Document, float, str]]) -> bool:
    """El primer candidato supera el umbral de su origen y no empata con el segundo."""
    _, puntaje, origen = candidatos[0]
    umbral = UMBRAL_LEXICO if origen == "lexica" else UMBRAL_SEMANTICO
    empate = len(candidatos) > 1 and puntaje < 1.0 and puntaje - candidatos[1][1] < MARGEN_EMPATE
    return puntaje >= umbral and not empate


def formatear_contexto(docs: list[Document]) -> str:
    return "\n\n".join(f"[{d.metadata['cita']}]\n{d.page_content}" for d in docs)


def responder(pregunta: str, k: int = 4) -> dict:
    """Responde una pregunta con los documentos de Pampasur y devuelve las fuentes usadas."""
    docs = buscar_politicas(pregunta, k=k)
    respuesta = (PROMPT_RAG | get_llm()).invoke({"contexto": formatear_contexto(docs), "pregunta": pregunta})
    return {"respuesta": respuesta.content, "fuentes": list(dict.fromkeys(d.metadata["cita"] for d in docs))}


# --- Normalización de listas de WhatsApp ---

_RE_POR = re.compile(r"\bx\s*(\d+(?:[.,]\d+)?)", re.I)
_RE_CANTIDAD = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|kilos?|k|grs?|g|lts?|litros?|l|un|unidades?)?\b", re.I)
_RE_RELLENO = re.compile(r"\b(de|del|la|el|los|las|unos?|unas?|pa|para)\b", re.I)
_KG_POR_UNIDAD = {"kg": 1, "k": 1, "kilo": 1, "kilos": 1, "g": 0.001, "gr": 0.001, "grs": 0.001}
# Productos que vienen en paquete: "Papel higiénico 12 rollos", "Té 100 bolsitas"...
_RE_PAQUETE = re.compile(r"(\d+)\s*(rollos|bolsitas|sobres|cajas|unidades)\b")
_SINGULAR = {"rollos": "rollo", "bolsitas": "bolsita", "sobres": "sobre", "cajas": "caja", "unidades": "unidad"}


def interpretar_linea(linea: str) -> dict | None:
    """Separa cantidad, unidad y nombre de una línea como '3 kg pan' o 'cloro x2'."""
    texto = linea.strip().lstrip("-•*· ").strip()
    if not texto or texto.lower().startswith("lista"):
        return None
    cantidad, unidad = 1.0, None
    if m := _RE_POR.search(texto):
        cantidad = float(m.group(1).replace(",", "."))
        texto = texto[: m.start()] + texto[m.end() :]
    elif m := _RE_CANTIDAD.search(texto):
        cantidad = float(m.group(1).replace(",", "."))
        unidad = (m.group(2) or "").lower() or None
        texto = texto[: m.start()] + texto[m.end() :]
    nombre = re.sub(r"\s+", " ", _RE_RELLENO.sub(" ", texto)).strip()
    return {"linea": linea.strip(), "texto": nombre, "cantidad_texto": cantidad, "unidad_texto": unidad}


def _a_unidades(cantidad: float, unidad: str | None, prod: pd.Series, linea: str = "") -> int:
    """Convierte '4 kg de papas' en unidades del catálogo (2 mallas de 2 kg) y
    '40 rollos de confort' en paquetes (4 paquetes de 12 rollos, no 40 paquetes)."""
    if (m := _RE_PAQUETE.search(prod["producto"])) and _SINGULAR[m.group(2)] in _normalizar(linea):
        return max(1, math.ceil(cantidad / int(m.group(1)) - 1e-9))
    if unidad in _KG_POR_UNIDAD and prod["unidad"].endswith(("kg", " g")):
        return max(1, math.ceil(cantidad * _KG_POR_UNIDAD[unidad] / prod["peso_kg"] - 1e-9))
    return max(1, math.ceil(cantidad))


def _interpretar_con_llm(linea: str, candidatos: list[tuple[Document, float, str]]) -> str | None:
    cat = cargar_catalogo()
    skus = [d.metadata["sku"] for d, _, _ in candidatos]
    texto_candidatos = "\n".join(f"- {s}: {cat.loc[s, 'producto']}" for s in skus)
    # json_schema y no tool calling: con ejemplos few-shot en JSON, gpt-oss a veces responde
    # el JSON como texto y Groq rechaza la llamada (400 tool_use_failed).
    llm = get_llm().with_structured_output(LineaInterpretada, method="json_schema")
    # Groq devuelve de vez en cuando una generación vacía (400 json_validate_failed): se reintenta.
    cadena = (PROMPT_INTERPRETAR_LINEA | llm).with_retry(stop_after_attempt=3)
    try:
        resultado = cadena.invoke({"linea": linea, "candidatos": texto_candidatos})
    except Exception:
        return None  # la línea queda para revisión manual en vez de romper la lista completa
    return resultado.sku if resultado.sku in skus else None


def normalizar_lista(texto: str, usar_llm: bool | None = None) -> pd.DataFrame:
    """Convierte una lista de WhatsApp en productos del catálogo con cantidad y precio."""
    usar_llm = hay_groq() if usar_llm is None else usar_llm
    cat = cargar_catalogo()
    filas = []
    for linea in texto.splitlines():
        info = interpretar_linea(linea)
        if not info or not info["texto"]:
            continue
        candidatos = buscar_productos(info["texto"], k=5)
        doc, puntaje, origen = candidatos[0]
        sku, metodo = doc.metadata["sku"], origen
        if not es_coincidencia_confiable(candidatos):
            sku, metodo = None, "revisar"
            if usar_llm and (sku_llm := _interpretar_con_llm(info["linea"], candidatos)):
                sku, metodo = sku_llm, "llm"
        fila = {**info, "sku": sku, "similitud": round(puntaje, 3), "metodo": metodo}
        if sku:
            prod = cat.loc[sku]
            cantidad = _a_unidades(info["cantidad_texto"], info["unidad_texto"], prod, info["linea"])
            fila.update(
                producto=prod["producto"], cantidad=cantidad,
                precio_clp=int(prod["precio_clp"]), subtotal_clp=int(cantidad * prod["precio_clp"]),
            )
        filas.append(fila)
    columnas = ["linea", "texto", "cantidad_texto", "unidad_texto", "sku", "producto",
                "cantidad", "precio_clp", "subtotal_clp", "similitud", "metodo"]
    return pd.DataFrame(filas, columns=columnas)


# --- CLI ---


def _main() -> None:
    import argparse

    from src.analisis import evaluar_lista, inventario_en_relevo, skus_relevo_anterior

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="RAG de abastecimiento Pampasur")
    parser.add_argument("pregunta", nargs="?", help="pregunta sobre políticas, centros o productos")
    parser.add_argument("--lista", type=Path, help="archivo .txt con una lista de WhatsApp (centro_AAAA-MM-DD.txt)")
    args = parser.parse_args()

    if args.lista:
        tabla = normalizar_lista(args.lista.read_text(encoding="utf-8"))
        print(tabla[["linea", "producto", "cantidad", "subtotal_clp", "similitud", "metodo"]].to_string(index=False))
        centro, _, fecha = args.lista.stem.partition("_")
        # Solo archivos <centro>_<AAAA-MM-DD>.txt traen el relevo; "maqui_prueba.txt" solo se normaliza.
        if centro in cargar_centros().index and re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
            validos = tabla.dropna(subset=["sku"])
            ev = evaluar_lista(
                centro,
                dict(zip(validos["sku"], validos["cantidad"])),
                inventario_en_relevo(centro, fecha),
                skus_relevo_anterior(centro, fecha),
            )
            print(f"\nTotal pedido: {_pesos(validos['subtotal_clp'].sum())} "
                  f"(presupuesto {_pesos(presupuesto_relevo(centro))})")
            print(f"Compras innecesarias: {_pesos(ev['exceso_clp'].sum())}")
            obs = ev[ev["motivo"] != ""]
            print(obs[["producto", "pedida", "inventario", "recomendada", "exceso_clp", "motivo"]].to_string(index=False))
    elif args.pregunta:
        resultado = responder(args.pregunta)
        print(resultado["respuesta"])
        print("\nFuentes:", ", ".join(resultado["fuentes"]))
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
