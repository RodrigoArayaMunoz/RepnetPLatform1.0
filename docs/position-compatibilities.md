# Posiciones por fila, independientes de FAMILIA

El proceso de agregar compatibilidades utiliza las columnas
`DELANTERA/TRASERA/CONDUCTOR/ACOMPAÑANTE` e `IZQUIERDA/DERECHA` de cada fila,
aunque `FAMILIA` esté vacía, tenga un nombre libre o un código de dominio.
Esta regla sustituye las condiciones anteriores por familia.

| Primera columna | Segunda columna | Posiciones enviadas |
| --- | --- | --- |
| Delantera, Trasera, Conductor o Acompañante | Vacía | Solamente la posición informada |
| Posición informada | IZQUIERDA | Posición principal + Izquierda |
| Posición informada | DERECHA | Posición principal + Derecha |
| Posición informada | IZQUIERDA/DERECHA | Posición principal + Izquierda; posición principal + Derecha |
| Vacía | IZQUIERDA | Izquierda |
| Vacía | DERECHA | Derecha |
| Vacía | IZQUIERDA/DERECHA | Izquierda; Derecha |
| Vacía | Vacía | Compatibilidad sin agregar restricciones de posición |

Se normalizan mayúsculas y espacios. Los dos lados opuestos se envían como
alternativas separadas en `POSITION.attribute_values`. No se generan lados
implícitos para pastillas de freno ni se omiten lados por familia.
Los valores desconocidos generan un error funcional de fila antes de escribir,
incluyendo familias vacías o desconocidas (`INVALID_POSITION`). Se conservan
los códigos de error específicos de las cuatro familias que ya los tenían.

Las alternativas se combinan sin duplicados por publicación y vehículo,
también entre bloques del mismo trabajo y al reutilizar una copia verificada.
Una fila vacía no aporta posiciones ni borra las informadas por otras filas.
En el flujo anterior por `product_id`, cada vehículo conserva sus posiciones;
ya no se reutilizan las posiciones de la primera fila para toda la publicación.

## Identificadores comprobados

| Posición | value_id |
| --- | --- |
| Delantera | 13701104 |
| Trasera | 13701105 |
| Conductor | 13373175 |
| Acompañante | 13373176 |
| Izquierda | 2262158 |
| Derecha | 2262160 |

Las consultas autenticadas a
`GET /catalog_compatibilities/restrictions/values` respondieron HTTP 200 para
los seis dominios de las 19 publicaciones de muestra: discos de freno,
pastillas, bandejas, bieletas, juntas homocinéticas y neblineros. Los seis IDs
y sus nombres coinciden en todos esos dominios. Se consultó el dominio real
de cada publicación, sin utilizar el valor de `FAMILIA` del archivo.

El formato de posiciones y combinaciones corresponde a la
[documentación oficial de Mercado Libre](https://developers.mercadolibre.cl/es_ar/atributos-y-variaciones/compatibilidades-entre-items-y-productos).
Las respuestas sin credenciales se conservan en
[mercadolibre-position-values.json](../artifacts/refax-compatibilities/mercadolibre-position-values.json).

## Revisión del archivo DOMINGO

Se revisó `Refax_Contenido_COMPATIBILITIES-DOMINGO.xlsx`, hoja `Hoja1`,
completo: **127.433 filas y 3.743 publicaciones**.

- 126.326 filas tienen `FAMILIA` vacía; 1.107 usan nombres libres.
- 127.295 filas tienen al menos una posición y 138 tienen ambas columnas vacías.
- Las 127.433 filas coinciden con las posiciones esperadas: **cero diferencias**.
- 19 filas reales representan todas las combinaciones de familia y posiciones
  presentes en el archivo. Su recorrido local completo verifica los cuerpos
  HTTP de creación y actualización, con respuestas simuladas.

El detalle de los once patrones, sus cantidades y las filas de muestra está en
[excel-positions-validation.json](../artifacts/refax-compatibilities/excel-positions-validation.json).

La auditoría completa y las consultas GET se pueden repetir desde
`src/backend/compatibilties`:

```powershell
C:\RepnetPlatform1.0\.venv\Scripts\python.exe C:\RepnetPlatform1.0\artifacts\refax-compatibilities\verify_positions.py 'C:\Users\Rodrigo Araya\Downloads\Refax_Contenido_COMPATIBILITIES-DOMINGO.xlsx' --check-ml
```

## Prueba real de creación y lectura posterior

Se procesaron **19 filas reales**, una por cada combinación de familia y
posiciones presente en el archivo. Las 19 publicaciones consultadas estaban
sin compatibilidades antes de la prueba y pertenecían a la cuenta conectada.
Se comprobó además que el Excel no contenía otras posiciones para esos mismos
vehículos y publicaciones que debieran incluirse en la muestra.

Las llamadas reales crearon **25 compatibilidades mediante 19 POST**. La
consulta GET posterior verificó las 25: posiciones y nota correctas, sin
diferencias. La fila 16.909 creó una compatibilidad sin posiciones; las filas
12.400 y 36.857 confirmaron posiciones laterales con la primera columna vacía.
También se verificaron familias vacías y los seis nombres libres del archivo.

Los cuerpos y las respuestas de Mercado Libre fueron reales. El script utiliza
la conexión vigente de Supabase y limitadores locales de llamadas, para no
depender del archivo de tokens ni del hostname Redis del contenedor al
ejecutarse desde Windows. La caché persistente y la copia se deshabilitaron
solamente en esta prueba de creación. El recorrido de actualización PUT se
verificó con las pruebas de integración simuladas.

El informe, con los estados anteriores y posteriores, está en
[live-sample-verification.json](../artifacts/refax-compatibilities/live-sample-verification.json).
[live_sample_test.py](../artifacts/refax-compatibilities/live_sample_test.py)
realiza solo la revisión previa por defecto; `--apply` habilita las creaciones
para los candidatos que todavía estén sin compatibilidades.

## Regresión y diagnósticos del editor

La suite completa pasó: **266 pruebas**. Las pruebas nuevas cubren los veinte
patrones de posiciones para doce variantes de familia, 60 filas de Excel hasta
POST y PUT, valores inválidos, agrupación, continuidad entre bloques y el flujo
anterior por IDs de producto. Las respuestas de esas pruebas son simuladas.

Se corrigieron nueve diagnósticos de acceso a una hoja potencialmente nula en
las pruebas anteriores y se completaron los tipos de parámetros que admiten
celdas vacías. `.vscode/settings.json` incluye las rutas de importación del
backend y del nuevo script. Pyright 1.1.414, con el intérprete configurado en
VS Code, verificó doce archivos con **cero errores y cero advertencias**.
El resultado está en
[pyright-verification.json](../artifacts/refax-compatibilities/pyright-verification.json).

La corrección está en el código local. El backend y el worker que procesen el
Excel deben ejecutar esta versión para aplicar la regla nueva.
