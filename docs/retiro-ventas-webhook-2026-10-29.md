# Retiro de ventas y notificaciones de Mercado Libre

Fecha: 29 de octubre de 2026. Estos cambios están preparados en el código local;
su efecto en producción requiere desplegar las nuevas imágenes.

## Alcance

Se eliminaron pantalla, escáner, consultas y sincronización de ventas, órdenes,
packs, envíos y picking, junto con la persistencia de notificaciones.
La ruta antigua `/gestion/ventas` redirige, con autenticación, a procesos.
Las rutas `/ml/sales` responden HTTP 410 antes de verificar la sesión en Supabase:
una pestaña antigua no puede seguir consultando ni encolar nuevas ventas.

El receptor antiguo `POST /webhooks/mercadolibre` solo acusa recibo con HTTP 200
y `disabled: true`, sin consultas ni encolado. Su health devuelve `enabled: false`.
Se mantienen los nombres Celery `tasks.process_meli_notification` y
`tasks.backfill_meli_sales` para consumir tareas antiguas sin ejecutar trabajo,
sin reintentos y sin solicitudes a Supabase o Mercado Libre.

Se conservan OAuth, tokens, publicaciones, compatibilidades, excepciones, precios,
stock, descripciones, fotografías, devoluciones, REFAX y la cola de archivos.
Las publicaciones dejan de actualizarse por eventos de órdenes/items; se utiliza
su sincronización existente. No se ejecutan borrados SQL y se conservan las
migraciones históricas. `publicaciones_ml` y la conexión ML siguen siendo necesarias.

## Aplicación en el VPS

La comprobación posterior a las pruebas devolvió HTTP 200 con `{"ok":true}` en
el health público: producción todavía ejecuta el receptor anterior. El acceso
SSH con la clave de auditoría disponible fue rechazado. No se desplegó este cambio.

Para detener inmediatamente los reintentos de este módulo, desde una sesión
autorizada del KVM o SSH del VPS puede detenerse solo su contenedor:

```bash
sudo docker stop compat_worker_meli_notifications
```

Esto detiene también cualquier sincronización antigua de ventas, retirada por
petición del propietario. No borra Redis ni detiene los demás contenedores.
El código nuevo debe instalarse antes de volver a levantar este trabajador;
reiniciar su imagen antigua reanudaría el procesamiento anterior.

El workflow `Deploy VPS` incluye `retire_meli_sales_only`: construye y actualiza
únicamente API, frontend y `worker_meli_notifications` usando `--no-deps`.
Comprueba que el webhook devuelve `enabled: false`. No combinar este modo con
otros modos parciales ni con `reset_runtime`.

La rama `calidad-revision-lc` dispara el workflow al recibir un push. Para esta
intervención, el mensaje del último commit debe contener `[retire-meli-sales]`:
el workflow activa automáticamente el modo selectivo, ejecuta pruebas y compila,
omite la configuración de credenciales REFAX y despliega exactamente el SHA probado.
Un push sin ese marcador conserva el despliegue completo habitual.

Desde la raíz del proyecto, después de revisar los cambios:

```powershell
git add -u
git add docs/retiro-ventas-webhook-2026-10-29.md
git commit -m "Retira webhook y ventas ML [retire-meli-sales]"
git push origin calidad-revision-lc
```

No añadir `[skip ci]`: impediría esta ejecución automática. El modo selectivo
puede trabajar mientras los otros workers continúan sus procesos.
Revisar en GitHub Actions que el paso Deploy indique el retiro de ventas/webhook
y finalice con la comprobación del receptor desactivado.

La rama predeterminada `main` no contiene este workflow según la consulta a GitHub;
por eso no debe dependerse del botón Run workflow para esta intervención.
La opción manual se conserva para cuando el workflow esté también registrado
en la rama predeterminada.

El usuario confirmó mediante captura que detuvo `compat_worker_meli_notifications`
y ejecutó `DEL meli_notifications`. Antes del borrado, `LLEN` devolvió 1106219.
El resultado `1` del borrado corresponde a una clave eliminada, no a una tarea.
Esto no verifica las entregas reservadas/programadas; se mantiene el consumidor
inocuo de transición para descartar las que reaparezcan.

