# Pruebas de rendimiento de precios, estado y stock — 1 de octubre de 2026

Se probaron solicitudes reales `PUT /items/{MLC}` con la cuenta conectada y
los valores de la hoja `ML_CAMBIOS` del archivo
`resultado_actualizacion_precios-30-09-2026.xlsx`.

## Archivo y configuracion observada

- 15.117 filas y 15.117 MLC diferentes, sin filas invalidas ni MLC duplicados.
- 8.826 objetivos `active` y 6.291 objetivos `paused`.
- Las hojas Shopify, alertas y errores no se enviaron a Mercado Libre.
- Worker inspeccionado: bloques de 100, pausa fija de **120 segundos**,
  concurrencia 2, timeout de 30 segundos y presupuesto global de escritura
  de 100 solicitudes por 60 segundos. Este presupuesto estaba compartido por
  precios/stock, fotos y compatibilidades.
- No habia jobs activos ni solicitudes en las colas locales inspeccionadas al
  empezar. Esto no demuestra que otras integraciones de la misma app estuvieran
  inactivas.

## Prueba directa de subida y punto de rechazo

Cada publicacion se envio como maximo una vez, sin reintentos que ocultaran
errores. Se verifico el vendedor y se excluyeron estados no editables,
variaciones y publicaciones Full. El cuerpo incluyo precio, stock y estado
en una sola solicitud. Las conexiones HTTP se reutilizaron.

| Objetivo solicitudes/minuto | Solicitudes realizadas | HTTP 200 | HTTP 429 | Duracion de envio | Ritmo observado/minuto | Latencia p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 120 | 120 | 0 | 71,764 s | 100,33 | 401,5 ms |
| 150 | 180 | 180 | 0 | 72,030 s | 149,94 | 446,5 ms |
| 200 | 240 | 240 | 0 | 72,162 s | 199,55 | 260,1 ms |
| 300 | 360 | 360 | 0 | 72,619 s | 297,44 | 288,4 ms |
| 450 | 172 | 171 | 1 | 23,077 s | 447,21 | 450,5 ms |

El primer `429` ocurrio el 01/10/2026 a las 14:14:49, hora de Santiago, en
la solicitud 1.072, correspondiente a la fila Excel 1.262. La respuesta fue
`too_many_requests`, sin `Retry-After` ni headers de cuota. Al recibirla se
detuvieron los envios nuevos. No se siguio subiendo a 600, 900 o 1.200/minuto.
No se registraron timeouts, errores de negocio ni warnings en los PUT realizados.

En los 60 segundos anteriores a ese rechazo hubo 296 PUT de esta prueba.
Esto sugiere un limite o una carga compartida cercana a 300/minuto, pero **no
demuestra una cuota oficial de 300/minuto**, ni que el limite sea universal.
La etapa de 300/minuto duro poco mas de un minuto; tampoco demuestra capacidad
sostenible para todo un archivo.

## Validacion del perfil recomendado

Despues de la prueba de rechazo se usaron otras publicaciones del archivo.
Se enviaron **720 solicitudes a 240/minuto con concurrencia 4**, sin pausas
entre bloques, durante **180,606 segundos**. Todas devolvieron HTTP 200 y los
tres campos coincidentes con lo solicitado:

- Ritmo observado: 239,20 solicitudes/minuto.
- Latencia media: 239,6 ms; p95: 262,1 ms; maxima: 471,9 ms.
- 0 errores 429, timeouts, warnings o diferencias de precio/stock/estado.

Total de ambas pruebas: **1.792 PUT**, **1.791 respuestas 200 confirmadas** y
**1 respuesta 429**. Los valores objetivo de todas las publicaciones enviadas
ya coincidían con los obtenidos en el preflight. Fueron solicitudes idempotentes;
esta prueba no mide el costo de cambiar valores diferentes ni garantiza los
mismos tiempos con otros tipos de publicaciones o carga concurrente.

## Cambios implementados en el codigo

1. Solo precios/estado/stock usa bloques de 300, concurrencia 4 y pausa fija 0.
   Los bloques sirven para organizar el trabajo; la frecuencia se controla
   en cada solicitud, no mediante rafagas de 300 solicitudes simultaneas.
