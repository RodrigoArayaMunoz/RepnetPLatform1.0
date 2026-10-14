# Compatibilidades y posiciones de faros

Verificación del 13 de octubre de 2026, hora de Chile, para
`Refax_Contenido_COMPATIBILITIES_7343dc9b.xlsx`.

El archivo contiene **16.474 filas y 745 publicaciones**, todas con
`FAMILIA = MLC-VEHICLE_HEADLIGHTS`. La regla anterior omitía las posiciones
de **4.464 filas**: 4.400 con ambos lados y 64 con la primera columna vacía.
La corrección permite tratar ambas columnas de forma independiente y
combina las posiciones de filas que representan el mismo vehículo y destino.

## Resultado del archivo

| Primera columna | IZQUIERDA/DERECHA | Filas | Posiciones |
| --- | --- | ---: | --- |
| DELANTERA | IZQUIERDA/DERECHA | 4.400 | Delantera + Izquierda; Delantera + Derecha |
| DELANTERA | DERECHA | 5.851 | Delantera + Derecha |
| DELANTERA | IZQUIERDA | 6.159 | Delantera + Izquierda |
| Vacía | IZQUIERDA | 35 | Izquierda |
| Vacía | IZQUIERDA/DERECHA | 23 | Izquierda; Derecha |
| Vacía | DERECHA | 6 | Derecha |

Las **16.474 filas** generan las posiciones correspondientes, sin diferencias.
Los dos lados se envían como alternativas separadas en
`restrictions[].attribute_values`; cada alternativa conserva la posición
principal cuando está informada. No se infieren valores para columnas vacías.

La primera columna admite Delantera, Trasera, Conductor y Acompañante. Si
solamente esa columna contiene un valor, se envía esa posición. Se normalizan
mayúsculas y espacios, y se eliminan alternativas repetidas. Si ambas columnas
están vacías o contienen valores desconocidos, la fila devuelve
`INVALID_HEADLIGHT_POSITION` antes de escribir. Las filas válidas continúan.

## IDs comprobados en Mercado Libre

La consulta autenticada de
[valores de posición para faros en Chile](https://api.mercadolibre.com/catalog_compatibilities/restrictions/values?main_domain_id=MLC-CARS_AND_VANS_FOR_COMPATIBILITIES&secondary_domain_id=MLC-VEHICLE_HEADLIGHTS)
respondió HTTP 200 y confirmó los nombres y los seis IDs utilizados:

| Posición | value_id |
| --- | --- |
| Delantera | 13701104 |
| Trasera | 13701105 |
| Conductor | 13373175 |
| Acompañante | 13373176 |
| Izquierda | 2262158 |
| Derecha | 2262160 |

La [documentación de restricciones](https://developers.mercadolibre.cl/es_ar/atributos-y-variaciones/compatibilidades-entre-items-y-productos)
describe las alternativas mediante listas de valores y exige IDs del conjunto
admitido, combinaciones únicas y límites de posiciones. El recorrido simulado
del archivo comprobó también que ningún conjunto combinado supera cuatro
alternativas.

Se consultaron las **745 publicaciones reales** mediante GET. Todas pertenecen
al vendedor conectado, dominio `MLC-VEHICLE_HEADLIGHTS`, categoría `MLC161712`,
y tienen `category_id` y `user_product_id` para el destino de escritura.
Las respuestas guardadas no contienen credenciales.

## Verificación de funcionamiento

- **260 pruebas del backend pasaron**, incluidas siete pruebas para faros.
- Se verificaron los 19 patrones válidos posibles desde los encabezados
  originales del Excel, errores de posición, filas repetidas, separación de
  destinos y conservación de posiciones entre bloques.
- La creación nueva incluye nota y posiciones en un POST. Un caso existente
  con cero creaciones comprueba que el PUT conserva la nota y los dos lados
  cuando la primera columna está vacía.
- El orquestador completo procesó las 16.474 filas en una simulación con los
  destinos reales. Se compararon exactamente las posiciones finales por
  vehículo y destino, incluyendo la unión de filas repetidas. El orden de
  los resultados coincide con el Excel.
- Pyright 1.1.414 comprobó el servicio, las pruebas y los scripts de auditoría
  de faros y amortiguadores: **cero errores y cero advertencias**.

El recorrido completo simula un producto por familia vehicular: 2.190
consultas de conteo, 810 POST por familias y 410 POST de copia. Estas cifras
corresponden a la simulación y no predicen el tráfico real del catálogo.

Las consultas reales fueron de lectura. Las escrituras se comprobaron con
respuestas simuladas: **cero compatibilidades creadas o modificadas durante
esta verificación**. No se verificó contra la API la existencia de todos los
vehículos del archivo; el flujo conserva su validación y fallback actuales.
El código debe desplegarse en los workers para utilizar la corrección en el
servidor. El Excel original permanece como archivo de entrada.

## Scripts y evidencia

- [Auditoría de las posiciones y seis envíos simulados](../artifacts/headlight-compatibilities/excel-positions-validation.json).
- [Recorrido completo simulado](../artifacts/headlight-compatibilities/full-pipeline-simulation.json).
- [IDs oficiales](../artifacts/headlight-compatibilities/mercadolibre-position-values.json).
- [745 destinos comprobados](../artifacts/headlight-compatibilities/mercadolibre-items-verification.json).
- [Omisiones anteriores a la corrección](../artifacts/headlight-compatibilities/before-fix.json).

Desde la raíz del proyecto, reproducir la auditoría sin escrituras reales:

```powershell
.venv\Scripts\python.exe artifacts\headlight-compatibilities\verify_positions.py 'C:\Users\Rodrigo Araya\Downloads\Refax_Contenido_COMPATIBILITIES_7343dc9b.xlsx'
```

El recorrido completo también se puede ejecutar sin llamadas a la API:

```powershell
.venv\Scripts\python.exe artifacts\headlight-compatibilities\verify_full_pipeline.py 'C:\Users\Rodrigo Araya\Downloads\Refax_Contenido_COMPATIBILITIES_7343dc9b.xlsx'
```

Las rutas de imports del editor están configuradas en
`.vscode/settings.json`, mediante `python.analysis.extraPaths`. Se mantiene
el ajuste de `sys.path` en los scripts para ejecutarlos desde otras carpetas.
Los indicadores `M` y `U` del editor describen cambios pendientes en Git y
no afectan la ejecución; los diagnósticos de Python se comprobaron por separado.
