# Despliegue de notificaciones de Mercado Libre

## 1. Seguridad previa

El Client Secret que aparecio en una captura debe rotarse en Dev Center. Despues
de rotarlo, actualizar `ML_CLIENT_SECRET` en
`src/backend/compatibilties/.env` antes de desplegar.

No guardar ni publicar el Client Secret en documentacion, capturas o commits.

## 2. Base de datos

Ejecutar en Supabase SQL Editor, una sola vez, el contenido de:

`supabase/migrations/202608280001_create_meli_sales_webhooks.sql`

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

No activar `Flex-Handshakes` para la pantalla de ventas. El worker identifica
Flex con `logistic_type = self_service` en el recurso shipment.

Aceptar los terminos, completar el CAPTCHA y guardar con Editar.

## 6. Sincronizacion inicial y operacion

La pantalla Gestion de Ventas lee Supabase al abrirse. El boton de actualizar
ejecuta una sincronizacion manual del dia a traves de la cola y luego vuelve a
leer Supabase. Esto permite cargar las ordenes existentes del dia sin esperar
una nueva notificacion.

Despues de activar Dev Center:

1. Hacer una venta o modificar una orden de prueba.
2. Verificar una fila `processed` en `meli_notification_events`.
3. Verificar la orden y SKU en `meli_orders` y `meli_order_items`.
4. Verificar `shipping_type = flex` para envios `self_service` o `normal` para
   las demas modalidades.
5. Abrir Gestion de Ventas y confirmar SKU y cantidad.

Si un evento queda `failed`, revisar `processing_error` y los logs del worker.
Celery reintenta automaticamente con espera incremental.