2. Los reintentos de este proceso tienen un solo responsable: `ml_client`.
   Cada intento HTTP adquiere el presupuesto compartido. Se evita multiplicar
   dos bucles de hasta seis intentos, y las metricas cuentan intentos reales,
   reintentos y todos los 429 recibidos.
3. Una ventana Redis llena espera solamente hasta que expire la solicitud mas
   antigua. Ya no agrega automaticamente 120 segundos de castigo por llegar al
   limite local. Los 429 reales siguen aplicando una penalizacion compartida;
   se respeta `Retry-After` y, si falta, se deja drenar al menos la ventana local.
4. El progreso se persiste cada 25 filas y al finalizar, en lugar de cada fila.
5. Un HTTP 200 que no confirme los valores solicitados queda como
   `UPDATE_NOT_CONFIRMED`, conservando respuesta y warnings para revisar.

Validacion del codigo: **115 pruebas de backend aprobadas**, incluyendo
reintentos 429, timeouts, orden de resultados, concurrencia, campos ignorados
y deteccion de configuraciones de despliegue inconsistentes. La suite tambien
se ejecuto en Python 3.11 sin .env, sin credenciales y sin acceso a la red,
como en CI. Se corrigieron dos fixtures que dependian del entorno local.
Una prueba con Redis real y dos clientes compartiendo una ventana de 2
solicitudes/segundo completo 6 solicitudes en 2,112 segundos, respetando la
ventana y la penalizacion, sin esperar los antiguos 120 segundos adicionales.

## Configuracion y despliegue

El despliegue de produccion activa automaticamente el perfil medido de
**240 solicitudes/minuto**, bloques de 300, concurrencia 4, pausa fija 0 y
progreso cada 25 filas. Estos valores se definen en una configuracion compartida
por la API y todos los workers de `deploy/docker-compose.prod.yml`.
El Compose local tambien incluye el mismo perfil.

**No hace falta editar el .env del backend en el VPS para activarlo.** Los
valores de `environment` de Compose prevalecen sobre el `.env` del backend,
incluso si ese archivo conserva el limite antiguo o la pausa de 120 segundos.
Las credenciales siguen cargandose desde el archivo privado existente.

Si se necesita ajustar el presupuesto o volver temporalmente a 100/minuto,
incorporar las variables de `deploy/price-stock-performance.env.example` en
`deploy/.env` **del VPS**, o exportarlas en la shell que ejecuta Compose.
El Compose local toma sus overrides del `.env` del backend o de la shell.
No reemplazar ni commitear archivos reales de secretos.

```dotenv
ML_WRITE_REQUESTS_PER_SECOND=4
ML_WRITE_MAX_REQUESTS_PER_WINDOW=240
ML_WRITE_WINDOW_SECONDS=60
ML_RETRY_429_COOLDOWN_SECONDS=60
PRICE_STOCK_CHUNK_SIZE=300
PRICE_STOCK_CHUNK_PAUSE_SECONDS=0
PRICE_STOCK_MAX_CONCURRENCY=4
JOB_PROGRESS_UPDATE_EVERY=25
PROCESS_QUEUE_DELAY_SECONDS=300
```

`ML_PRICE_STOCK_*` no controla por separado la frecuencia de este flujo:
`PRICE_STOCK_WRITE_RATE_LIMITER` sigue utilizando el presupuesto **global**.
La configuracion `ML_WRITE_*` afecta a los otros escritores que lo comparten.
Estos consumen parte de las 240 solicitudes/minuto; no son 240 por cada worker.
Todos los escritores deben usar el mismo Redis y el mismo presupuesto global.
No aumentar solamente bloques o concurrencia esperando superar ese presupuesto.
Python ejecutado directamente fuera de Compose conserva el default de
100/minuto salvo que se configure su entorno.

### Despliegue y verificaciones

