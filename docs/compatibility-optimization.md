# Optimización de compatibilidades: implementación y pruebas reales

Implementado y verificado el 11 de octubre de 2026 usando filas de
`RESULTADO MLC-AMORTIGUADORES.xlsx`. Los cambios están en el backend local y
en los perfiles de Docker Compose. Las pruebas utilizaron la API real de
Mercado Libre y dejaron **9 compatibilidades correctas en 6 publicaciones**.
No se ejecutó una carga real de las 42.870 filas ni un despliegue del servidor.

## Comportamiento implementado

1. MARCA, MODELO, AÑO, VERSION, CILINDRADA y TRANSMISION se envían mediante
   `value_name`. Se valida el conteo de la familia antes de escribir. Un
   conteo cero o un HTTP 400 activa la resolución contextual por IDs. Los
   errores transitorios conservan el mecanismo de reintentos.
2. Cada combinación vehicular comparte un conteo entre publicaciones, sin
   compartir sus metadatos de destino. La caché en memoria evita consultas
   simultáneas repetidas y SQLite conserva resultados durante una hora en
   el volumen de uploads compartido por los workers. Si SQLite no está
   disponible, continúa el flujo con la caché del trabajo.
3. Todas las filas de una publicación se procesan juntas, incluso si
   aparecen intercaladas en el Excel. Se combinan las posiciones del mismo
   vehículo antes de escribir y el resultado conserva el orden del archivo.
4. Los lotes nuevos incluyen compatibilidad, nota y posiciones en un único
   POST. Si el número creado es menor al número de productos encontrados,
   se ejecuta un PUT para actualizar la información de los existentes.
   Cuando una familia ya se aplicó en el mismo trabajo y cambian posiciones,
   se usa directamente PUT. Un fallo de actualización mantiene la fila con
   error y conserva el conteo de las creaciones confirmadas.
5. Se mantienen los límites actuales de 10 familias y 200 productos
   encontrados por solicitud, la concurrencia limitada y el control de
   tráfico compartido mediante Redis.
6. Las publicaciones con el mismo conjunto completo de vehículos, posiciones,
   nota y categoría pueden usar copia con `extended_information: true`.
   La firma incluye también al vendedor. Solo se registra como origen una
   publicación que estaba vacía y cuyo resultado completo coincide con el
   conjunto solicitado. El origen se vuelve a leer antes de copiar y el
   destino debe estar vacío. Se excluyen resúmenes de catálogo, respuestas
   parciales, vehículos duplicados y orígenes con reclamos. Se limita la
   copia a conjuntos de hasta 200 productos para verificar su resultado
   síncrono. Una copia incompleta se completa mediante POST/PUT por familias.
7. Se guarda una confirmación después de leer el resultado completo y
   comprobar cantidades, posiciones y notas. Una reejecución usa GET para
   confirmar que esos productos conservan la información guardada y omite
   escrituras. Si se elimina un vehículo o cambia la información, vuelve al
   flujo normal. Estas confirmaciones también caducan en una hora.

Las posiciones conservan sus IDs oficiales y se calculan localmente. No
requieren resolver IDs por HTTP. Una posición principal vacía permite
informar solamente los lados. `IZQUIERDA/DERECHA` crea dos alternativas.

