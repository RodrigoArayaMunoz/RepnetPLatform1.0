# Despliegue de notificaciones de Mercado Libre

## 1. Seguridad previa

El Client Secret que aparecio en una captura debe rotarse en Dev Center. Despues
de rotarlo, actualizar `ML_CLIENT_SECRET` en
`src/backend/compatibilties/.env` antes de desplegar.

No guardar ni publicar el Client Secret en documentacion, capturas o commits.

## 2. Base de datos

Ejecutar en Supabase SQL Editor, una sola vez, el contenido de:

`supabase/migrations/202608280001_create_meli_sales_webhooks.sql`

Ejecutar tambien la migracion que permite actualizar `publicaciones_ml` desde
las notificaciones `orders_v2` sin alterar el control de las cargas completas:

`supabase/migrations/202609260001_upsert_publicaciones_ml_from_orders.sql`

Para guardar el numero de pieza de cada publicacion, aplicar tambien antes de
desplegar el backend y sus workers:

`supabase/migrations/202609300001_add_part_number_to_publicaciones_ml.sql`

El nuevo campo `part_number` es texto y se obtiene de
`attributes[id=PART_NUMBER].value_name` en `/items`. Las filas antiguas quedan
con `NULL` hasta que llegue una notificacion `items` o se ejecute una nueva
carga de publicaciones.

La migracion crea las tablas de eventos, packs, ordenes, lineas/SKU, envios y
relaciones. Todas tienen RLS habilitado y solo el backend con
`SUPABASE_SERVICE_ROLE_KEY` puede acceder.

## 3. Variables del backend

Verificar en `src/backend/compatibilties/.env`:

```dotenv
FRONTEND_URL=https://repnet.online
ML_CLIENT_ID=<APP_ID_DE_DEV_CENTER>
ML_CLIENT_SECRET=<NUEVO_CLIENT_SECRET_ROTADO>
ML_REDIRECT_URI=https://repnet.online/api/auth/callback
ML_NOTIFICATIONS_ENABLED=true
ML_NOTIFICATION_APPLICATION_ID=<APP_ID_DE_DEV_CENTER>
ML_NOTIFICATION_ALLOWED_USER_ID=<ID_DEL_VENDEDOR_CONECTADO>
SUPABASE_URL=<URL_DEL_PROYECTO>
SUPABASE_SERVICE_ROLE_KEY=<SERVICE_ROLE_KEY>
```

## 4. Despliegue

Desde la raiz del repositorio en el servidor:

```powershell
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml up -d --build api worker_meli_notifications frontend
```

Comprobar el receptor publico:

```powershell
Invoke-RestMethod https://repnet.online/api/webhooks/mercadolibre/health
```

Debe responder:

```json
{"ok": true}
```

Revisar el worker:

```powershell
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml logs --tail 100 worker_meli_notifications
```

## 5. Dev Center

Configurar:

- Redirect URI: `https://repnet.online/api/auth/callback`
- Notifications callbacks URL: `https://repnet.online/api/webhooks/mercadolibre`
- OAuth: Authorization Code y Refresh Token
- Negocio: Mercado Libre
- Permiso Venta y envios de un producto: Lectura
- Topico Orders: `Orders_v2`
- Topico Shipments: `Shipments`
- Topico Items: `Items`

No activar `Flex-Handshakes` para la pantalla de ventas. El worker identifica
Flex con `logistic_type = self_service` en el recurso shipment.

Aceptar los terminos, completar el CAPTCHA y guardar con Editar.

## 6. Sincronizacion inicial y operacion

La pantalla Gestion de Ventas lee Supabase al abrirse. El boton de actualizar
ejecuta una sincronizacion manual del dia a traves de la cola y luego vuelve a
leer Supabase. Esto permite cargar las ordenes existentes del dia sin esperar
una nueva notificacion.

Despues de activar Dev Center:

1. Crear o modificar una publicacion y hacer una venta u orden de prueba.
2. Verificar una fila `processed` en `meli_notification_events`.
3. Verificar la orden y SKU en `meli_orders` y `meli_order_items`.
4. Verificar el evento `items` y el MLC, SKU, titulo y fecha actualizados en
   `publicaciones_ml`.
5. Verificar `shipping_type = flex` para envios `self_service` o `normal` para
   las demas modalidades.
6. Abrir Gestion de Ventas y confirmar SKU y cantidad.

Si un evento queda `failed`, revisar `processing_error` y los logs del worker.
Celery reintenta automaticamente con espera incremental.

## 7. Numero de pieza en publicaciones nuevas

El evento `items` contiene el recurso, no la ficha completa. El worker consulta
`GET /items/{MLC}?attributes=id,title,attributes,date_created,seller_id`, comprueba
el vendedor y toma `attributes[id=PART_NUMBER].value_name` como texto, conservando
los ceros iniciales. `value_id` identifica el valor en el catalogo y no debe
guardarse como numero de pieza.

El guardado usa `upsert_publicaciones_ml_incremental`. Cuando ML entrega un
numero de pieza, el worker vuelve a leer ese campo de Supabase y verifica que
coincida antes de marcar la notificacion como `processed`. Un error o un valor
diferente hace fallar el intento y activa los reintentos del worker. Si ML no
entrega `PART_NUMBER`, la publicacion sigue siendo valida; la funcion SQL
conserva el valor anterior si ya existia.

La documentacion oficial describe las
[notificaciones items](https://developers.mercadolibre.cl/es_ar/manejo-de-envios/productos-recibe-notificaciones)
y el atributo
[PART_NUMBER / Numero de pieza](https://developers.mercadolibre.cl/es_ar/gestiona-ventas/buscador-de-productos).

### Desplegar solamente este flujo

El workflow `Deploy VPS` bloquea el despliegue general cuando hay procesos
activos. Un push puede terminar con el despliegue fallido y dejar en produccion
el worker anterior, aunque Supabase ya tenga la columna `part_number`.

Con los cambios ya publicados en `calidad-revision-lc`, ejecutar manualmente
`Deploy VPS` sobre esa rama con:

- `notifications_only`: `true`.
- `reset_runtime`: `false`.

Tambien puede solicitarse sobre esa rama con GitHub CLI:

```sh
gh workflow run deploy-vps.yml --ref calidad-revision-lc -f notifications_only=true -f reset_runtime=false
```

El boton `Run workflow` depende de que el workflow exista en la rama por
defecto. Si el disparo manual no esta disponible en el repositorio, usar el
comando de Docker del VPS indicado a continuacion.

Esta opcion reconstruye y recrea solo `worker_meli_notifications`, dejando
activos Redis, API, frontend y los workers de precios/stock y publicaciones.
El worker dispone de hasta dos minutos para terminar su tarea al detenerse.
No combina esta opcion con el reseteo de colas.

Equivalente desde la raiz del codigo actualizado en el VPS:

```sh
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml up -d --build --no-deps --force-recreate worker_meli_notifications
```

### Verificar el resultado

Despues de recibir una notificacion nueva, revisar el log
`[MELI_PUBLICATION_SYNC][SAVED] ... part_number_verified=True` y consultar:

```sql
select e.resource, e.processing_status, e.processing_error,
       p.part_number, p.sincronizado_at
from public.meli_notification_events e
left join public.publicaciones_ml p
  on p.mlc = split_part(e.resource, '/', 3)
 and p.seller_id::text = e.ml_user_id
where e.topic = 'items'
order by e.created_at desc
limit 20;
```

Las notificaciones que ya estaban `processed` no se repiten al desplegar.
Los registros historicos sin numero de pieza se completan con una nueva
notificacion del item o con una carga de publicaciones posterior.
