# Guion de la demostración (EP2)

Duración: unos 5 minutos. Se usa el **Centro Tarao, relevo del 2026-10-09**: va en bus,
la lista de WhatsApp viene copiada y el sistema tiene que tomar varias decisiones.

---

## Antes de empezar

1. Abre una terminal **dentro de la carpeta `pampasur-agente-abastecimiento`**.
2. Abre tu **Gmail** (felipeangel90@gmail.com) en el navegador.
3. Si pasaron más de 7 días desde la última vez que autorizaste Gmail, corre esto y acepta
   en el navegador:

   ```bash
   uv run python -m scripts.autenticar_gmail
   ```

---

## Paso 1 – Mostrar el problema

Abre el archivo `data/listas_whatsapp/tarao_2026-10-09.txt`.

> **Qué decir:** "Esta es la lista que mandan los cuidadores por WhatsApp. Es copiada del
> turno anterior: piden 5 kilos de azúcar y 2 detergentes, aunque en bodega todavía
> quedan."

---

## Paso 2 – Correr el agente

```bash
uv run python -m src.grafo --centro tarao --fecha 2026-10-09 --lista data/listas_whatsapp/tarao_2026-10-09.txt
```

Van a ir apareciendo líneas que empiezan con `·`. Cada una es un paso del agente. Una de
ellas es la alerta **⚠ Lista copiada: 41 de 41 productos (100%)**: la lista de Tarao es
igual a la del relevo del 25 de septiembre.

> **Qué decir:** "El agente lee la lista, calcula lo que de verdad hace falta según el
> inventario, revisa el presupuesto y el peso que cabe en el bus, y el Analista explica la
> compra citando la política de la empresa."

---

## Paso 3 – Aprobar el primer correo

Aparece el correo para el **Responsable de compras**. Escribe:

```
a
```

y presiona Enter.

> **Qué decir:** "Ningún correo sale sin que una persona lo apruebe."

---

## Paso 4 – Rechazar el segundo correo (la parte más importante)

Aparece el correo para los **cuidadores**. Escribe:

```
r
```

Presiona Enter y escribe un comentario, por ejemplo:

```
Saluda al equipo B y di cuántos kilos lleva cada uno
```

El agente reescribe el correo y te lo muestra de nuevo. Ahora apruébalo:

```
a
```

> **Qué decir:** "Lo rechacé con un comentario y el agente lo corrigió solo. Además, ese
> comentario queda guardado en la memoria y lo aplica en los próximos relevos."

---

## Paso 5 – Mostrar los correos en Gmail

Abre los 2 correos que llegaron. El asunto empieza con `[PRUEBA para ...]` porque los
contactos de la empresa son ficticios y todo se redirige a tu Gmail.

- Correo al Responsable de compras: abre el **Excel** (orden de compra) y el **PDF** (reporte).
- Correo a los cuidadores: muestra los kilos por persona que llevan en el bus.

> **Qué decir:** "La lista de WhatsApp pedía $271.250 y la compra real quedó en $175.430:
> se evitaron $77.340 en compras innecesarias."

---

## Paso 6 – Mostrar la memoria del agente

```bash
uv run python -m src.agentes
```

Haz estas dos preguntas, una a la vez:

1. `¿Qué pasó en el último relevo de Tarao?`
   → Recuerda el relevo que acabas de hacer (**memoria de largo plazo**).
2. `¿Y cuánto detergente quedaba?`
   → Entiende que sigues hablando de Tarao (**memoria de corto plazo**).

Para terminar, escribe:

```
salir
```

---

## Paso 7 – Mostrar que detecta la lista copiada

La lista de Maqui del 15-10 es **igual** a la del relevo anterior (01-10): alguien la copió
y pegó del chat.

```bash
uv run python -m src.grafo --centro maqui --fecha 2026-10-15 --lista data/listas_whatsapp/maqui_2026-10-15.txt --nuevo
```

(`--nuevo` es porque ese relevo ya se procesó una vez.)

En la terminal aparece una línea con **⚠ Lista copiada: 39 de 39 productos (100%)...** y,
al aprobar el correo del Responsable, abajo dice **"Alertas del sistema"** con el mismo aviso.

> **Qué decir:** "El sistema compara la lista con la del relevo anterior. Si más del 90% es
> igual (mismos productos y cantidades), avisa que es copia. Esa alerta la agrega el
> sistema, no el modelo: no depende de que la IA se acuerde de mencionarla."

---

## Cómo probar una lista inventada por ti

1. Crea un archivo de texto, por ejemplo `data/listas_whatsapp/maqui_prueba.txt`, y escribe
   la lista como en WhatsApp, un producto por línea:

   ```
   Lista Maqui
   40 rollos de confort
   detergente x2
   azucar 5 kilos
   2 six de cerveza
   ```

2. Córrelo con una fecha que tenga inventario (para Maqui, `2026-10-15`):

   ```bash
   uv run python -m src.grafo --centro maqui --fecha 2026-10-15 --lista data/listas_whatsapp/maqui_prueba.txt --nuevo
   ```

Qué detecta:

| Lo que pides | Qué hace el sistema |
|---|---|
| `40 rollos de confort` | Lo entiende como 4 paquetes de 12 rollos y lo marca como exceso si en bodega queda papel |
| `detergente x2`, `azucar 5 kilos` | Exceso: el inventario alcanza o la cantidad dura varios turnos |
| `2 six de cerveza` | No se compra: está fuera del catálogo autorizado |
| Una lista que suma más que el presupuesto | Alerta **⚠ Sobregasto en la lista** y corrige la orden a lo que el turno necesita |
| La misma lista del relevo anterior | Alerta **⚠ Lista copiada** |

Fechas con inventario para cada centro: Maqui `2026-10-15`, Chaiquén `2026-10-18`,
Peñasmo `2026-10-07`, Porcelana `2026-10-16`, Tarao `2026-10-09`.

---

## Si algo sale mal

| Qué pasa | Qué hacer |
|---|---|
| Dice "El relevo tarao-2026-10-09 ya se procesó" (por ejemplo, porque ensayaste antes) | Agrega `--nuevo` al final del comando del paso 2 |
| Un correo falla con "token venció" | Corre `uv run python -m scripts.autenticar_gmail` y vuelve a intentar |
| Se demora mucho o aparece "rate limit" | Es el límite gratuito de Groq: espera 1 minuto. El agente reintenta solo |
| Quieres pausar en medio de la aprobación | Escribe `s`. Para continuar, corre el mismo comando del paso 2 y sigue donde quedó |
| No llegó un correo | Revisa Spam. Además, una copia de cada correo queda en `outbox/tarao_2026-10-09/` |

---

## Extra (si te preguntan cómo se sabe que funciona)

```bash
uv run pytest tests/test_grafo.py -v
```

Muestra las pruebas automáticas: aprobación, rechazo con comentario, memoria entre relevos
y el plan B cuando el modelo falla.
