# Integración REFAX

La autenticación se ejecuta exclusivamente desde el backend. El navegador nunca
recibe el código, la clave ni el token de REFAX.

La tabla `refax_global_connection` guarda el token y las fechas de renovación;
no contiene el código del proveedor ni la clave necesarios para obtener un nuevo
token. El nombre `refax_global_connecction` no corresponde a la tabla existente.

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

## Registros de producción y TEST

Para preparar ambos ambientes, aplica la migración SQL de ambientes en el
SQL Editor de Supabase, después de la migración que crea la tabla.

La migración identifica el registro `id = 1` como `environment = 'production'`
y conserva su token, estado y fechas de autenticación. Agrega el registro
`id = 2` con `environment = 'test'`, `is_active = false` y sin token. Las
restricciones permiten como máximo esos dos registros, uno por ambiente.
Se puede repetir sin reiniciar una conexión TEST que ya esté configurada.

El backend usa `id = 1` para PRODUCCIÓN y `id = 2` para TEST. Cada ambiente
consulta, guarda y renueva únicamente su token. La migración de ambientes debe
estar aplicada antes de usar esta versión del backend.

## Conexión TEST

Configura estas variables exclusivamente en el `.env` del backend:

```dotenv
REFAX_TEST_API_BASE_URL=http://apitest.refax.com:8098
REFAX_TEST_PROVIDER_CODE=<codigo del proveedor TEST>
REFAX_TEST_API_KEY=<clave de REFAX TEST>
REFAX_TEST_COUNTRY_CODE=1
```

La tarjeta TEST consulta `GET /refax/test/status` y su botón Conectar solicita
`POST /refax/test/connect`. El backend envía un POST JSON a
`http://apitest.refax.com:8098/api/Autenticacion/GetToken` con `codigo`, `clave`
y `pais`. Guarda el token y sus fechas en el registro TEST y devuelve al navegador
solo el estado público de conexión. La tarjeta restaura ese estado al recargar.

TEST utiliza los mismos intervalos de vencimiento y renovación configurados para
REFAX, con su propia tarea de renovación. Si faltan las credenciales TEST, no se
utilizan las credenciales de PRODUCCIÓN.

## Configuración de producción (GitHub Actions / VPS)

La etapa `Validate backend tests` se ejecuta antes de configurar las credenciales
del VPS. Las pruebas usan endpoints, códigos y tokens ficticios, con las
peticiones HTTP simuladas, para validar producción y TEST sin depender del
`.env` local ni de secretos reales. La prueba de descarga con progreso cubre
ambos ambientes, con y sin `Content-Length`, y comprueba el contenido del Excel.

El `.env` local está excluido de Git y de la imagen Docker. Configura los secretos
`REFAX_PROVIDER_CODE` y `REFAX_API_KEY` en el repositorio de GitHub para que el
workflow `Deploy VPS` los transfiera al `.env` del backend en el VPS por la entrada
estándar de SSH. `REFAX_API_BASE_URL` y `REFAX_COUNTRY_CODE` también se pueden
configurar como secretos; si se omiten, se conserva la configuración del VPS.

Para habilitar TEST en el VPS, configura además los secretos
`REFAX_TEST_PROVIDER_CODE` y `REFAX_TEST_API_KEY`. Opcionalmente puedes configurar
`REFAX_TEST_API_BASE_URL` y `REFAX_TEST_COUNTRY_CODE`; sus valores predeterminados
son `http://apitest.refax.com:8098` y `1`. El mismo workflow transfiere estos
valores al backend. También permite actualizar solamente las credenciales TEST.

La actualización valida que código y clave estén presentes juntos, conserva las
otras variables y reemplaza el archivo de forma atómica con permisos privados.
Los valores no se incluyen en comandos ni en logs. Los despliegues de solo
notificaciones no modifican esta configuración.

Después de recrear los servicios, el workflow autentica REFAX, guarda el token
en Supabase y comprueba la descarga del Excel. Si REFAX o Supabase rechazan la
operación, el despliegue reporta el fallo en vez de informar una conexión exitosa.

Para verificar también la descarga desde el VPS:

```sh
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml \
  exec -T api python -m scripts.check_refax_connection --download-products
```

El comando solo informa el estado, el vencimiento y el tamaño del Excel;
no imprime credenciales ni productos.

