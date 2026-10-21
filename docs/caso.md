# Caso organizacional: abastecimiento de centros Pampasur (EP1)

## Organización

**Pampasur** es una empresa de servicios de logística que trabaja para **Camanchaca**
(salmonicultura) en centros de cultivo cerrados de la Región de Los Lagos. En cada
centro hay cuidadores que trabajan en turnos de **14 días de trabajo por 14 de
descanso**. Pampasur se encarga de abastecerlos de alimentos, artículos de aseo y
materiales para trabajos de mantenimiento (jardinería, pintura, electricidad).

| Centro | Ubicación | Cuidadores por turno | Trabajadores totales | Traslado del personal |
|---|---|---|---|---|
| Maqui | Playa Maqui, Frutillar | 1 | 2 | Vehículo del prevencionista |
| Chaiquén | Cochamó | 1 | 2 | Vehículo del prevencionista |
| Peñasmo | Colaco | 1 | 2 | Vehículo del prevencionista |
| Porcelana | Hornopirén | 2 | 4 | Bus |
| Tarao | Chiloé | 2 | 4 | Bus |
| **Total** | | | **14** | |

## Proceso actual

1. Antes de cada relevo, se le pide al turno saliente la lista de lo que hay que comprar.
2. La lista llega por **WhatsApp**. Por lo general se **copia y pega la lista del turno
   anterior**, sin revisar lo que queda en bodega.
3. El **Responsable de compras** compra todo en **Lider**. También es quien aprueba
   las ampliaciones de presupuesto, así que las excepciones se resuelven rápido.
4. **Maqui, Chaiquén y Peñasmo:** el prevencionista lleva al trabajador y los víveres
   en vehículo.
5. **Porcelana y Tarao:** los trabajadores viajan en bus con pasajes que compra
   Pampasur y **llevan ellos mismos los víveres**.
6. Cada centro hace su relevo en un día distinto.

## Problema

Como las listas se copian, se compran productos que todavía alcanzan:

- **Productos de larga duración pedidos en cada turno.** El detergente para ropa dura
  varios turnos (dato observado por Felipe), pero aparece en relevos consecutivos.
- **Cantidades fijas que no corresponden al consumo.** Se piden siempre 5 kg de
  azúcar, cuando una persona consume unos 0,7 kg en 14 días: alcanza para ~7 turnos.
- **Mantenimiento repetido.** Si una lista tuvo pintura o tierra de hoja para un
  trabajo puntual, la copia la vuelve a pedir.
- **Perecibles que se pierden.** El pan, el queso o la lechuga que sobran al relevo se
  botan.
- **Sin trazabilidad.** No hay registro estructurado de qué se compró ni de qué se
  consumió, así que no se puede medir el desperdicio ni comparar centros.

### Magnitud

- **Presupuesto:** $180.000 por trabajador por turno.
- **Por ronda de relevos:** $1.260.000 (3 centros × $180.000 + 2 centros × $360.000).
- **Al año:** como cada centro tiene ~26 relevos, son unos **$32,8 millones solo en
  víveres**, más los pasajes de bus.

**Historial simulado:** abril a octubre de 2026, generado con `scripts/generar_historial.py`
y reproducible con semilla 42. Los resultados de `src/analisis.py` sobre ese historial son:

| Centro | Relevos | Listas copiadas | Gasto total | Compras innecesarias | % del gasto | Perecibles perdidos |
|---|---|---|---|---|---|---|
| Maqui | 15 | 47% | $2.650.680 | $990.000 | 37% | $250.055 |
| Chaiquén | 15 | 60% | $2.483.270 | $857.090 | 34% | $185.541 |
| Peñasmo | 14 | 43% | $2.410.310 | $829.530 | 34% | $194.487 |
| Porcelana | 15 | 67% | $4.457.040 | $1.348.040 | 30% | $213.917 |
| Tarao | 14 | 50% | $4.271.250 | $1.094.030 | 26% | $252.984 |
| **Total** | **73** | | **$16.272.550** | **$5.118.690** | **31%** | **$1.096.984** |

**Productos con más compras innecesarias:**

