# Compatibilidades de bandejas: MLC-VEHICLE_SUSPENSION_CONTROL_ARMS

El proceso de agregar compatibilidades reconoce esta familia y obtiene las
posiciones de cada fila mediante los encabezados
`DELANTERA/TRASERA/CONDUCTOR/ACOMPAÑANTE` e `IZQUIERDA/DERECHA`.

Los valores vacíos se omiten. Si la posición principal está vacía, se envía
únicamente el lado indicado, según la elección del usuario para este archivo.
Si el lado está vacío, se envía únicamente la posición principal. Si ambos
campos están vacíos o contienen valores no reconocidos, la fila obtiene el error
`INVALID_CONTROL_ARM_POSITION` antes de escribirla en Mercado Libre.

`IZQUIERDA/DERECHA` crea dos alternativas separadas en `POSITION.attribute_values`:
una con Izquierda y otra con Derecha. Se agrega a cada alternativa la posición
principal solo cuando la celda tiene un valor. Se ignoran diferencias de
mayúsculas y espacios alrededor de los valores o de `/`.

Las posiciones se incluyen tanto en el POST de creación de compatibilidades
como en el PUT que actualiza las restricciones y notas del producto de usuario.
La agrupación conserva las alternativas de todas las filas del mismo vehículo
y publicación, incluyendo posiciones aplicadas con éxito en bloques anteriores
del mismo trabajo.

## Verificación del archivo

Se revisó completo `RESULTADO MLC-BANDEJAS.xlsx`, hoja `Hoja1`: 40.669 filas y
1.311 publicaciones. Todas las filas tienen los datos vehiculares requeridos.
La validación local de posiciones obtuvo cero errores.

| Posición principal | Lado | Filas | Alternativas generadas por fila |
| --- | --- | ---: | --- |
| DELANTERA | DERECHA | 18.650 | Delantera + Derecha |
| DELANTERA | Vacío | 194 | Delantera |
| DELANTERA | IZQUIERDA | 19.457 | Delantera + Izquierda |
| DELANTERA | IZQUIERDA/DERECHA | 1.212 | Delantera + Izquierda; Delantera + Derecha |
| Vacío | DERECHA | 570 | Derecha |
| Vacío | IZQUIERDA | 516 | Izquierda |
| Vacío | IZQUIERDA/DERECHA | 70 | Izquierda; Derecha |

Hay 1.156 filas sin posición principal y 1.282 con ambos lados. En total, las
filas generan 41.951 alternativas de posición. Este total describe las
alternativas del archivo; la cantidad de compatibilidades creadas depende del
catálogo de vehículos de Mercado Libre al ejecutar el proceso.

El resultado de la validación y una muestra de cada patrón están en
[excel-positions-validation.json](../artifacts/control-arm-compatibilities/excel-positions-validation.json).

## Verificación con Mercado Libre

El 11 de octubre de 2026 se consultó con autenticación el recurso oficial
[valores de restricciones para bandejas en Chile](https://api.mercadolibre.com/catalog_compatibilities/restrictions/values?main_domain_id=MLC-CARS_AND_VANS_FOR_COMPATIBILITIES&secondary_domain_id=MLC-VEHICLE_SUSPENSION_CONTROL_ARMS).
Respondió HTTP 200 y confirmó los identificadores:

| Posición | value_id |
| --- | --- |
| Delantera | 13701104 |
| Trasera | 13701105 |
| Conductor | 13373175 |
| Acompañante | 13373176 |
| Izquierda | 2262158 |
| Derecha | 2262160 |

El catálogo describe Izquierda y Derecha como valores opuestos. La
[documentación oficial](https://developers.mercadolibre.cl/es_ar/atributos-y-variaciones/compatibilidades-entre-items-y-productos)
explica que `combined_values` proporciona combinaciones frecuentes; los valores
permitidos se enumeran en `values`.

También se consultaron las 1.311 publicaciones por GET en lotes de hasta 20:
todas pertenecen a `MLC-VEHICLE_SUSPENSION_CONTROL_ARMS`, categoría `MLC161582`,
y todas tienen `category_id` y `user_product_id` para el destino de escritura.
Se conservan las respuestas sin credenciales en
[mercadolibre-position-values.json](../artifacts/control-arm-compatibilities/mercadolibre-position-values.json)
y [mercadolibre-items-verification.json](../artifacts/control-arm-compatibilities/mercadolibre-items-verification.json).

## Pruebas

`tests/test_control_arm_compatibilities.py` cubre posiciones principales, lados
individuales y dobles, posición principal vacía, valores inválidos, agrupación
de filas y conservación entre bloques sin mezclar publicaciones. Reconstruye un
Excel con una fila real de cada uno de los siete patrones del archivo y verifica
el recorrido completo hasta los cuerpos HTTP de creación y actualización.

Desde `src/backend/compatibilties`:

```powershell
C:\RepnetPlatform1.0\.venv\Scripts\python.exe -m unittest discover -s tests -p '*compatibilit*.py' -q
```

Resultado de la validación conjunta: 57 pruebas aprobadas. Las respuestas de
escritura se simularon en las pruebas; las consultas reales fueron GET.