Si la autenticación funciona localmente pero el VPS devuelve `ConnectTimeout`,
ejecuta el workflow en modo de diagnóstico, sin recrear servicios:

```sh
gh workflow run deploy-vps.yml --ref calidad-revision-lc \
  -f refax_diagnostics_only=true
```

El diagnóstico compara DNS y conexiones TCP/TLS en el contenedor y en el host,
prueba autenticación sin registrar credenciales y consulta reglas del firewall
si el usuario del VPS tiene permisos. Si ambos entornos agotan el tiempo de
conexión a `api.refax.com:443`, se debe revisar el acceso de red con el proveedor
del VPS y REFAX, incluyendo la autorización de la IP pública del servidor.

## Renovación

El backend intenta renovar el token al llegar a 7 horas y 45 minutos. Revisa el
estado cada minuto y también antes de consultar el estado o pedir un token para
otra operación. Si una renovación falla, reintenta en la siguiente revisión y
conserva el token anterior mientras no haya alcanzado las 8 horas.

## Descarga de productos

La tarjeta TEST habilita Descargar Productos solamente cuando su conexión está
activa. Solicita `GET /refax/test/products/download?progress=true` al backend,
que obtiene el token del registro TEST (`id = 2`) y consulta
`http://apitest.refax.com:8098/api/Productos/Listado?codigo=<codigo TEST>`
con `Authorization: Bearer <token TEST>`. El código proviene de
`REFAX_TEST_PROVIDER_CODE`.

TEST utiliza la misma generación de Excel, columnas, formatos, validaciones,
modal de progreso y reintentos que PRODUCCIÓN. El archivo se identifica como
`productos_refax_test_YYYYMMDD_HHMMSS.xlsx`. Si REFAX rechaza el token, se renueva
únicamente la conexión TEST antes de reintentar. La misma ruta sin `progress=true`
entrega el Excel directamente.

`GET /refax/products/download` obtiene un token vigente, consulta
`GET /api/Productos/Listado?codigo=<codigo>` con el esquema Bearer y entrega
un Excel `productos_refax_YYYYMMDD_HHMMSS.xlsx` con una hoja `Productos` y
las columnas `SKU` (`numero_refax`), `PRECIO`, `STOCK`, `NOMBRE_PRODUCTO`,
`GLOSA_1`, `GLOSA_2`, `GLOSA_3`, `FACTOR`, `MARCA_PRODUCTO`, `ORIGEN`,
`RUBRO_COMERCIAL`, `IMAGENES_URL`, `CODIGO_OEM`, `MARCA_OEM`, `CODIGO_FABRICA`,
`FABRICA`, `APLICACIONES`, `PRECIO_OFERTA_WEB` y `CANTIDAD_OFERTA_WEB`.
Cada fila corresponde a un producto del listado REFAX; los campos adicionales
ausentes o nulos quedan vacíos. Todas las columnas se guardan como texto real
con formato Texto (`@`).
El exportador acepta `aplicaciones` e `imagenes_url` de PRODUCCIÓN y sus variantes
`aplicacion` e `imagen_url` de TEST. Ambas se exportan en las columnas
`APLICACIONES` e `IMAGENES_URL`, respectivamente, conservando los valores completos.
Si el campo plural está ausente, es nulo, una cadena vacía o una lista vacía,
se utiliza el campo singular disponible.
SKU conserva sus ceros iniciales; PRECIO se escribe sin separadores de miles ni
separador decimal sobrante (`0`, `1500`, `19.95`). La disponibilidad textual de REFAX
(por ejemplo, `Disponible`) se conserva sin asignarle una cantidad inventada.
Todos los campos aceptan un valor individual o una lista. Las listas conservan
todos sus elementos y su orden, separados por saltos de línea en la misma celda;
esto incluye `CODIGO_OEM`, `CODIGO_FABRICA`, `FABRICA`, `APLICACIONES`,
`IMAGENES_URL` y los campos numéricos. Los objetos y listas anidados se conservan
como JSON. Los elementos nulos quedan vacíos y los ceros numéricos se conservan.
`APLICACIONES` conserva el texto completo si REFAX entrega una cadena. No se
parten descripciones por comas ni se descartan aplicaciones para mantener una
fila por producto. Las celdas tienen ajuste de texto. Si un campo supera los 32.767 caracteres
permitidos por Excel, la exportación informa el error en lugar de recortarlo.
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
