# Reducción de solicitudes y transferencia a Supabase

Fecha: 29 de octubre de 2026.

Los cambios están preparados en el repositorio local. Esta revisión no hace commit, push, cambios SQL ni despliegue, y no ha medido todavía el ahorro en producción.

## Qué se encontró

Las operaciones de compatibilidades, precio/estado/stock, fotografías, descripciones y excepciones se ejecutan en los workers del VPS contra Mercado Libre. En la ruta común de archivos, Supabase guarda el archivo y el registro del proceso; el worker descarga el archivo al comenzar y persiste estados. El avance se guarda en Redis. No hay una escritura a Supabase por cada fila de estos importadores.

El consumo evitable estaba también en las pantallas de seguimiento: el tablero descargaba el historial completo que devolvía Supabase cada cinco segundos durante una ejecución y cada treinta segundos en reposo. Los resúmenes de resultados se pedían para ese historial entero. Además, las exportaciones consultaban su estado cada segundo, y la consulta de sincronización de publicaciones verificaba repetidamente en Supabase si existían filas.

El retiro del webhook y del procesamiento de ventas es una corrección previa. No se reactivan en este cambio. Las llamadas de Mercado Libre al VPS y los logs locales de Docker no son, por sí mismos, transferencia ni ingestión de logs de Supabase.

## Cambios

| Parte | Comportamiento nuevo |
| --- | --- |
| Tablero de procesos | Tres filas por página y columna, con conteo total en Supabase y solo las columnas necesarias. El proceso activo se mantiene en ejecución aunque su estado persistido esté pendiente. |
| Historial del tablero | Refresco al iniciar/finalizar/cambiar de archivo, al guardar un archivo, al navegar páginas y al volver a la pestaña. Comprobación periódica cada 60 segundos durante una ejecución y 120 segundos en reposo. |
| Detalles de resultados | Solo se consultan los identificadores de la página visible de cada columna. |
| Avance de la cola | Continúa actualizándose desde la API/Redis cada cinco segundos mientras corre y treinta segundos en reposo; el avance de una fila no recarga el historial. |
| Seguimiento de procesos individuales | Precios/stock y no compatibilidades consultan cada cinco segundos. Se suspende la observación al ocultar la pestaña y se cancela al salir de la pantalla. El trabajo del servidor continúa. |
| Seguimiento de exportaciones | Cada tres segundos; sincronización de publicaciones cada cinco segundos mientras está activa. |
| Fallos del seguimiento | Una solicitud a la vez; espera progresiva con `Retry-After`; máximo de cinco fallos consecutivos. Los errores permanentes de sesión, cuota o ruta detienen el seguimiento. |
| Autenticación del backend | Las consultas simultáneas del mismo token comparten una verificación. Se conserva el TTL configurado para sesiones válidas, se cachean rechazos 10 segundos y se limita la caché a 2048 entradas. Tokens distintos siguen aislados. |
| Existencia de publicaciones | Caché de 60 segundos por proceso de API, con invalidación al cambiar la ejecución, el estado, la primera persistencia o la finalización de la sincronización. |
| Escrituras de procesos | Las actualizaciones de estado usan `return=minimal`; guardar un proceso no solicita la fila recién insertada. |

Las lecturas del tablero siguen usando el cliente y la sesión del usuario, sujetos a RLS. No se introduce un endpoint con privilegios de servicio para listar procesos. Las exportaciones explícitas mantienen la generación de resultados actuales. No se modifican los límites de solicitudes ni la lógica de escritura de compatibilidades, precios, stock, estado, fotografías, descripciones o excepciones en Mercado Libre.

Con una página estable durante una ejecución, el historial pasa de aproximadamente 12 lecturas amplias por minuto a 3–4 lecturas acotadas por minuto, sin contar transiciones o navegación manual. Se reciben como máximo nueve registros para las tres columnas. En reposo se hacen tres lecturas cada dos minutos. Estas cifras describen el código, no un porcentaje medido de ahorro de la factura completa. Siguen existiendo las solicitudes necesarias de autenticación, archivos y operaciones del negocio.

## Qué significan los contadores

