# Agente de Abastecimiento Pampasur

Proyecto de **Ingeniería de Soluciones con IA (ISY0101)**. Es un agente de IA que arma y
valida las compras de víveres de los centros de cultivo que Pampasur atiende para
Camanchaca.

**Problema:** en cada relevo (turnos 14x14) los cuidadores envían la lista de compra
copiando la del turno anterior desde WhatsApp. Se compra lo que todavía queda en bodega:
detergente que dura 3 turnos se pide en cada relevo, se piden 5 kg de azúcar cuando se
consume menos de 1 kg, etc.

**Solución:** a partir del inventario que deja el turno saliente, la **duración de cada
producto** y la dotación del centro, el sistema calcula lo que realmente hace falta,
detecta las compras innecesarias y las listas copiadas, arma la orden de compra y envía
los correos con sus adjuntos después de que una persona los aprueba.

| Centro | Ubicación | Cuidadores por turno | Presupuesto por relevo | Traslado |
|---|---|---|---|---|
| Maqui | Playa Maqui, Frutillar | 1 | $180.000 | Vehículo del prevencionista |
| Chaiquén | Cochamó | 1 | $180.000 | Vehículo del prevencionista |
| Peñasmo | Colaco | 1 | $180.000 | Vehículo del prevencionista |
| Porcelana | Hornopirén | 2 | $360.000 | Bus (llevan los víveres) |
| Tarao | Chiloé | 2 | $360.000 | Bus (llevan los víveres) |

## Estado

- [x] **EP1:** caso, datos, RAG (políticas internas + catálogo Lider), prompts,
  reglas de duración, tests
- [x] **EP2:** grafo LangGraph con dos agentes (Analista y Correos), memoria de corto y
  largo plazo, planificación adaptativa y envío por Gmail con aprobación humana
  ([`docs/arquitectura_agente.md`](docs/arquitectura_agente.md))
- [ ] **EP3:** observabilidad (LangSmith)

## Instalación

