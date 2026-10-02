# Carga de descripciones por MLC

En **Sincronizacion de Procesos**, seleccionar el Excel, guardar el proceso y
pulsar **Ejecutar procesos**. El backend identifica este flujo mediante las
columnas `MLC` y `DESCRIPCION A` de `Hoja1`. Admite encabezados con espacios al
principio/final, diferencias de mayusculas y acentos. Requiere `.xlsx` y `Hoja1`;
las otras hojas no se procesan.

Cada fila conserva exactamente el texto de la celda, incluidos acentos,
comillas y saltos de linea. El archivo entregado
`DESCRIPCIONES MOTOR DE ARRANQUE CRUZADO.xlsx` tiene 312 MLC distintos y
312 descripciones no vacias de 1.352 a 1.420 caracteres. Sus encabezados tienen
espacios finales que el lector elimina. Las comillas dentro de sus celdas no
se eliminan ni se interpretan como instrucciones.

## Operaciones sobre Mercado Libre

La API distingue crear y reemplazar una descripcion:

1. `GET /items/{MLC}/description` consulta el texto actual.
2. Si devuelve 404, se crea con `POST /items/{MLC}/description`.
3. Si existe y difiere, se reemplaza con
   `PUT /items/{MLC}/description?api_version=2`.
4. Si ya coincide, no se realiza escritura. Para comparar se equiparan los
   saltos de linea CRLF/LF; el body conserva el texto original.

El body de POST y PUT es exclusivamente:

```json
{ "plain_text": "valor de DESCRIPCION A" }
```

Esta implementacion reemplaza descripciones existentes conforme a la opcion
elegida por el usuario. No usa POST sobre todas las filas: Mercado Libre
documenta que POST devuelve 400 si la descripcion ya existe.

Se confirma `plain_text` en la respuesta. Si la escritura devuelve solo
metadatos, se consulta de nuevo para comprobar el texto. Una respuesta sin
confirmacion se informa como error. Si se pierde la respuesta de una escritura
por timeout y el reintento POST devuelve 400, se lee de nuevo: si el texto ya
coincide, se recupera como exito sin volver a modificarlo.

## Rendimiento y frecuencia

- Lectura del Excel con openpyxl en modo `read_only`, sin convertir toda la
  hoja a un DataFrame. No se carga Hoja2.
- Bloques de 300 y hasta 4 filas concurrentes, sin pausa fija entre bloques.
- Un solo cliente HTTP para toda la cola, conexiones reutilizadas y timeout
  configurable existente de 30 segundos.
- Un unico responsable de reintentos (`ml_client`), con un maximo de seis
  intentos por solicitud, jitter, espera `Retry-After` y penalizacion Redis
  compartida ante 429. Cada intento real pasa por el limitador.
- Presupuesto inicial de **100 solicitudes/minuto para el endpoint de
  descripcion**, contando GET, POST, PUT, verificaciones y reintentos.
  Las escrituras tambien pasan por el presupuesto global de 240/minuto.
- Los duplicados con el mismo texto reutilizan un unico resultado. Los MLC
  duplicados con textos diferentes se rechazan antes de llamar a la API.
- Filas con MLC invalido o descripcion vacia se reportan sin llamar a la API.
- Progreso persistido cada 25 filas, al terminar y en los limites de bloque.

Los 100/minuto son una politica inicial configurable de esta aplicacion,
**no una cuota oficial garantizada ni un benchmark del endpoint**. La prueba
anterior de 240 PUT/minuto sobre `/items/{id}` no demuestra esa capacidad en
`/description`. Ninguna configuracion puede garantizar ausencia de 429 o
timeouts: se controlan y recuperan, y los errores persistentes quedan por fila.

Para 312 filas que necesitan escritura, dos solicitudes por fila implican
aproximadamente **6 min 14 s** de envio al presupuesto inicial. Si cada
escritura requiere una lectura adicional de confirmacion, son aproximadamente
**9 min 22 s**. Son estimaciones; no incluyen reintentos ni otra carga.
Los textos que ya coinciden necesitan solamente una consulta.

## Configuracion y despliegue

Los Compose local y de produccion aplican a API y todos los workers:

```dotenv
ML_ITEM_DESCRIPTION_REQUESTS_PER_SECOND=1.6666666666666667
ML_ITEM_DESCRIPTION_MAX_REQUESTS_PER_WINDOW=100
ML_ITEM_DESCRIPTION_WINDOW_SECONDS=60
ITEM_DESCRIPTION_CHUNK_SIZE=300
ITEM_DESCRIPTION_MAX_CONCURRENCY=4
```

En produccion los overrides pertenecen a `deploy/.env` del VPS. Los defaults
se activan con el deploy completo sin editar el `.env` privado del backend.
El chequeo `scripts.check_price_stock_config` incluye ahora el perfil de
descripciones y su uso del presupuesto global.
No hace falta una migracion SQL ni un nuevo worker. La cola existente ejecuta
`item_descriptions` en `worker_dispatch` y aplica la espera de **5 minutos
entre archivos completos** si hay otro pendiente.

## Resultados

Cada fila queda en el resultado JSON del job y en el Excel descargable desde
el proceso: `MLC`, `DESCRIPCION A`, `RESULTADO`, `DETALLE`, `FILA EXCEL`.
El Excel mantiene Hoja1 y las columnas de entrada, para poder corregir y
reprocesar filas. Los textos se escriben como texto de Excel, incluso si
empiezan con `=`. El resumen cuenta creadas, actualizadas, sin cambios,
duplicadas, exitosas y errores. Los errores parciales se muestran mediante el
modal existente; el listado de MLC con error sigue disponible.

La descarga de descripciones por `SKU-BUSQUEDA` conserva su comportamiento.
Las pausas y bloques de fotos, compatibilidades y precio/stock se conservan.

## Validacion realizada

- 134 pruebas de backend en Python 3.11, incluyendo creacion, reemplazo,
  saltos de linea, deduplicacion, 429, timeout despues de aplicar POST,
  exportacion de resultados y espera de 300 segundos entre archivos.
- Build del frontend aprobado.
- Configuracion Compose validada: los seis servicios del backend reciben
  el mismo perfil de descripciones y escritura global.
- Simulacion sin red con las 312 filas del archivo real: 156 creaciones,
  156 reemplazos, texto exacto verificado para cada MLC y 0 filas fallidas.
  Se inyectaron y recuperaron un 429 y un timeout: 627 solicitudes simuladas
  (313 GET, 157 POST, 157 PUT), con concurrencia maxima 4.
  El GET 404 que indica ausencia de descripcion no se cuenta como error;
  el POST 400 recuperado si queda registrado en las metricas HTTP.
- Evidencia local en
  `src/backend/compatibilties/uploads/benchmarks/item_descriptions_20261002/`:
  `validation_summary.json`, `resultado_simulacion_descripciones.xlsx`,
  el resultado JSON y el script de simulacion. Son archivos ignorados por Git.
- El Excel original permanecio intacto (SHA256
  `93c58089c2dfd4b5dcda03c0dd4a5a8afdfbb2ca005136f9b0fe94a7d5de1bff`).

Esta simulacion prueba el flujo y la recuperacion, **no mide el rendimiento
real de la API**. No se actualizaron publicaciones reales ni se desplego.

## Documentacion oficial consultada

- [Descripcion de productos: GET, POST, PUT y errores](https://developers.mercadolibre.cl/es_ar/publica-productos/descripcion-de-articulos).
- [Rate limit y error 429: cuota por aplicacion/endpoint, espaciado y reintentos](https://developers.mercadolibre.cl/es_ar/gestiona-ventas/rate-limit-error-429).