| Producto | Relevos con exceso | Monto en exceso |
|---|---|---|
| Detergente líquido para ropa 3 L | 67 | $639.200 |
| Huevos bandeja 30 unidades | 42 | $356.490 |
| Chuleta de cerdo 1 kg | 45 | $317.470 |
| Azúcar 1 kg | 53 | $311.360 |
| Papel higiénico 12 rollos | 44 | $275.540 |

El detergente y el azúcar, los dos hábitos observados en terreno, aparecen entre los
cuatro productos con más exceso. Una "compra innecesaria" es lo pedido por sobre la
necesidad del turno (más un 20% de tolerancia), descontando lo que quedaba en bodega.

> Estos números salen de datos **simulados** que reproducen el comportamiento descrito.
> Con las listas reales de WhatsApp se vuelve a correr el mismo análisis.

## Objetivos de la intervención (medibles)

1. **Reducir las compras innecesarias**: bajar el exceso detectado (compras sobre la
   necesidad del turno + 20% de tolerancia) a menos del 5% del gasto por relevo.
2. **Eliminar la copia de listas**: el 100% de las listas se calcula a partir del
   inventario del turno saliente.
3. **Controlar el presupuesto**: toda compra sobre `cuidadores × $180.000` sale con una
   solicitud de ampliación justificada ítem por ítem.
4. **Reducir el tiempo de preparación de la compra**: de revisar una lista a mano a
   recibir una orden lista en minutos.
5. **Trazabilidad**: cada compra queda registrada con su inventario, su necesidad
   calculada y el motivo de cada ajuste.

## Datos disponibles

| Dato | Estado | Uso en la solución |
|---|---|---|
| Centros, dotación y traslado | Real (entregado por la empresa) | Necesidad y presupuesto por relevo |
| Presupuesto y regla de ampliación | Real | Validación de la compra |
| Duración de productos | Observado (detergente, azúcar) + supuesto (resto) | Detectar compras anticipadas y excesivas |
| Listas de WhatsApp | Por conseguir (simuladas por ahora) | Entrada del sistema |
| Catálogo y precios Lider | Simulado con precios referenciales | Cotización y normalización |
| Historial de compras e inventarios | Simulado | Dimensionar el problema y probar el agente |

## Restricciones y requerimientos

- **Presupuesto:** $180.000 por trabajador por turno, ampliable con aprobación del
  Responsable de compras.
- **Lugar de compra:** solo Lider.
- **Carga por trabajador:** en Porcelana y Tarao la compra tiene que poder llevarse en
  bus (supuesto: 20 kg por trabajador).
- **Privacidad:** las listas reales contienen nombres y teléfonos. Se anonimizan y los
  datos crudos no se suben al repositorio (`data/raw/` está en `.gitignore`).
- **Sin envío automático:** en la EP2, ningún correo sale sin aprobación humana.
- **Costo:** el sistema usa el plan gratuito de Groq y embeddings locales, sin costo
  de licencias.

## Por qué agentes, LLM y RAG

- **LLM:** las listas llegan en lenguaje informal ("confort", "nescafe", "algo pa la
  loza"); hay que interpretarlas y llevarlas a productos del catálogo.
- **RAG:** las reglas (presupuesto, prioridades, duración, traslado) están en
  documentos internos. Las respuestas tienen que apoyarse en esas reglas y citarlas,
  no inventarlas.
- **Agente (EP2):** el relevo es un proceso de varias etapas (inventario → necesidad →
  validación → presupuesto → orden → correos) que cambia según el centro (bus o
  vehículo, 1 o 2 personas) y según la situación (sobre presupuesto, producto agotado,
  relevo atrasado).

## Supuestos a validar con Pampasur

| Supuesto | Valor usado | Dónde |
|---|---|---|
| Sucursal de compra | Lider Puerto Montt | política |
| Base de operaciones | Puerto Montt | protocolo |
| Distancias en vehículo | Maqui 70 km, Chaiquén 95 km, Peñasmo 50 km | `centros.csv` |
| Pasaje de bus (ida) | Hornopirén $9.000, Chiloé $12.000 | `centros.csv` |
| Carga máxima en bus | 20 kg por trabajador | protocolo |
| Reserva por mal tiempo | 3 días de no perecibles | política |
| Umbral de ampliación con justificación extendida | 20% | política |
| Duración de cada producto | Ver columna `fuente_dato` | `duracion_productos.csv` |
| Precios | Referenciales | `catalogo_lider.csv` |