Requisitos: [uv](https://docs.astral.sh/uv/) y una API key gratuita de
[Groq](https://console.groq.com/).

```bash
uv sync                       # crea .venv e instala dependencias
cp .env.example .env          # y completa GROQ_API_KEY
```

La primera vez que se usa el RAG se descarga el modelo de embeddings (~470 MB) y se
crean los índices en `.indices/`. Las siguientes ejecuciones los reutilizan.

Para enviar correos de verdad (opcional): deja `credentials.json` de Google Cloud en la
raíz, corre `uv run python -m scripts.autenticar_gmail` una vez y usa
`MODO_CORREO="gmail"` en `.env`. Los pasos están en
[docs/arquitectura_agente.md > Gmail](docs/arquitectura_agente.md#6-gmail-configuración-una-sola-vez).

## Agente (EP2)

Cada relevo pasa por un grafo de LangGraph **sin agente manager**: el orden del proceso
está en el código y las rutas dependen de los datos (¿llegó lista? ¿centro con bus?
¿se pasa del presupuesto? ¿el revisor rechazó un correo?).

| Rol | Qué hace | LLM |
|---|---|---|
| Intérprete | Convierte la lista de WhatsApp en productos del catálogo (RAG de la EP1) | Solo líneas dudosas |
| Comprador | Arma la orden: inventario, duración, presupuesto, ampliación y carga del bus; avisa si la lista es copiada o se pasa del presupuesto | No (auditable) |
| **Analista** | Explica la orden con la política (citas) y la memoria de relevos anteriores | Sí, 7 herramientas |
| **Correos** | Redacta y envía por Gmail la orden y los avisos, con Excel y PDF adjuntos | Sí, con aprobación humana |

```bash
# Procesar un relevo: pide aprobar cada correo en la terminal ([s] pausa y se retoma después)
uv run python -m src.grafo --centro tarao --fecha 2026-10-09 --lista data/listas_whatsapp/tarao_2026-10-09.txt

# Conversar con el Analista (recuerda la conversación y los relevos ya procesados)
uv run python -m src.agentes

# Medir llamadas, tokens y segundos por relevo
uv run python -m scripts.medir
```

Con `MODO_CORREO="outbox"` (el valor de `.env.example`) los correos **no salen**: quedan
como `.eml`, con sus adjuntos, en `outbox/<centro>_<fecha>/`. Con `MODO_CORREO="gmail"`
salen por la API de Gmail y además se guarda la copia. El guion para mostrarlo está en
[docs/demo.md](docs/demo.md).

## Uso del RAG (EP1)

```bash
# Preguntas sobre políticas, centros y productos (RAG + Groq)
uv run python -m src.rag "¿Cuántos turnos dura el detergente y cuánto se puede gastar en Tarao?"

# Interpretar una lista de WhatsApp y detectar compras innecesarias (no necesita Groq)
uv run python -m src.rag --lista data/listas_whatsapp/<centro>_<fecha>.txt

# Regenerar el historial simulado
uv run python -m scripts.generar_historial

# Tests (los marcados "llm" llaman a Groq; para omitirlos: -m "not llm")
uv run pytest
```

## Estructura

```
├── data/
│   ├── centros.csv               # 5 centros, dotación, traslado
│   ├── contactos.csv             # roles y correos ficticios (@pampasur.example)
│   ├── historial_compras.csv     # SIMULADO: compras por relevo, abril a octubre 2026
│   ├── inventarios.csv           # SIMULADO: lo que reporta cada turno saliente
│   ├── listas_whatsapp/          # SIMULADO: listas como las escriben los cuidadores
│   ├── internos/                 # política, protocolo, duración de productos
│   ├── externos/                 # catálogo Lider (precios referenciales)
│   └── raw/                      # datos reales sin anonimizar (ignorado por git)
├── docs/
│   ├── caso.md                   # EP1: caso organizacional, objetivos y supuestos
│   ├── arquitectura_rag.md       # EP1: diagrama y componentes del RAG
│   ├── prompts.md                # EP1 y EP2: diseño de prompts del RAG y de los agentes
│   ├── arquitectura_agente.md    # EP2: ADR, grafo, contratos, medición, Gmail
│   ├── demo.md                   # EP2: guion de la demostración
│   └── informe/                  # informe escrito de la entrega
├── outbox/                       # correos generados y sus adjuntos (ignorado por git)
├── scripts/
│   ├── generar_historial.py      # simulación reproducible (semilla 42)
│   ├── autenticar_gmail.py       # login de Google una vez (crea gmail_token.json)
│   └── medir.py                  # tokens y segundos por relevo
├── src/
│   ├── config.py                 # rutas, reglas de negocio, LLM y embeddings
│   ├── datos.py                  # carga de CSV
│   ├── analisis.py               # reglas de duración: necesidad, exceso, resumen
│   ├── prompts.py                # prompts del sistema (RAG y agentes)
│   ├── rag.py                    # índices FAISS, búsqueda, respuesta, normalización
│   ├── compra.py                 # EP2: Comprador (orden, presupuesto, carga, destinatarios)
│   ├── herramientas.py           # EP2: herramientas de los agentes
│   ├── agentes.py                # EP2: agentes Analista y Correos; chat
│   ├── grafo.py                  # EP2: grafo del relevo; CLI con aprobación
│   ├── memoria.py                # EP2: checkpointer y memoria de largo plazo (SQLite)
│   ├── correo.py                 # EP2: Gmail API y bandeja local
│   └── reportes.py               # EP2: orden en Excel y reporte en PDF
└── tests/                        # datos, análisis, RAG, compra, correo y grafo
```

## Datos reales y privacidad

Los datos de `data/` son **simulados** a partir de lo que describió la empresa. Para
usar listas reales de WhatsApp:

1. Exportar el chat sin archivos y dejar el `.txt` en `data/raw/` (no se sube a git).
2. Reemplazar los nombres y teléfonos antes de copiar una lista a `data/listas_whatsapp/`.
3. Los supuestos están marcados con **(supuesto)** en los documentos y con
   `fuente_dato=supuesto` en `duracion_productos.csv`.

## Tecnologías

LangChain 1.x (`create_agent`, middleware de aprobación humana) · LangGraph (`StateGraph`,
`SqliteSaver`, `SqliteStore`) · Groq (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`) ·
sentence-transformers (embeddings locales) · FAISS · Gmail API · pandas · openpyxl · fpdf2 · pytest