La [documentación oficial de compatibilidades](https://developers.mercadolibre.cl/compatibilidades-entre-items-y-productos)
describe familias por nombre/ID y creación con nota y restricciones. Las
[reglas de copia](https://developers.mercadolibre.com.ar/compatibilidades-entre-items-y-productos)
establecen el origen exclusivo por ítem o user product, la inclusión de
información extendida y la exclusión de catálogo y reclamos. Las escrituras
reales y su lectura posterior comprobaron los casos descritos abajo.

## Pruebas reales realizadas

Los cuerpos HTTP y las lecturas posteriores se guardaron sin credenciales.
El cliente HTTP y las respuestas fueron reales. El arnés utilizó un token
vigente de la conexión existente, evitó crear un trabajo en la cola y usó
una caché local separada. Las pruebas estuvieron limitadas a seis destinos
del Excel, con 12 solicitudes de escritura en total.

| Publicación | Prueba | Resultado verificado |
| --- | --- | --- |
| MLC4557290850 | Kia Cerato, delantera y ambos lados | 1 compatibilidad, nota y dos posiciones, con un POST |
| MLC2312790181 | Changan CX70, trasera y ambos lados | 1 compatibilidad, nota y dos posiciones, con un POST |
| MLC4557291764 | Suzuki Alto, primera columna vacía | 1 compatibilidad, nota, izquierda y derecha, con un POST |
| MLC4557291816 | Publicación base de un conjunto completo | 1 compatibilidad con posiciones y nota, con un POST |
| MLC4557187840 | Destino con el mismo conjunto completo | Copia de la compatibilidad, posiciones y nota, con un POST de copia |
| MLC4557239678 | Familia Toyota que encuentra cuatro vehículos | 4 compatibilidades, todas con nota y posiciones, con un POST |

Se repitieron las primeras tres familias ya existentes. Mercado Libre
respondió con cero creaciones y el backend aplicó tres PUT después de los
tres POST: ese caso confirma que la actualización sigue siendo necesaria.
Posteriormente se repitieron los seis destinos con confirmaciones válidas:
**cero escrituras**, con todos los productos y metadatos comprobados.

| Escenario real del backend | Escrituras | Tiempo del proceso |
| --- | ---: | ---: |
| Crear una base y copiar a otro destino | 2 POST | 13,110 s |
| Repetir inmediatamente esos dos destinos | 0 | 5,687 s |
| Aplicar tres familias existentes sin confirmación previa | 3 POST + 3 PUT | 21,594 s |
| Repetir las mismas tres filas con confirmación | 0 | 7,312 s |
| Crear cuatro productos por una familia Toyota | 1 POST | 5,735 s |
| Repetir esa familia Toyota con confirmación | 0 | 2,859 s |

Son mediciones de muestras pequeñas con el mismo limitador de 0,7
solicitudes/segundo. Incluyen las comprobaciones internas del backend y
excluyen las lecturas adicionales del arnés. La comparación de tres filas
mide el ahorro de reanudación y caché, alrededor del 66 % en esa muestra.
No estima la duración de la carga completa.

Evidencia:

- [Creación nueva y lectura de nota/posiciones](../artifacts/compatibility-optimization/live-creation-probe.json).
- [Creación, copia, existentes y múltiples productos](../artifacts/compatibility-optimization/live-pipeline-verification.json).
- [Reejecución real de los seis destinos sin escrituras](../artifacts/compatibility-optimization/live-resume-verification.json).
- [Prevalidación de los destinos inicialmente vacíos](../artifacts/compatibility-optimization/live-preflight.json).

## Verificación del archivo completo y del código

La auditoría de posiciones confirmó las **42.870 filas sin diferencias**.
También se recorrió el archivo completo con el orquestador implementado y
una API simulada que conserva el estado de cada publicación. Se comprobaron
el destino correcto, el orden de resultados y las posiciones de cada fila.

| Recorrido completo simulado | Resultado |
| --- | ---: |
| Filas válidas | 42.870 |
| Publicaciones | 1.480 |
| Conteos de familia consultados | 4.300 |
| POST por familias | 971 |
| POST de copia | 1.165 |
| PUT | 0 |
| Total de escrituras simuladas | 2.136 |

Esta simulación supone un producto encontrado por familia y destinos
inicialmente vacíos. No consultó la API ni predice el número final de
solicitudes reales. Frente al cálculo anterior de 10.112 escrituras, esa
misma hipótesis reduce las escrituras aproximadamente un 79 %. En la carga
real influyen las coincidencias múltiples, los existentes, la elegibilidad
para copiar y los reintentos. El
[informe de simulación](../artifacts/compatibility-optimization/full-pipeline-simulation.json)
registra la hipótesis explícitamente.

La suite del backend pasó **253 pruebas**, incluidas 24 pruebas nuevas de
caché persistente, fallback por IDs, lotes, copia parcial, reclamos, cambios
en origen/destino, reanudación, conservación del conteo tras fallos y orden
del Excel. Pyright 1.1.414 analizó los servicios modificados, el nuevo módulo
de pruebas y los scripts de verificación con **cero errores y advertencias**.

## Configuración y reproducción

Los valores predeterminados y ambos perfiles de Compose incluyen:

| Variable | Valor | Efecto |
| --- | --- | --- |
| COMPATIBILITY_USE_VALUE_NAMES | true | Nombres y agrupación por publicación completa. false conserva IDs y bloques originales. |
| COMPATIBILITY_ENABLE_COPY | true | Copia de conjuntos completos verificados. false conserva creación por familias. |
| COMPATIBILITY_FAMILY_CACHE_TTL_SECONDS | 3600 | Caducidad de conteos, orígenes y confirmaciones en SQLite. 0 desactiva persistencia. |

Los workers de producción ya comparten `/code/uploads`; allí se guarda
`compatibility-family-cache.sqlite3`. El cambio debe instalarse en los
workers que procesan compatibilidades para activar la optimización del
servidor. La implementación y las pruebas reales se realizaron desde este
workspace, sin despliegue remoto.

Desde `src/backend/compatibilties`, ejecutar la suite:

```powershell
C:\RepnetPlatform1.0\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Desde la raíz del proyecto, comprobar el recorrido completo sin API:

```powershell
C:\RepnetPlatform1.0\.venv\Scripts\python.exe artifacts/compatibility-optimization/verify_full_pipeline.py 'C:\Users\Rodrigo Araya\Downloads\RESULTADO MLC-AMORTIGUADORES.xlsx'
```

`live_pipeline_test.py --resume-only` comprueba las confirmaciones de los
seis destinos y rechaza todas las escrituras de compatibilidades. Requiere
la conexión vigente y la caché local original todavía válida. El modo sin
esa opción realiza escrituras reales en los destinos de la prevalidación y
debe usarse cuando se pretenda repetir esa prueba de creación/copia.

El análisis previo de seis búsquedas por nombres y tres comparaciones con
IDs permanece en [verification.json](../artifacts/compatibility-optimization/verification.json).
