# Procesamiento de fotografías y compatibilidades

El perfil elimina las pausas fijas entre bloques de fotografías,
compatibilidades y excepciones. La API y los cinco workers cargan la misma
configuración en ambos archivos Docker Compose. Las cargas directas, los
archivos resueltos y Sincronización de Procesos usan estos servicios.

| Ajuste | Fotografías | Compatibilidades y excepciones |
| --- | --- | --- |
| Filas por bloque | 300 | 300 |
| Concurrencia máxima | 4 | 4 |
| Pausa fija entre bloques | 0 s | 0 s |
| Presupuesto propio compartido entre workers | 100 solicitudes / 60 s | 100 solicitudes / 60 s |
| Timeout HTTP de escritura | 60 s | 60 s |
| Persistencia de progreso | Cada 25 filas y al terminar | Cada 25 lotes y al terminar cada bloque |

Los dos presupuestos propios también adquieren el presupuesto global,
configurado en Compose a 240 escrituras/minuto y compartido con precios,
stock, estado y descripciones. No son límites por worker. El POST de creación
de familias y el PUT de notas/restricciones consumen **dos solicitudes** del
presupuesto de compatibilidades. Las excepciones comparten ese presupuesto.
Las lecturas de publicaciones y del catálogo conservan el límite de lectura.

**100/minuto es una política inicial conservadora de la aplicación, no una
cuota oficial ni un resultado de una prueba de carga de estos endpoints.**
La validación previa de precios no demuestra la capacidad de procesar fotos
ni de crear compatibilidades a 240/minuto. Mercado Libre indica que los
límites dependen del recurso y la aplicación:
[Rate Limit / Error 429](https://developers.mercadolibre.cl/es_ar/publica-productos/rate-limit-error-429).

## Manejo de errores

- El cliente HTTP es el único responsable de los reintentos de estos flujos.
  Cada intento adquiere sus límites, incluidos los reintentos por red, 5xx,
  429 y renovación del token tras un 401. Las métricas cuentan intentos reales.
- Un 429 aplica una espera compartida al presupuesto propio y al global.
  Se respeta `Retry-After` en segundos o fecha HTTP; sin ese dato se deja
  drenar al menos la ventana de 60 segundos. La concurrencia no permite
  saltarse esa espera.
- Las esperas por el limitador ocurren antes de iniciar la solicitud HTTP,
  por lo que no consumen el timeout de red.
- Un error definitivo de una fila o lote queda en el resultado; las otras
  filas y bloques continúan. Los errores de validación o permisos requieren
  corregir el dato o la conexión, y no se ocultan como éxitos.
- Si la creación de familias fue confirmada y luego falla el PUT de sus
  notas/restricciones, se conserva la respuesta y el conteo ya creado. El
  lote queda con error para revisar la información pendiente.

No se puede garantizar que una API externa nunca devuelva 429 o timeouts.
Un fallo persistente, una credencial inválida o una imagen que Mercado Libre
no pueda descargar pueden agotar los intentos. Los resultados permiten
identificar los MLC pendientes.

## Lectura y reutilización

Los `.xlsx` se leen con `openpyxl` en modo de solo lectura, celda por celda,
cerrando siempre el libro. Se conserva `Hoja1` y la selección de la primera
hoja cuando no existe, junto con los aliases de columnas. Los lectores de
CSV y los formatos anteriores mantienen su ruta existente.

Las fotos idénticas repetidas para un MLC se envían una sola vez, conservando
un resultado por fila. Las URLs repetidas se eliminan sin cambiar su orden.
Un MLC con listas de fotos contradictorias queda con error de validación
antes de escribir; lo mismo ocurre con URLs sin esquema HTTP/HTTPS válido.

En compatibilidades, el detalle del ítem, los conteos de familias y las
resoluciones correctas se reutilizan dentro del job, incluso entre bloques.
Los accesos concurrentes al mismo dato comparten un bloqueo para evitar
consultas repetidas. Una compatibilidad ya aplicada correctamente en otro
bloque se reutiliza sin enviar otro POST/PUT ni volver a sumar su creación.
Los errores no se almacenan como éxitos. Los lotes del mismo destino se
ejecutan en orden; otros destinos pueden avanzar en paralelo. Se mantienen
los topes existentes de 10 familias y 200 productos coincidentes por petición.

## Configuración y activación

Las variables están en
[`deploy/price-stock-performance.env.example`](../deploy/price-stock-performance.env.example).
Compose establece los nuevos valores aunque el `.env` privado del backend
conserve los valores antiguos. Los ajustes de producción se pueden cambiar
en `deploy/.env`; no hace falta sustituir el archivo de credenciales.

Para activar los cambios hace falta reconstruir y recrear la API y los
workers mediante el despliegue habitual. El workflow existente ejecuta
`python -m scripts.check_price_stock_config` en los seis servicios. Ahora
verifica también fotografías, compatibilidades, lectura y excepciones,
incluyendo presupuestos, Redis, concurrencia, pausas y timeouts. Un perfil
distinto entre servicios hace fallar la verificación del despliegue.

La pausa de 300 segundos **entre archivos completos** de la cola se mantiene.
No se requiere migración SQL ni vaciar Redis. Python ejecutado directamente
conserva el presupuesto global que tenga su entorno; sin overrides es de
100/minuto.

## Validación

Se aprobaron 170 pruebas de backend, también al ejecutarlas sin `.env` ni
credenciales de la aplicación, y el build del frontend. Se verificó la
configuración efectiva de ambos Compose: los seis servicios cargan el mismo
perfil de fotografías y compatibilidades. Estos cambios están preparados
en el repositorio y no se han desplegado en producción.

Las pruebas de backend incluyen reintentos 429/timeout/503, agotamiento de
intentos, límites por intento, fechas HTTP en `Retry-After`, errores de
negocio, deduplicación, continuidad tras errores, orden de filas y lotes,
reutilización entre bloques, conteos de creación parcial y coherencia de
Compose. Se usan respuestas HTTP simuladas, sin modificar publicaciones.

También se comprobó con Redis real y dos clientes independientes una ventana
reducida de 2 solicitudes/segundo: 6 solicitudes terminaron en 2,125 segundos,
respetando el límite y la penalización compartida. Redis se ejecutó en un
contenedor temporal aislado y se retiró al terminar. Esta prueba valida la
coordinación local; no mide la cuota ni la latencia de Mercado Libre.
