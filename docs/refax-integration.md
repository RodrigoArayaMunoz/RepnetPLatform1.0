# Integración REFAX

La autenticación se ejecuta exclusivamente desde el backend. El navegador nunca
recibe el código, la clave ni el token de REFAX.

## Configuración del backend

Agrega estas variables al entorno donde se ejecuta FastAPI:

```dotenv
REFAX_API_BASE_URL=https://api.refax.com
REFAX_PROVIDER_CODE=<codigo del proveedor>
REFAX_API_KEY=<clave de REFAX>
REFAX_COUNTRY_CODE=1
```

Los valores opcionales usan estos valores predeterminados:

```dotenv
REFAX_TOKEN_LIFETIME_SECONDS=28800
REFAX_TOKEN_REFRESH_AFTER_SECONDS=27900
REFAX_REFRESH_CHECK_INTERVAL_SECONDS=60
REFAX_HTTP_TIMEOUT_SECONDS=30
REFAX_PRODUCTS_HTTP_TIMEOUT_SECONDS=120
SUPABASE_REFAX_CONNECTION_TABLE=refax_global_connection
```

Aplica la migración `202609290001_create_refax_global_connection.sql` antes de
usar el botón. La tabla tiene RLS habilitado, no entrega permisos a `anon` ni a
`authenticated` y solo es accedida por el backend mediante `service_role`.

## Renovación

El backend intenta renovar el token al llegar a 7 horas y 45 minutos. Revisa el
estado cada minuto y también antes de consultar el estado o pedir un token para
otra operación. Si una renovación falla, reintenta en la siguiente revisión y
conserva el token anterior mientras no haya alcanzado las 8 horas.

## Descarga de productos

`GET /refax/products/download` obtiene un token vigente, consulta
`GET /api/Productos/Listado?codigo=<codigo>` con el esquema Bearer y entrega
un Excel `productos_refax_YYYYMMDD_HHMMSS.xlsx` con una hoja `Productos` y
únicamente las columnas `SKU` (`numero_refax`), `PRECIO` (`precio`) y `STOCK`
(`stock`). Las tres columnas se guardan como texto real con formato Texto (`@`).
SKU conserva sus ceros iniciales; PRECIO se escribe sin separadores de miles ni
separador decimal sobrante (`0`, `1500`, `19.95`). La disponibilidad textual de REFAX
(por ejemplo, `Disponible`) se conserva sin asignarle una cantidad inventada.
El archivo incluye la regla OOXML `numberStoredAsText` para omitir esa advertencia
únicamente en las celdas exportadas, sin cambiar otras comprobaciones de Excel.
El token nunca se envía al navegador.

La consulta GET reintenta hasta tres veces ante cortes de comunicación, timeouts
y respuestas HTTP temporales. Los logs registran el tipo de error y el intento,
sin incluir credenciales, tokens ni cuerpos de respuesta. Una respuesta inválida
o sin las columnas requeridas produce un error visible, nunca un falso Excel.

Durante la exportación, un diálogo modal bloquea la interacción con el fondo.
El frontend solicita `GET /refax/products/download?progress=true`, que devuelve
una secuencia NDJSON autenticada con eventos de progreso y el archivo en bloques
base64. No se crea una segunda consulta a REFAX ni se requieren tablas nuevas.
La descarga directa sin `progress=true` sigue entregando un adjunto XLSX.

El porcentaje representa etapas completadas: conexión (0–5 %), recepción del
catálogo (10–58 %), validación (60 %), filas del Excel (60–90 %), guardado y
formato (90–95 %) y recepción del archivo en el navegador (95–100 %).
Si REFAX no declara `Content-Length`, se muestran los MB recibidos mientras la
etapa se mantiene en 10 %; no se inventa un total ni se aumenta por temporizador.
La etapa de generación cuenta las filas realmente incorporadas al Excel.
Solo se alcanza 100 % al recibir todos los bloques y la confirmación final.
Un error o una transferencia incompleta descarta el archivo y libera el fondo;
cerrar la petición cancela su tarea y la creación del Excel cooperativamente.
