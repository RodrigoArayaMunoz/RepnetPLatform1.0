# Diagnóstico de rendimiento de compatibilidades

Revisión: 28 de octubre de 2026, America/Santiago. Auditoría de lectura; sin cambios en producción, planes, colas ni datos. Se considera la aclaración del usuario: las notificaciones de Mercado Libre están desactivadas y está eliminando su historial. No se atribuye la demora actual a notificaciones activas.

## Resultado

Recomendación: mantener inicialmente el VPS de 8 vCPU y 24 GB; resolver las cuotas de Supabase mediante Pro y dimensionar su compute; optimizar y medir el importador. Pro con Small es un punto de partida razonable por aproximadamente US$30/mes para una organización con un solo proyecto, sin extras ni impuestos. Micro reduce ese total a aproximadamente US$25/mes. Son estimaciones de tarifas oficiales, no una garantía de tiempo de importación.

Hay límites y trabajo repetido concretos en el código que pueden explicar horas de procesamiento sin agotar el VPS. No fue posible establecer cuál de ellos domina esta ejecución ni confirmar una regresión específica: faltan métricas de la tarea y configuración efectiva del servidor. Supabase Free ya no cubre el consumo mostrado, pero esa insuficiencia no acredita la causa principal del tramo central de la importación.

## Evidencia disponible y límites

| Fuente | Observación | Alcance |
| --- | --- | --- |
| Captura OVH | 8 vCPU, 24 GB RAM, 200 GB, Beauharnois, Ubuntu 24.04 | Capacidad contratada; no utilización real |
| Captura importación | 37.414 / 69.676 filas, 54%, 50.175 compatibilidades, bloque 127/237 | Un instante; no demuestra si sigue avanzando |
| Captura Supabase | Free/Nano, hasta 0,5 GB RAM | Suscripción y compute mostrados |
| Uso del ciclo mostrado | Base 1,177 GB / 0,5 GB; egress 12,55 GB / 5 GB | Cuotas excedidas; no son tasas de carga |
| Captura Disk IO balance | 44% | Presupuesto restante mostrado, no porcentaje de ocupación del disco; no prueba agotamiento a cero |
| Metrics API, 16:52–16:53 Chile | HTTP 200; PostgreSQL disponible, unas 13 conexiones, RAM reportada 49,86%, base postgres 1.161.809.043 bytes | Muestra reportada por el endpoint; no permite extrapolar al día entero |
| REST últimos ocho procesos | HTTP 200, aproximadamente 0,47 s | Esa consulta respondió rápido en ese momento |
| SSH VPS | Clave disponible rechazada para ubuntu | No se midieron CPU/RAM/disco, colas, errores ni variables efectivas del VPS |
| GET estado de cola | HTTP 401 sin sesión de usuario | No se consultó el avance real de Redis |

Las tres respuestas de métricas entregaron contadores CPU/disco idénticos durante unos 56 segundos. No se calcularon tasas a partir de esos datos repetidos. No permiten afirmar CPU o IO a cero ni descartar picos.

El registro de la importación figura con fecha 28-10-2026 00:38:28. Esa es la fecha de carga mostrada; no se debe confundir con el comienzo efectivo de procesamiento. La fila conserva estado Pendiente en Supabase; el código guarda el avance en Redis y actualiza el estado final después, por lo que esa fila no demuestra que esté detenida. No se estimó una hora de término.

La evidencia actual de runtime se resume en `artifacts/compatibility-performance-2026-10-28/runtime-summary.json.local`. Las capturas de Metrics API están en `artifacts/supabase-diagnostico-2026-10-06/metrics_20261028T195257Z.local` y las dos muestras siguientes. El nombre del directorio es histórico; los archivos citados son del 28 de octubre. Las estadísticas del diagnóstico del 6 de octubre no se presentan como carga actual.

## Frenos encontrados en el código

1. **Lecturas ML limitadas y compartidas.** `src/backend/compatibilties/config.py:101` define 0,8 solicitudes/segundo. `services/compatibility_service.py:86`, `services/publication_sync_service.py:38` y `services/seller_sales_service.py:23` comparten el namespace Redis `ml:read`. Equivale a unas 48 lecturas/minuto agregadas. Es una política de la aplicación, no una cuota oficial demostrada de Mercado Libre. No se confirmó su valor efectivo en producción.
2. **El número de filas no equivale a solicitudes.** Se consultan publicaciones, familias, catálogo, copias y verificaciones. A 0,8 solicitudes/s, 20.000 lecturas requieren aproximadamente 6 h 57 min aun sin contar otras esperas. Es un ejemplo matemático, no el conteo medido de este archivo.
3. **Escrituras reguladas aparte.** `deploy/docker-compose.prod.yml:4` configura 240 escrituras/minuto globales y `:33` configura 100/minuto para compatibilidades. Más CPU o concurrencia no supera estos presupuestos. También deben comprobarse los overrides del servidor.
4. **Consultas negativas repetidas.** `services/compatibility_service.py:470` conserva conteos positivos entre 1 y 200; una familia con cero coincidencias puede repetir consulta y fallback para otros destinos. Una caché temporal de resultados negativos funcionales puede ahorrar llamadas. No se deben cachear timeouts, 429 o fallos de autenticación como si fueran ausencia de compatibilidad.
5. **Copia con coste de comprobación.** `services/compatibility_copy_service.py:117` consulta origen/destino y comprueba resultados para proteger posiciones y notas. Cuando pocos destinos son aptos para copiar, puede haber poco ahorro. Medir su utilidad sin quitar verificaciones de integridad.
6. **Tarea de duración muy larga.** La cola ejecuta directamente el trabajo completo. `celery_app.py:26` habilita acknowledgements tardíos y visibilidad de 12 horas. Si la tarea supera esa ventana, existe riesgo de redespacho. No se observó ni confirmó duplicación actual.
7. **Visibilidad insuficiente.** `services/compatibility_orchestrator_service.py:159` publica métricas al inicio y al final, sin actualizar todas las métricas de espera y llamadas por bloque. El porcentaje no permite separar lentitud normal, espera por límites, reintentos o un worker detenido.
8. **Historial consultado repetidamente.** `src/frontend/pages/MainSyncJobs.jsx:72` refresca cada cinco segundos durante ejecución y `:239` consulta el listado sin paginación de servidor. Son hasta 720 consultas/hora/navegador, además de consultas de resúmenes. Paginar visualmente no reduce ese tráfico.

