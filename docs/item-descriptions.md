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
   saltos de linea CRLF/LF y se ignoran solamente saltos de linea al final,
   que Mercado Libre elimina al devolver el texto guardado. Se conservan
   espacios y lineas vacias internas; el body conserva el texto original.

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
- Presupuesto de **400 solicitudes/minuto para el endpoint de
  descripcion**, contando GET, POST, PUT, verificaciones y reintentos.
  Las escrituras tambien pasan por el presupuesto global de 240/minuto.
- Los duplicados con el mismo texto reutilizan un unico resultado. Los MLC
  duplicados con textos diferentes se rechazan antes de llamar a la API.
- Filas con MLC invalido o descripcion vacia se reportan sin llamar a la API.
- Progreso persistido cada 25 filas, al terminar y en los limites de bloque.

Los 400/minuto son una politica configurable de esta aplicacion, inferior a
las 480 solicitudes GET/PUT por minuto probadas durante tres minutos sobre
`/description`; **no son una cuota oficial garantizada**. La prueba anterior
de precios/stock no se usa para inferir la cuota de descripciones.
Ninguna configuracion puede garantizar ausencia de 429 o
timeouts: se controlan y recuperan, y los errores persistentes quedan por fila.

Para 312 filas que necesitan escritura, dos solicitudes por fila implican
aproximadamente **1 min 34 s** de envio con el nuevo presupuesto, frente a
**6 min 14 s** con los 100/minuto anteriores. Si cada
escritura requiere una lectura adicional de confirmacion, son aproximadamente
**2 min 20 s**, frente a los **9 min 22 s** anteriores.
Son estimaciones; no incluyen reintentos ni otra carga.
Los textos que ya coinciden necesitan solamente una consulta.

## Configuracion y despliegue

Los Compose local y de produccion aplican a API y todos los workers:

```dotenv
ML_ITEM_DESCRIPTION_REQUESTS_PER_SECOND=6.666666666666667
ML_ITEM_DESCRIPTION_MAX_REQUESTS_PER_WINDOW=400
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
real de la API**. Las pruebas reales posteriores se detallan a continuacion.

## Pruebas reales del 2 de octubre de 2026

Se utilizo el mismo archivo y la cuenta conectada, verificando previamente el
vendedor de los 312 MLC. Los workers locales estaban sin tareas activas.
Las 312 consultas iniciales devolvieron 404: no existian descripciones.
Se enviaron los textos originales del Excel; las repeticiones reutilizaron
exactamente esos textos y pasaron a PUT despues de crear la descripcion.
No se cambiaron precios, estados, stock, fotos ni compatibilidades.

La prueba directa no uso reintentos ni el limitador de la aplicacion: espaciaba
cada solicitud y detenia los envios nuevos al primer error, dejando terminar
solamente las solicitudes ya iniciadas. Cada escritura exitosa confirmo el
texto en la propia respuesta.

| Objetivo escrituras/minuto | Solicitudes | HTTP 201 | HTTP 200 | HTTP 429 | Duracion | Latencia p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 100 | 100 | 0 | 0 | 59,685 s | 215,0 ms |
| 200 | 200 | 100 | 100 | 0 | 60,070 s | 211,7 ms |
| 300 | 300 | 100 | 200 | 0 | 60,330 s | 212,0 ms |
| 450 | 443 | 12 | 430 | 1 | 59,564 s | 257,9 ms |

Primer rechazo: `PUT /items/MLC2290659981/description`, fila Excel 131,
`2026-10-02T05:47:04.757591+00:00` (02:47:04 en Santiago),
`too_many_requests`, sin `Retry-After` ni headers de cuota. Hubo 446 escrituras
de esta prueba en los 60 segundos hasta ese rechazo, incluyendo el rechazado.
Esto **no establece una cuota oficial de 450/minuto**, ni demuestra que otras
integraciones de la misma aplicacion estuvieran inactivas.
No se continuaron las etapas previstas de 600, 900 o 1.200/minuto.

La subida realizo 1.043 escrituras: 312 creaciones (201), 730 reemplazos (200)
y un 429. Las 312 publicaciones recibieron su descripcion. No hubo timeouts.
Una escritura ya iniciada termino correctamente despues del primer rechazo.

Tras dejar pasar mas de 100 segundos, se probo durante **181,440 segundos** el
flujo mixto GET + PUT, con concurrencia 4, a un objetivo de **480 solicitudes
totales/minuto**: 720 consultas y 720 reemplazos, **1.440 respuestas 200**, cero
429, timeouts o textos diferentes. Ritmo observado: 476,19 solicitudes/minuto;
latencia media 199,8 ms, p95 220,1 ms y maxima 639,5 ms. Las escrituras de esta
validacion fueron idempotentes, puesto que los textos ya se habian aplicado.
La evidencia mide creacion en la primera prueba y reemplazos del mismo texto
en la segunda; no garantiza identicos tiempos al reemplazar textos diferentes.

Se eligieron **400 solicitudes totales/minuto** (16,7 % por debajo de las 480
probadas), con un maximo global compartido de 240 escrituras/minuto. Si cada
fila requiere GET y escritura, el ritmo previsto es 200 filas/minuto: hasta
cuatro veces el ritmo del presupuesto anterior, con una reduccion teorica del
75 % en el tiempo de envio. Si ya coincide el texto, basta un GET por fila.
Se mantienen las conexiones reutilizadas, los reintentos con espera y el
presupuesto Redis por intento. Los otros procesos conservan su configuracion
y los cinco minutos entre archivos completos.

Se ejecuto tambien el procesador real `process_item_description_job` con el
perfil final (400 solicitudes/minuto, escritura global 240): **312 filas
actualizadas en 95,419 segundos**, 624 solicitudes, ningun reintento, 429,
timeout ni error. Se comprobo la deteccion exclusiva `item_descriptions`, la
exportacion Excel y los dos bloques de 300 + 12 filas. La credencial de la
cuenta conectada se suministro en memoria; las llamadas HTTP y los limitadores
Redis fueron reales, sin cambiar los archivos privados de tokens.

Esta ejecucion revelo que GET omite el salto de linea final que POST/PUT
confirman en su respuesta. Se corrigio la comparacion para evitar volver a
escribir el mismo texto por esa normalizacion y para admitir confirmaciones
mediante GET. Se agregaron tres regresiones: omitir esa escritura, mantener
espacios/lineas internas y aceptar la verificacion tras una respuesta con
solo metadatos. La suite final tiene **137 pruebas aprobadas**, en Python 3.11
sin credenciales ni red. El payload y el archivo original permanecen intactos.

La recarga real despues de la correccion termino las **312 filas en 47,568
segundos**, con 312 GET, **cero escrituras**, 312 filas sin cambios y ningun
error o reintento. Confirma que las descripciones almacenadas coinciden con el
Excel salvo los saltos de linea finales normalizados por Mercado Libre.
Los dos Compose se validaron y sus seis servicios reciben el mismo perfil.

Las cuatro pruebas reportadas suman 3.731 solicitudes del endpoint de
descripciones y 2.075 escrituras: 312 POST correctos, 1.762 PUT correctos y el
unico PUT rechazado con 429. Este total no incluye las consultas adicionales
de identidad, vendedor o el GET usado para diagnosticar el salto final.

El codigo del benchmark reproducible esta en
`src/backend/compatibilties/scripts/benchmark_item_descriptions.py`.
Requiere `--execute`, acepta solamente filas validas y unicas, verifica cuenta
y vendedor, detecta si empieza otro proceso local y limita las etapas a
1.200 solicitudes/minuto y 300 segundos. Nunca se ejecuta durante CI/deploy.
`--mode mixed` fuerza tambien escrituras del mismo texto para medir la carga
GET + PUT; el procesador normal evita escribir textos que ya coinciden.
Los snapshots contienen datos de publicaciones y permanecen fuera de Git;
ningun informe almacena tokens.

Evidencia local ignorada por Git en
`src/backend/compatibilties/uploads/benchmarks/item_descriptions_live_20261002/`:
`ramp/summary.json`, `ramp/requests.jsonl`, `ramp/before.json`,
`mixed_validation/summary.json`, `mixed_validation/requests.jsonl`,
`application_validation/summary.json`, `reload_validation/summary.json`,
`benchmark_report.json`, `resultado_pruebas_descripciones.xlsx`
y el Excel con resultados por MLC. El original conserva su SHA256.
Los cambios de configuracion requieren deploy completo para llegar a los
workers existentes; estas pruebas no desplegaron ni reiniciaron servicios.

## Correccion de ejecuciones consecutivas: resultado 00348

El archivo `resultado_descripciones_mlc_00348.xlsx` contenia las 312 filas
del archivo de Videos, desde la fila Excel 2 hasta la 313. Habia 250 resultados
"Sin cambios" y 62 errores internos: cinco `Event loop is closed` y 57
`Lock ... is bound to a different event loop`. No eran rechazos 429 ni filas
omitidas por empezar a leer en la fila 75.

Las tareas Celery usaban `asyncio.run` por ejecucion. Esta funcion crea y
cierra un loop, mientras que los pools Redis de los limitadores permanecian
en variables de modulo. La siguiente tarea reutilizaba conexiones y locks
del loop cerrado. Se reprodujo con Redis real: primera ejecucion correcta,
segunda con `Event loop is closed`.

Todas las entradas sincronas de tareas usan ahora
`services.worker_async_runner.run_worker_coroutine`: un `asyncio.Runner`
creado de forma perezosa por proceso prefork, con el mismo loop durante la
vida del worker, contexto separado para cada tarea y cierre al finalizar el
worker. Esto permite alternar cola, precios, fotos y compatibilidades sin
reintroducir el mismo problema. Se conservan rutas de tareas, colas,
concurrencia, presupuestos Redis, pausas y espera entre archivos.

La cola limpia `current_job_id` al empezar otro archivo y durante la espera.
El job de descripciones inicializa sus contadores en cero. La pantalla indica
"filas procesadas" y limpia los contadores al iniciar una nueva cola.
El avance se publica cada 25 filas y se consulta cada cinco segundos; ver 75
en la primera consulta significa que ya terminaron 75 filas, no que se omitan
las primeras 74. Los resultados conservan el orden y la fila original del Excel.

La validacion anterior lanzaba cada prueba en un proceso independiente, por
lo que no cubria la reutilizacion entre dos tareas en el mismo worker.
Se agregaron regresiones para ese caso, para alternar distintos tipos de
tarea, para ejecutar otra tarea despues de un error y para separar contextos.
Una prueba de Redis real con tres ejecuciones y cuatro adquisiciones
concurrentes por ejecucion finalizo sin errores en el mismo loop.
La suite completa finalizo con **142 pruebas aprobadas** en Python 3.11,
sin credenciales ni red, y el build del frontend paso.

Evidencia local en
`src/backend/compatibilties/uploads/benchmarks/description_failure_00348/`:
entrada y resultado originales copiados, `validate_repeated_tasks.py`,
`repeated_tasks_summary.json`, resultados JSON y `corrected_run_1.xlsx` /
`corrected_run_2.xlsx`. El script ejecuta dos entradas reales de la tarea
Celery en el mismo proceso, con credenciales del token store, Redis y HTTP
reales; sustituye solamente la seleccion de pendientes y las escrituras del
estado de la cola para no alterar los registros de procesos existentes.

Con el archivo real de Videos (312 filas, SHA256
`67ceae610afc571ee63a9a0708e2c8239197349f7d44cb4d06eaa147ff0c9fd8`),
la primera tarea finalizo en **57,427 segundos**: 62 descripciones actualizadas,
250 ya coincidentes, 374 llamadas (312 GET + 62 PUT) y cero errores. La segunda
tarea, en el mismo proceso y loop, finalizo en **48,042 segundos**: 312 filas
sin cambios, 312 GET, cero escrituras y cero errores. No hubo reintentos, 429
ni timeouts en ninguna. Todas las filas originales 2..313 y sus textos se
verificaron en orden. Se repararon efectivamente las 62 publicaciones que
habian fallado en el resultado 00348.

La correccion requiere un **deploy completo que reconstruya y recree los
workers**: los procesos ya iniciados conservan el codigo importado. Volver a
cargar el Excel despues del deploy genera un resultado nuevo; los reportes
historicos como 00348 conservan los errores de su ejecucion original.

Referencias de implementacion:
[Runner y ciclo de vida de asyncio.run](https://docs.python.org/3.11/library/asyncio-runner.html).

## Documentacion oficial consultada

- [Descripcion de productos: GET, POST, PUT y errores](https://developers.mercadolibre.cl/es_ar/publica-productos/descripcion-de-articulos).
- [Rate limit y error 429: cuota por aplicacion/endpoint, espaciado y reintentos](https://developers.mercadolibre.cl/es_ar/gestiona-ventas/rate-limit-error-429).
