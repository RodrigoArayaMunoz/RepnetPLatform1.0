# Verificación de amortiguadores: MLC-VEHICLE_SHOCK_ABSORBERS

Se analizó `RESULTADO MLC-AMORTIGUADORES.xlsx`, hoja `Hoja1`, el 11 de octubre de
2026. El archivo contiene 42.870 filas y 1.480 publicaciones. Todas las filas
pertenecen a esta familia y tienen los valores vehiculares requeridos.

## Resultado de la corrección

Cada fila con `FAMILIA = MLC-VEHICLE_SHOCK_ABSORBERS` utiliza las dos columnas
de posición de forma independiente. La posición principal se incluye solamente
cuando está informada; el lado se incluye solamente cuando está informado.
`IZQUIERDA/DERECHA` genera dos combinaciones alternativas, una por cada lado.

Las 42.870 filas producen ahora las posiciones indicadas en el Excel, sin
diferencias:

| Posición principal | Lado | Filas | Posiciones enviadas |
| --- | --- | ---: | --- |
| DELANTERA | IZQUIERDA/DERECHA | 22.208 | Delantera + Izquierda; Delantera + Derecha |
| TRASERA | IZQUIERDA/DERECHA | 20.501 | Trasera + Izquierda; Trasera + Derecha |
| Vacío | IZQUIERDA/DERECHA | 161 | Izquierda; Derecha |

La regla anterior compartía una rama con pastillas de freno y omitía todas las
restricciones cuando faltaba la posición principal. Las 161 filas corregidas
pertenecen a cinco publicaciones:

- `MLC4557226464`
- `MLC4557239556`
- `MLC4557239580`
- `MLC4557291744`
- `MLC4557291764`

El resultado de la verificación y los cuerpos HTTP simulados están en
[excel-positions-validation.json](../artifacts/shock-absorber-compatibilities/excel-positions-validation.json).

## Casos admitidos

| Primera columna | Segunda columna | Resultado |
| --- | --- | --- |
| Posición informada | IZQUIERDA | Posición principal + Izquierda |
| Posición informada | DERECHA | Posición principal + Derecha |
| Posición informada | IZQUIERDA/DERECHA | Posición principal + Izquierda; posición principal + Derecha |
| Posición informada | Vacío | Solamente la posición principal |
| Vacío | IZQUIERDA | Solamente Izquierda |
| Vacío | DERECHA | Solamente Derecha |
| Vacío | IZQUIERDA/DERECHA | Izquierda; Derecha |
| Vacío | Vacío | Error de fila `INVALID_SHOCK_ABSORBER_POSITION`, sin escritura |

La primera columna admite Delantera, Trasera, Conductor y Acompañante. Se
normalizan mayúsculas y espacios. Los valores desconocidos producen un error
de fila antes de escribir, sin bloquear las filas válidas.

Las posiciones de filas del mismo vehículo y publicación se combinan sin
duplicarlas, incluso entre bloques del mismo proceso. Se utiliza el mismo
criterio que para bandejas; las reglas de otras familias conservan su comportamiento.

## Identificadores confirmados

Se consultó con autenticación el recurso oficial de Mercado Libre para
[posiciones de amortiguadores en Chile](https://api.mercadolibre.com/catalog_compatibilities/restrictions/values?main_domain_id=MLC-CARS_AND_VANS_FOR_COMPATIBILITIES&secondary_domain_id=MLC-VEHICLE_SHOCK_ABSORBERS),
que respondió HTTP 200:

| Posición utilizada por el Excel | value_id |
| --- | --- |
| Delantera | 13701104 |
| Trasera | 13701105 |
| Conductor | 13373175 |
| Acompañante | 13373176 |
| Izquierda | 2262158 |
| Derecha | 2262160 |

Los seis IDs y sus nombres coinciden con la respuesta oficial guardada el
11 de octubre de 2026. La verificación local utiliza esa respuesta guardada;
el script no realiza consultas ni escrituras reales.

La consulta por GET de las 1.480 publicaciones confirmó que todas pertenecen a
`MLC-VEHICLE_SHOCK_ABSORBERS`, categoría `MLC22579`, y que todas tienen
`category_id` y `user_product_id` para el destino de escritura.

Las respuestas sin credenciales se conservan en
[mercadolibre-position-values.json](../artifacts/shock-absorber-compatibilities/mercadolibre-position-values.json)
y [mercadolibre-items-verification.json](../artifacts/shock-absorber-compatibilities/mercadolibre-items-verification.json).

## Comprobación de los envíos

Se revisaron las posiciones de las 42.870 filas y se ejecutó el recorrido local
completo con una fila real de cada uno de los tres patrones del Excel. Las
respuestas de catálogo y escritura se simularon, y se inspeccionaron los tres
cuerpos HTTP generados: un POST de creación por muestra con nota y posiciones.

Los tres envíos incluyen las posiciones esperadas, también en la muestra con
posición principal vacía. Los IDs esperados se comparan de forma independiente
con los cuerpos HTTP, sin reutilizar `build_restrictions` como resultado esperado.

La prueba de integración adicional recorre los 19 patrones válidos: cuatro
posiciones principales con cuatro variantes de lado, más tres variantes de
lado sin posición principal. Se verifican los 19 cuerpos POST, los IDs,
los nombres y la lectura de los encabezados originales del Excel. También se
prueban errores de fila, posiciones repetidas y conservación entre bloques.
La suite completa del backend pasó: **253 pruebas**.

La auditoría reproducible de posiciones utiliza respuestas GET guardadas y
escrituras simuladas: **cero escrituras reales en esa auditoría**. La
[optimización posterior](compatibility-optimization.md) también fue comprobada
con creaciones, copia y lectura posterior reales: 9 compatibilidades en 6
publicaciones. Se conservaron las notas y posiciones esperadas con un POST
para creación nueva; los existentes usan PUT cuando requieren actualización.
La corrección debe estar instalada en el backend que procese el archivo.
El script reproducible es
[verify_positions.py](../artifacts/shock-absorber-compatibilities/verify_positions.py):

```powershell
C:\RepnetPlatform1.0\.venv\Scripts\python.exe C:\RepnetPlatform1.0\artifacts\shock-absorber-compatibilities\verify_positions.py 'C:\Users\Rodrigo Araya\Downloads\RESULTADO MLC-AMORTIGUADORES.xlsx'
```

## Advertencias de VS Code

Las cuatro advertencias de imports de `verify_positions.py` se resuelven
agregando `src/backend/compatibilties` a `python.analysis.extraPaths` en
`.vscode/settings.json`. Se mantiene el ajuste de `sys.path` del script para
poder ejecutarlo directamente desde cualquier carpeta.

La verificación con Pyright 1.1.414, usando las mismas rutas de análisis y el
intérprete configurado en VS Code, analizó `verify_positions.py` con **cero
errores y cero advertencias**.

El indicador `U` de VS Code significa que el archivo aún no está versionado
en Git; no es un diagnóstico de Python ni un error de funcionalidad.