El progreso frecuente se almacena en Redis (`services/job_store.py:9`). Supabase interviene al descargar el archivo, consultar pendientes y actualizar estados; no recibe una escritura por cada compatibilidad aplicada. Por eso mejorar Supabase beneficia estabilidad general, pero no elimina los límites ML.

## Estrategia propuesta

1. **Diagnóstico del proceso activo, antes de reiniciar.** Con acceso válido, comparar dos lecturas de Redis y comprobar tareas Celery activas, worker, último avance, 429/timeouts, configuración efectiva y consumo real de CPU/disco. Separar tiempo en cola, ejecución y esperas. La interfaz debe mostrar heartbeat y estado de espera.
2. **Supabase Pro con Small como punto inicial recomendado.** Small ofrece 2 GB RAM y mínimos sostenidos de 1.000 IOPS/22 MB/s frente a Nano con hasta 0,5 GB y 250 IOPS/5 MB/s. Son capacidades documentadas, no aceleraciones garantizadas. Confirmar explícitamente el compute después del cambio de plan: una instancia Nano existente puede permanecer Nano. El cambio de compute tiene una interrupción y debe coordinarse con el proceso activo.
3. **Reducir trabajo ML.** Resolver una combinación vehicular una vez y reutilizarla en las publicaciones aplicables, añadir caché negativa temporal, medir elegibilidad de copia y conservar las verificaciones de notas/posiciones. Evitar sincronizaciones masivas simultáneas que consuman el presupuesto compartido.
4. **Ajustar límites con medición.** Registrar solicitudes por endpoint, latencia, tiempo esperando el limitador, 429, timeouts y reintentos. Aumentar gradualmente lectura/concurrencia solo si las respuestas lo permiten, respetando Retry-After y el presupuesto global. No asumir que 48/min es un máximo impuesto por ML.
5. **Tareas cortas reanudables.** Crear unidades de trabajo de duración acotada, mantener juntas las operaciones del mismo destino, persistir checkpoints y resultados por bloque, y adquirir un bloqueo de ejecución. Una reanudación debe continuar lo pendiente sin repetir operaciones confirmadas. La durabilidad debe incluir recuperación tras reinicio del worker.
6. **Reducir tráfico auxiliar.** Refrescar solo procesos activos/cambiados; cargar historial y detalles al navegar; suspender polling de pestañas ocultas; reutilizar conexiones HTTP.
7. **Retención de datos definida.** Tras la limpieza actual, establecer qué eventos procesados se archivan y por cuánto tiempo. Si se reactivan webhooks en el futuro, corregir las consultas y escrituras redundantes antes de hacerlo. No se necesita mantenerlos activos para optimizar este importador.

Criterio de éxito: misma muestra representativa y resultados verificados, con menos solicitudes por combinación, menor tiempo total, reanudación sin duplicados y tasas de error conocidas. No se puede prometer pasar a minutos sin medir el número real de llamadas y la respuesta de ML.

## Efecto de borrar los datos de notificaciones

Desactivarlas reduce futuras entradas si la fuente quedó desactivada. No confirma por sí solo que no haya tareas previamente encoladas; eso requiere lectura de la cola.

Borrar filas con DELETE no reduce necesariamente el archivo físico inmediatamente. PostgreSQL conserva versiones antiguas hasta que pueden limpiarse; VACUUM normal permite reutilizar espacio y generalmente no lo devuelve al sistema operativo. VACUUM FULL reescribe y bloquea la tabla: no es una operación para ejecutar indiscriminadamente durante la carga actual.

La transferencia ya consumida no disminuye al borrar registros. La cuota de base de datos para Fair Use se evalúa con un promedio del ciclo, por lo que reducir el tamaño actual tampoco elimina inmediatamente ese promedio. Los valores Log Ingestion y Log Query no representan el tamaño de la tabla de notificaciones. La documentación consultada marca la facturación/restricción futura de estos medidores como todavía no activa; sus avisos no prueban la causa de esta demora. El aviso de restricción del 17 de noviembre es futuro respecto del día de esta revisión.

## Fuentes oficiales consultadas

- [Precios y créditos de compute](https://supabase.com/pricing): Pro US$25/mes, crédito de US$10, Micro aproximadamente US$10 y Small aproximadamente US$15 de compute.
- [Compute, IO y cambios de instancia](https://supabase.com/docs/guides/platform/compute-and-disk).
- [Tamaño, disco y promedio del ciclo](https://supabase.com/docs/guides/platform/database-size).
- [Transferencia acumulada del ciclo](https://supabase.com/docs/guides/platform/manage-your-usage/egress).
- [Uso de Log Ingest](https://supabase.com/docs/guides/platform/manage-your-usage/logs-ingest) y [Log Query](https://supabase.com/docs/guides/platform/manage-your-usage/logs-query).
- [PostgreSQL: limpieza y reutilización de espacio](https://www.postgresql.org/docs/current/routine-vacuuming.html).
- [Celery con Redis: visibility timeout](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html).