- **Egress** mide bytes que salen de Supabase. Menos respuestas y respuestas más pequeñas reducen el consumo futuro. Borrar registros no deshace transferencias ya realizadas. [Documentación oficial](https://supabase.com/docs/guides/platform/manage-your-usage/egress).
- **Log Ingestion** acumula logs recibidos de los servicios de Supabase durante el ciclo. Reducir solicitudes repetidas reduce una fuente de esos logs; no se apaga el registro de errores ni de seguridad. No es el tamaño de una tabla de la aplicación que haya que vaciar. [Documentación oficial](https://supabase.com/docs/guides/platform/manage-your-usage/logs-ingest).
- **Log Query** mide los datos escaneados al consultar logs, no el número de consultas normales de la aplicación. Para diagnosticar, usar intervalos de 5–15 minutos y filtros antes de buscar; evitar consultas automáticas repetidas sobre todo el mes. [Documentación oficial](https://supabase.com/docs/guides/platform/manage-your-usage/logs-query).

Los acumulados de uso no son una medición de latencia. Para evaluar lentitud hay que separar tiempo en Mercado Libre, respuestas 429 y reintentos, consultas SQL, bloqueos y recursos del VPS/base de datos. Pro y Small aportan cuotas y recursos, pero no eliminan solicitudes innecesarias ni los límites de Mercado Libre.

## Verificación local

- 253 pruebas del backend satisfactorias, incluyendo pruebas nuevas de concurrencia de autenticación, cancelación, aislamiento de tokens, caché de publicaciones y respuestas mínimas al escribir.
- 27 pruebas de JavaScript en `tests/` satisfactorias, incluyendo paginación de un historial simulado de 10.001 procesos, recuperación de una página tras borrar registros, conservación del proceso activo, errores permanentes, `Retry-After`, suspensión por visibilidad y cancelación de seguimiento.
- Compilación de producción y ESLint sobre los archivos modificados satisfactorios. Vite mantiene el aviso de tamaño del bundle existente.
- Las pruebas del backend usaron un directorio temporal de archivos y configuración de prueba; no operaron sobre datos de producción.

## Despliegue y comprobación

1. Revisar el diff y comprobar que no hay procesos activos antes del despliegue general. Publicar estos cambios en `calidad-revision-lc` activa el workflow `Deploy VPS`; usar su modo general, porque hay cambios de frontend, API y código utilizado por workers. No usar el marcador de despliegue parcial `[retire-meli-sales]` para esta optimización.
2. Esperar el resultado satisfactorio de GitHub Actions y recargar la aplicación para recibir la versión nueva. Este cambio no requiere ejecutar migraciones, vaciar Redis, borrar archivos ni alterar las políticas de Supabase.
3. Abrir el tablero y verificar que los totales, páginas, proceso activo, detalles y descargas funcionan. En la pestaña Network del navegador, `/rest/v1/procesos` debe incluir límite/rango de hasta tres filas; no debe descargarse el historial con cada avance. Al ocultar la pestaña deben cesar las consultas periódicas de estas pantallas; puede terminar alguna solicitud ya enviada.
4. Durante un proceso real, comparar filas/minuto y duración con un archivo equivalente. Revisar por separado los errores de Mercado Libre y Supabase. No repetir una carga de escritura solo para medir si generaría cambios de negocio duplicados.
5. En Supabase, seleccionar este proyecto y un rango reciente. Confirmar que no se repite el antiguo `PATCH /rest/v1/meli_notification_events`. Si reaparece, identificar la instancia vieja que aún lo emite: no reactivar el webhook ni borrar tablas para ocultar el síntoma.
6. Anotar consumo antes y después de un intervalo comparable y nuevamente a las 24 horas. Comparar incremento de GB/hora, bytes por archivo y tasa de errores, teniendo en cuenta cantidad de usuarios, archivos y exportaciones. El panel puede tardar hasta una hora en reflejar el consumo; no se espera que el acumulado mensual disminuya al desplegar.

Las pantallas nuevas no observan el avance mientras están ocultas; los procesos continúan en el servidor. Los cambios hechos por otro usuario al historial pueden tardar hasta dos minutos en aparecer cuando la cola está en reposo. Volver a la pestaña fuerza una lectura actualizada. Tras un error permanente o cinco fallos de seguimiento, se requiere actualizar/reabrir la pantalla para volver a consultar.
