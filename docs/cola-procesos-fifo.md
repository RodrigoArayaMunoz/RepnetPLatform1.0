# Orden de la cola de archivos

La cola ejecuta primero el pendiente más antiguo según su fecha y hora guardadas. Los empates se resuelven por `proceso_id` ascendente y después por `id`. El tablero muestra los pendientes en ese mismo orden, incluida la numeración y las páginas. El historial de finalizados conserva los más recientes primero.

Ejemplo: mientras se ejecuta A se guardan B y C; durante B se guarda D. El orden de ejecución es A → B → C → D. La misma regla aplica a todos los tipos de archivo que procesa la cola.

El worker vuelve a consultar los pendientes antes de elegir cada archivo. Los nuevos archivos se incluyen en esa consulta y los errores de un archivo conservan el orden de los restantes. Se mantiene la espera configurada entre archivos y el procesamiento de uno a la vez.

La modificación utiliza las columnas existentes; no necesita migración SQL, borrado de Redis ni volver a subir los archivos pendientes. Las pruebas simulan altas durante una ejecución y durante la espera, cambios de día, empates de fecha/hora, distintos tipos de archivo y un archivo fallido.

Para aplicarlo en producción hay que desplegar frontend y backend/worker mediante el despliegue general de `Deploy VPS` en `calidad-revision-lc`, en un momento sin procesos activos. Un worker que ya está ejecutando código anterior no cambia su orden hasta recibir la versión nueva. Esta modificación local no realiza commit, push ni despliegue.