Alternativa, desde el directorio del VPS una vez instalado el código revisado:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml build api frontend worker_meli_notifications
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml stop worker_meli_notifications
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml up -d --no-deps worker_meli_notifications
docker compose --env-file deploy/.env -f deploy/docker-compose.prod.yml up -d --no-deps api frontend
curl --fail https://repnet.online/api/webhooks/mercadolibre/health
curl -i https://repnet.online/api/ml/sales/today
```

Esperar `enabled: false` y HTTP 410. La API y la web pueden interrumpirse brevemente;
los trabajadores de archivos continúan. Verificar que los demás contenedores
conservan identidad/fecha de inicio y que el proceso activo sigue avanzando.
No usar `docker compose down`, `FLUSHDB`, purgas globales ni borrar `process_queue:state`.

El trabajador con nombre histórico solo drena su cola. Mantenerlo durante la
transición permite consumir mensajes reservados, reintentos programados y
redeliveries. Una lista vacía no demuestra que no queden mensajes reservados;
su eliminación definitiva requiere comprobar esos estados y los productores
antiguos. No hace falta purgar Redis para detener las solicitudes a Supabase.

## Consumo y rendimiento

El 28 de octubre a las 23:54 de Chile, la API de `meli_notification_events`
respondió 404/PGRST205, tabla no encontrada en el esquema expuesto. Las tablas de
procesos, publicaciones y conexiones respondieron 200. El usuario identificó
advertencias `PATCH /rest/v1/meli_notification_events`. Esto es compatible con
tareas antiguas reintentando guardar estado en una tabla ausente; no demuestra
que todas las advertencias del panel tengan esa causa.

Después del despliegue, comparar ventanas equivalentes de cinco minutos: las
llamadas a esa ruta deben cesar y el proceso de archivos debe seguir avanzando.
El consumo acumulado del ciclo no desaparece al retirar código o tablas.

- Log Ingestion: reducir llamadas y errores disminuye los nuevos registros.
- Log Query: mide datos de logs examinados. Usar rangos cortos y filtros y evitar
  consultar repetidamente todo el historial.
- Egress: mide bytes enviados. Paginar y reutilizar datos en caché, evitando
  nuevas lecturas a Supabase, reduce el crecimiento del consumo.

La captura reciente muestra una base de aproximadamente 95,6 MB: ese tamaño no
justifica ampliar el VPS ni borrar más tablas. Priorizar el tráfico repetitivo
y medir después las consultas y el avance de compatibilidades por bloque.
Para continuidad, considerar Supabase Pro por el exceso de egress. El panel
indica restricciones el 30 de octubre, aunque el correo menciona el 31; tomar
la fecha más temprana. Pro parte de USD 25/mes; con un proyecto Small serían
aproximadamente USD 30/mes (25 + 15 de cómputo - 10 de crédito), antes de impuestos
y extras. Cambiar el plan no elimina por sí solo los reintentos ni las esperas
por Mercado Libre.

Fuentes oficiales consultadas el 29 de octubre de 2026:

- [Log Ingestion](https://supabase.com/docs/guides/platform/manage-your-usage/logs-ingest)
- [Log Query](https://supabase.com/docs/guides/platform/manage-your-usage/logs-query)
- [Egress](https://supabase.com/docs/guides/platform/manage-your-usage/egress)
- [Precios](https://supabase.com/pricing)

## Validación local

- 248 pruebas del backend aprobadas, con almacenamiento temporal aislado y
  endpoints de prueba para no utilizar los datos de producción.
- 19 pruebas del frontend aprobadas y compilación Vite correcta.
- Pruebas de retiro verifican que callbacks tardíos y tareas antiguas no consultan
  HTTP/Redis de aplicación ni crean nuevos trabajos o reintentos; las rutas de
  ventas responden 410 sin consultar autenticación.
- La sincronización manual mantiene número de pieza, estado y compatibilidades
  en las publicaciones, incluidas las compatibilidades con valor falso.
- Workflow verificado con análisis YAML/Bash y simulaciones de modos y fallos
  de construcción. La operación selectiva no recrea los demás trabajadores.