El push a `calidad-revision-lc` dispara `.github/workflows/deploy-vps.yml`.
Antes del deploy se ejecutan todas las pruebas de backend y el build del
frontend. Esperar a que terminen los procesos de carga y actualizacion: el
deploy normal se bloquea si detecta la cola de procesos o la sincronizacion
de publicaciones activa. No resetear Redis para aplicar este cambio.
Para el workflow manual usar `notifications_only=false` y `reset_runtime=false`.

El deploy completo reconstruye y recrea API y workers, sin sustituir los
archivos privados de entorno. Despues ejecuta
`python -m scripts.check_price_stock_config` en los seis servicios del backend.
Esta comprobacion no llama a Mercado Libre ni imprime credenciales: verifica
que precios/stock usa el limitador global y que coincide con los ajustes
cargados. El workflow falla si algun worker carga un perfil distinto al de la
API. Los valores efectivos quedan visibles en el log del despliegue.

El worker que ejecuta precios/stock y la cola de procesos es `worker_dispatch`.
La cola espera **5 minutos (300 segundos)** entre archivos completos cuando
hay otro pendiente. Esa espera es independiente de las pausas entre bloques.
Los endpoints de carga directa y la cola de procesos usan el mismo servicio
optimizado. No se requiere una nueva migracion SQL para este cambio de
rendimiento. El script de benchmark solo envia actualizaciones al ejecutarlo
explicitamente con `--execute`; no se ejecuta durante el despliegue.

No se desplego ni se modifico el entorno persistente de produccion durante
estas pruebas. La activacion ocurre al desplegar los archivos actualizados.
En la primera carga revisar duracion, `ml_rate_limited`, `ml_retries` y errores:
la prueba de 720 solicitudes fue idempotente y no sustituye una ejecucion
completa con valores diferentes.

## Estimaciones para las 15.117 filas

| Escenario | Tiempo ideal estimado |
| --- | --- |
| Anterior: 100 por bloque, 120 s de pausa, 100 solicitudes/minuto | 7 h 33 min |
| Codigo optimizado, presupuesto global todavia a 100/minuto | 2 h 31 min |
| Perfil recomendado: bloques 300, pausa 0, presupuesto 240/minuto | 1 h 3 min |

La mejora estimada del perfil es **7,2 veces** respecto del escenario anterior.
Son estimaciones de envio: no incluyen lectura del Excel, otras cargas,
verificaciones adicionales ni reintentos. No se ejecuto la actualizacion
completa de las 15.117 filas durante el benchmark.

## Evidencia y repeticion

Los archivos quedan en
`src/backend/compatibilties/uploads/benchmarks/price_stock_20261001/`:

- `input_audit.json`: conteos y hash SHA-256 del archivo original.
- `summary.json` y `requests.jsonl`: prueba de subida hasta el primer 429.
- `validation/summary.json` y `validation/requests.jsonl`: validacion a 240/minuto.
- `resultado_pruebas_api.xlsx`: resumen y detalle por publicacion.

El script `src/backend/compatibilties/scripts/benchmark_price_stock.py` requiere
filas normalizadas en JSON, un directorio de salida nuevo y `--execute` para
efectuar PUT reales. Sin ese flag solo informa el plan. Siempre se detiene al
primer 429, timeout, error de autenticacion o servidor; tras varios errores de
negocio consecutivos tambien detiene la prueba.

## Documentacion oficial consultada

Mercado Libre indica que las cuotas dependen del endpoint y del Client ID,
y recomienda espaciar llamadas y reducir concurrencia ante 429:
[Rate Limit / Error 429](https://developers.mercadolibre.cl/es_ar/guia-para-servicios/rate-limit-error-429).
La cuota documentada de 100/minuto de
[stock Full/Flex](https://developers.mercadolibre.cl/convivencia-full-y-flex)
corresponde a otro recurso y no prueba un limite universal para `PUT /items`.

La API tambien documenta que, con automatizacion de precios activa, un PUT
puede rechazar o ignorar el precio. Por eso se verifican los campos devueltos y
los warnings, ademas del codigo HTTP:
[Precios de productos](https://developers.mercadolibre.cl/es_ar/servicio-sincroniza-publicaciones/api-de-precios).
