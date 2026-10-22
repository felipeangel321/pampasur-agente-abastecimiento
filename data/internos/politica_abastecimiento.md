# Política de abastecimiento de centros

> Documento interno **simulado** para el proyecto académico. Se construyó con la
> información entregada por Pampasur; los puntos marcados **(supuesto)** deben
> validarse con la empresa antes de usarse en operación.

## Alcance

Aplica al abastecimiento de víveres, artículos de aseo y materiales de
mantenimiento de los centros de cultivo que Pampasur atiende para Camanchaca:
Centro Maqui (Playa Maqui, Frutillar), Centro Chaiquén (Cochamó), Centro Peñasmo
(Colaco), Centro Porcelana (Hornopirén) y Centro Tarao (Chiloé). Los centros son
cerrados y cuentan con cuidadores que trabajan en turnos de 14 días de trabajo por
14 días de descanso.

## Presupuesto por turno

- El presupuesto es de **$180.000 por trabajador por turno de 14 días** y cubre los
  víveres y los artículos de aseo.
- Es un **tope máximo que siempre se respeta**: la compra de víveres y aseo nunca lo
  supera y no se amplía.
- Un centro con 1 cuidador por turno (Maqui, Chaiquén, Peñasmo) dispone de
  $180.000 por relevo.
- Un centro con 2 cuidadores por turno (Porcelana, Tarao) dispone de $360.000 por
  relevo.
- Los pasajes de bus de los trabajadores **no** se descuentan de este presupuesto:
  se registran como gasto de locomoción.
- Los materiales de mantenimiento tampoco salen de este presupuesto: se pagan como
  un extra (ver "Extra de mantenimiento").

## Extra de mantenimiento

- El mantenimiento es ocasional, cada varios meses: pintar, arreglar algo que se
  rompió o resolver un problema de luz o de agua. No se pinta todos los meses ni se
  rompen cosas todos los turnos.
- Por eso se paga como un **extra**, aparte del presupuesto de víveres y aseo.
- El extra lo aprueba el **Responsable de compras**, que es la misma persona que
  compra los víveres. Por eso la orden de compra y la aprobación del extra van en un
  solo correo.
- La solicitud indica el monto, los materiales y el trabajo para el que se
  necesitan.

## Cómo se arma la lista de compra

- La lista se arma a partir del **inventario que deja el turno saliente**. No se
  debe copiar la lista del turno anterior.
- Necesidad del turno = consumo esperado de 14 días según la tabla de duración de
  productos (`duracion_productos.csv`) por la cantidad de cuidadores.
- Cantidad a comprar = necesidad del turno menos el inventario utilizable que queda
  en el centro.
- Los productos de larga duración (detergente para ropa, sal, té, escoba) **no se
  compran todos los turnos**: solo cuando el inventario no alcanza para el turno
  que entra.
- Pedir más de 1,5 veces la necesidad de un producto debe justificarse.

## Prioridades de compra

- **Esencial**: alimentos base (arroz, fideos, aceite, legumbres, huevos, leche,
  pan, carnes), papel higiénico, cloro, lavalozas, jabón de manos y bolsas de
  basura.
- **Normal**: complementos de alimentación y artículos de limpieza no críticos.
- **Opcional**: productos de gusto personal o que el centro no necesita siempre
  (jugo en polvo, harina, agua embotellada si hay agua potable).
- **Por solicitud**: materiales de mantenimiento; se piden con la descripción del
  trabajo a realizar y se pagan como extra.
- Si la compra de víveres y aseo no cabe en el presupuesto, se recorta primero lo
  opcional, después lo normal y nunca lo esencial.
- **(supuesto)** No se compran bebidas alcohólicas ni cigarrillos con el
  presupuesto de víveres. Solo se compran productos del catálogo autorizado.

## Productos perecibles

- Pan, queso, jamón, tomate, plátano y lechuga se pierden si sobran al cambio de
  turno. Se compran solo para el consumo esperado del turno.
- Las carnes y el pollo se compran congelados y se guardan en el freezer del
  centro.

## Reserva por mal tiempo

- **(supuesto)** Cada centro mantiene una reserva de productos no perecibles para 3
  días adicionales, porque el relevo puede atrasarse por condiciones climáticas.
  Esta reserva se repone solo si se consumió.

## Mantenimiento

- Los materiales de mantenimiento (jardinería, pintura, electricidad) se piden por
  separado, indicando el trabajo para el que se necesitan, y se pagan como extra.
- No se repiten en el turno siguiente salvo que el trabajo continúe; una lista
  copiada que vuelve a pedir pintura o tierra de hoja es una compra innecesaria.
- Los artículos durables (escoba, guantes de trabajo) se reponen solo si el turno
  saliente informa que están dañados o se agotaron.

## Lugar de compra

- Todas las compras se realizan en el supermercado Lider.
- **(supuesto)** La sucursal habitual es Lider Puerto Montt.
