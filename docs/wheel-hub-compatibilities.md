# Compatibilidades de mazas: MLC-VEHICLE_WHEEL_HUBS

El proceso de agregar compatibilidades reconoce esta familia desde la columna
`FAMILIA`. El lector de Excel ya traduce los encabezados originales a las columnas
lógicas `POSICION_DT` y `POSICION_ID`; la resolución conserva esos valores de cada
fila, incluso cuando reutiliza la búsqueda del mismo vehículo.

## Posiciones por fila

La columna `DELANTERA/TRASERA/CONDUCTOR/ACOMPAÑANTE` debe contener uno de los cuatro
valores de posición indicados. La columna `IZQUIERDA/DERECHA` es opcional:

| Posición principal | Lado | Combinaciones enviadas |
| --- | --- | --- |
| DELANTERA | Vacío | Delantera |
| DELANTERA | IZQUIERDA | Delantera + Izquierda |
| DELANTERA | DERECHA | Delantera + Derecha |
| DELANTERA | IZQUIERDA/DERECHA | Delantera + Izquierda; Delantera + Derecha |

La misma regla se aplica a Trasera, Conductor y Acompañante. Se ignoran diferencias
de mayúsculas y espacios alrededor de los valores y de `/`. Cuando el lado está
vacío, el payload omite completamente los valores Izquierda y Derecha.

Las dos posiciones laterales opuestas se envían como **alternativas separadas**
en `POSITION.attribute_values`; cada alternativa contiene su posición principal
y un solo lado. Por ejemplo:

```json
{
  "attribute_id": "POSITION",
  "attribute_values": [
    {"values": [
      {"value_id": "13701104", "value_name": "Delantera"},
      {"value_id": "2262158", "value_name": "Izquierda"}
    ]},
    {"values": [
      {"value_id": "13701104", "value_name": "Delantera"},
      {"value_id": "2262160", "value_name": "Derecha"}
    ]}
  ]
}
```

Las posiciones no reconocidas o una posición principal vacía producen el error
de fila `INVALID_WHEEL_HUB_POSITION` antes de escribir esa fila en Mercado Libre.
Las otras filas válidas continúan procesándose.

Si varias filas de esta familia corresponden a la misma publicación y al mismo
vehículo, se combinan sus alternativas sin repetirlas. Las posiciones aplicadas
con éxito se conservan durante el trabajo para incluirlas en los siguientes
bloques del mismo archivo. Un envío fallido no se guarda como aplicado.

El payload se utiliza tanto al crear las compatibilidades mediante
`POST /user-products/{id}/compatibilities` como al actualizar sus notas y
restricciones mediante `PUT` al mismo recurso.

## Verificación con Mercado Libre

El 10 de octubre de 2026 se consultó con autenticación el recurso oficial
[valores de restricciones para mazas en Chile](https://api.mercadolibre.com/catalog_compatibilities/restrictions/values?main_domain_id=MLC-CARS_AND_VANS_FOR_COMPATIBILITIES&secondary_domain_id=MLC-VEHICLE_WHEEL_HUBS).
Respondió HTTP 200 y confirmó estos identificadores:

| Posición | value_id |
| --- | --- |
| Delantera | 13701104 |
| Trasera | 13701105 |
| Conductor | 13373175 |
| Acompañante | 13373176 |
| Izquierda | 2262158 |
| Derecha | 2262160 |

Las dos publicaciones del Excel proporcionado, `MLC4557186208` y `MLC4557224882`,
también se consultaron por GET: ambas pertenecen a `MLC-VEHICLE_WHEEL_HUBS`, en la
categoría `MLC161586`, y tienen `user_product_id`.

La respuesta sin credenciales se conserva en
[mercadolibre-position-values.json](../artifacts/wheel-hubs-compatibilities/mercadolibre-position-values.json).
La lectura del Excel original produjo seis filas válidas: tres con Delantera
solamente y tres con dos alternativas, Delantera + Izquierda y Delantera + Derecha.
La vista previa está en
[excel-positions-preview.json](../artifacts/wheel-hubs-compatibilities/excel-positions-preview.json).

## Pruebas

`tests/test_wheel_hub_compatibilities.py` verifica los cuatro valores principales,
lados individuales, lados vacíos, ambas posiciones, valores inválidos, agrupación
por vehículo, continuidad entre bloques y fallos de actualización. También
reconstruye un Excel a partir de las seis filas del archivo proporcionado y prueba
el recorrido completo hasta los cuerpos POST y PUT, simulando las respuestas de
Mercado Libre.

Desde `src/backend/compatibilties`:

```powershell
C:\RepnetPlatform1.0\.venv\Scripts\python.exe -m unittest discover -s tests -p '*compatibilit*.py' -q
```

Resultado: 50 pruebas aprobadas. La verificación remota utilizó solo consultas
GET; los envíos de compatibilidades se comprobaron mediante simulación.
