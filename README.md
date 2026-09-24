# Auddit MVP local

MVP en Python para cubrir la base del proyecto que conyiene: usuarios con roles,
asignacion de empresas a auditores y herramienta de busqueda asistida para
generar una ficha inicial de la empresa auditada.

## Alcance incluido

- Login local con sesiones.
- Rol `admin`: representa al jefe auditor con acceso de supervisión.
- Rol `auditor`: representa al trabajador/auditor.
- Creacion de usuarios por parte del jefe.
- Ciclo de vida de cuentas con desactivación reversible, baja lógica definitiva
  e historial permanente de empresas asignadas.
- Registro de empresas y asignacion a auditores.
- Vista del auditor con sus empresas asignadas.
- Vista de supervisión del jefe sobre todos los expedientes en modo lectura.
- Herramienta de busqueda asistida:
  - enlaces a fuentes sugeridas;
  - registro de fuentes consultadas;
  - captura estructurada de informacion;
  - registro de administradores y accionistas;
  - pegado de texto encontrado;
  - extraccion basica de RUC, correos y telefonos;
  - validacion de requisitos obligatorios antes del resumen;
  - generacion de resumen preliminar por parte del auditor.
- Descarga del resumen en texto y del expediente en Excel (.xlsx): la primera
  hoja presenta los datos SRI, Supercias, ubicación, personas, cifras financieras,
  validaciones, fuentes, pendientes y recomendación; las hojas siguientes contienen
  seis bloques del expediente y registros originales
  completos de los catálogos SRI y Supercias y estados de situación y resultados
  con todos los casilleros del TXT por ramo, por año, incluidos saldos cero.
  Incluye requisitos, validaciones, documentos, fuentes y trazabilidad. El
  archivo distingue cifras del catálogo y correcciones del auditor: no presenta
  el TXT como un estado financiero firmado ni una nómina parcial como completa.

## Levantamiento de información general del cliente

El expediente sigue el requisito "Levantamiento de Información General del
Cliente" (v1.0): 6 bloques de datos, 5 reglas generales y un flujo de consulta
de 9 pasos que la pantalla del expediente muestra en ese orden.

| Paso | Fuente | Qué se registra |
| ---: | --- | --- |
| 1 | SRI — Consulta de RUC | Bloque 1: identificación tributaria |
| 2-3 | Supercias — Información general | Bloques 2 y 3: información societaria y ubicación |
| 4 | Supercias — Administradores actuales | Bloque 4: gerente general y presidente, con identificación |
| 5 | Supercias — Accionistas y Kárdex | Bloque 5: accionistas, participación y beneficiario final |
| 6-8 | Supercias — Documentos económicos | Bloque 6: año fiscal y casilleros 1, 2, 3, 401, 403, 501, 502 y 707 |
| 9 | Sistema | Validaciones cruzadas y alertas |

- **Búsqueda automática:** "Iniciar búsqueda" completa lo que publican los
  catálogos locales. Lo demás se captura a mano con una fecha de consulta.
- **Trazabilidad:** cada dato registra su fuente y fecha de consulta en
  `data_provenance`, un historial de solo inserción.
- **Clave RUC:** la información financiera se guarda por RUC y año fiscal
  (`financial_statements`). Un ejercicio nuevo no sobrescribe otro.
- **Validaciones** (`services/validaciones.py`, única fuente de decisión):
  - razón social SRI = Supercias;
  - fecha de inicio = fecha de constitución;
  - representante legal = gerente general;
  - balance cuadrado (tolerancia USD 1,00);
  - CIIU SRI = CIIU Supercias;
  - actividad económica vs objeto social (revisión del auditor).
- **Alertas:**
  - RUC no activo;
  - situación legal distinta de activa;
  - balance descuadrado;
  - contribuyente fantasma o transacciones inexistentes: alerta crítica, que
    exige registrar su tratamiento.
- **Resumen:** si faltan datos, "Generar resumen" abre un modal con los
  obligatorios y las recomendaciones pendientes. El auditor puede volver a
  completarlos o generar igual; en ese caso el resumen los lista en su
  sección "Pendientes de validación". Se presenta en el orden de los bloques,
  con la fuente y fecha de cada uno.
- **Alerta crítica:** su tratamiento se registra en la pestaña SRI, junto al
  dato que la origina.

La documentación de cada fase está en
`../atlas_documentacion/propuesta_levantamiento/`.

## Ejecucion local

Desde esta carpeta:

```bash
python3 -m pip install -r requirements.txt   # pypdf (certificados PDF) y openpyxl (Excel)
python3 app.py --port 8765
```

Abrir:

```text
http://127.0.0.1:8765
```

Usuarios:

- Una base nueva crea solo al **jefe auditor**, el usuario principal:
  `admin` / `admin123`. Cambie esa contraseña al primer ingreso en
  **Mi cuenta** (clic en su nombre, arriba a la derecha).
- El jefe auditor crea a los **auditores** desde Usuarios, con una contraseña
  temporal que cada auditor cambia en Mi cuenta.
- Todos los usuarios cambian su propia contraseña en Mi cuenta: se pide la
  actual, y las demás sesiones abiertas de la cuenta se cierran.

El auditor y la empresa demo solo existen en los tests (`init_db(demo=True)`).

## Catastro local del SRI

"Iniciar búsqueda" consulta el catastro RUC del SRI importado localmente. El SRI
publica un archivo por provincia; cada carga actualiza los RUC del archivo y
conserva los de las provincias ya importadas:

```bash
python3 scripts/update_catastro.py SRI_RUC_Azuay.csv
python3 scripts/update_catastro.py SRI_RUC_Pichincha.csv SRI_RUC_Guayas.csv
python3 scripts/update_catastro.py --reemplazar SRI_RUC_*.csv   # vacía la base antes de cargar
```

Al terminar, el script muestra cuántos RUC hay por jurisdicción, y cada archivo
importado queda registrado en la tabla `sri_catastro_meta`. Si un cliente no
aparece en la búsqueda, lo primero es revisar que su provincia esté cargada.

## Catálogo local de Supercías (Directorio de Compañías)

"Iniciar búsqueda" consulta, además del catastro RUC del SRI, un catálogo
local del Directorio de Compañías de Supercías. Ese catálogo no se descarga
automáticamente en cada consulta: se importa manualmente, igual que el
catastro del SRI.

```bash
python3 -m pip install -r requirements.txt   # instala openpyxl
python3 scripts/update_supercias_catalog.py             # descarga la última versión pública
python3 scripts/update_supercias_catalog.py archivo.xlsx # o usa un archivo ya descargado
```

Si el catálogo no fue importado, o el RUC no consta en él, la búsqueda igual
se completa con los datos del SRI y Supercías queda marcado como pendiente
(nunca produce un error 500 ni bloquea la investigación).

El Directorio de Compañías solo trae el representante legal actual, no la
nómina completa de administradores ni los accionistas. Para esos dos campos,
las pestañas "Administradores" y "Accionistas" muestran un flujo asistido:

1. Enlaces al trámite del certificado y al portal de información de Supercías.
2. El auditor adjunta el certificado en PDF una sola vez, desde cualquiera de
   las dos pestañas. Si la nómina viene en varios documentos (hasta 5), los
   selecciona juntos. Cada archivo se guarda en `adjuntos/<expediente>/<sha256>.pdf`
   (excluido de Git) y queda registrado como evidencia.
3. `services/certificados.py` extrae el texto (pypdf) y propone ambas
   nóminas, combinando los documentos en una sola propuesta sin repetir filas: administradores (identificación, nombre, cargo, nacionalidad) y
   accionistas (identificación, nombre, capital, participación). Cada fila se
   asigna por la sección del documento y por su contenido (cargo o capital).
4. Nada se importa sin revisión: el mismo panel aparece en las dos pestañas y
   un solo "Importar" registra administradores y accionistas.
   Las filas importadas llevan la fuente "Supercias — certificado de nómina
   (PDF adjunto)" y su fecha de consulta.

El lector prefiere descartar a adivinar:

- Si el documento declara el RUC de otra compañía, se rechaza el archivo.
- Una fila empieza en una identificación válida: cédula con provincia, tercer
  dígito y dígito verificador correctos, o RUC con su dígito verificador. Así
  un teléfono o un número de registro no se toman por cédula, y el RUC de la
  propia compañía nunca es una fila.
- Es administrador si trae un cargo reconocido y accionista si trae capital o
  porcentaje o está en la sección de accionistas; una identificación sin esas
  señales se omite con un aviso.
- El nombre excluye encabezados, etiquetas, cargo y nacionalidad.

Está calibrado con los documentos del portal de Supercias (información general
y nómina de accionistas). Un PDF escaneado, sin texto seleccionable, no se
puede leer, y lo que no reconozca se registra a mano en el mismo formulario de
siempre. Para calibrarlo con más documentos, guarde muestras en
`muestras_supercias/` (también excluida de Git).

## Estados financieros por ramo de Supercías

Descargue del [portal de estados financieros por ramo](https://appscvsgen.supercias.gob.ec/consultaCompanias/societario/estadosFinancierosPorRamo.jsf)
los TXT `balances_YYYY_*.txt` y `catalogo_YYYY_*.txt` para el ejercicio
requerido. Coloque ambos archivos en una carpeta y ejecute:

```bash
python3 scripts/update_balances_catalog.py /ruta/estadosFinancieros_2025
```

El importador usa solo la biblioteca estándar de Python. Carga los casilleros
1, 2, 3, 401, 403, 501, 502 y 707 para la ficha del expediente y conserva
la totalidad de cuentas del catálogo correspondiente por RUC y año para el Excel de detalle en
`supercias_balances.db` (excluida de Git). Guarda también el catálogo de
cuentas, el nombre del archivo, su SHA-256 y la fecha de importación. Puede
repetirse para reemplazar un ejercicio sin borrar los demás. Filas con RUC
inválido se omiten y se cuentan; otros errores de formato cancelan la carga
sin reemplazar el ejercicio anterior.

Tras importar, el auditor debe pulsar **Iniciar búsqueda** de nuevo en el
expediente para cargar las cifras disponibles. La búsqueda solo llena
casilleros vacíos: conserva las correcciones manuales y registra fuente y
fecha por dato. El año fiscal de la auditoría sigue requiriendo confirmación
explícita. El jefe puede ver la información, pero no ejecutar la búsqueda ni
editarla. Este reporte agregado no sustituye la revisión del documento
económico original, las notas ni el acta de junta.

## Fuentes sugeridas para la busqueda

La herramienta no intenta hacer scraping automatico porque las fuentes oficiales
pueden cambiar, usar JavaScript o pedir verificacion humana. El MVP se enfoca en
guiar la consulta y guardar evidencia.

- Supercias: consulta de companias.
- SRI: informacion RUC sociedades.
- SERCOP: busqueda de proveedores.
- Busqueda web general por nombre/RUC.

## Archivos principales

- `app.py`: punto de entrada; delega en `core/server.py`.
- `core/server.py`: servidor HTTP (rutas, sesiones, CSRF, RBAC).
- `core/router.py`: tabla declarativa de rutas GET.
- `database/`: SQLite, un módulo por tema (usuarios, expedientes,
  investigación, perfil, personas, financiero, catálogos, trazabilidad,
  esquema). `database/__init__.py` re-exporta la API pública.
- `schema.sql`: esquema de la base de datos.
- `services/`: lógica de negocio pura, sin SQL. Incluye:
  - validación de RUC e identificaciones;
  - normalización de valores de los catálogos;
  - trazabilidad;
  - validaciones cruzadas y alertas (`validaciones.py`);
  - indicadores financieros, resumen y ficha final.
- `providers/`: generan enlaces e instrucciones hacia fuentes oficiales
  externas (SRI, Supercías); nunca hacen scraping ni guardan credenciales.
- `views/`: construcción de HTML por pantalla (`admin/`, `auditor/`,
  `auditor/radar/` para el Radar Empresarial).
- `ui/`: componentes, helpers e íconos HTML reutilizables entre vistas.
- `static/css/`: estilos, un archivo por responsabilidad. `core/server.py`
  los une al arrancar en el orden de `CSS_FILES`: `tokens` → `base` →
  `componentes` → páginas (`login`, `paneles`, `expediente`, `financiero`,
  `personas`, `resumen`, `ficha`) → `utilidades`, que va último.
- `scripts/`: herramientas de importación de catálogos locales
  (`update_catastro.py` para el SRI, `update_supercias_catalog.py` para el
  Directorio de Compañías de Supercías) — se ejecutan manualmente, no en
  cada arranque del servidor.
- `auddit.db`: base local generada al primer arranque. `sri_catastro.db` y
  `supercias_catalog.db` son catálogos locales separados, generados por los
  scripts de arriba (ninguno de los tres se versiona en git).
- `tests/`: pruebas unitarias (bases temporales) y de integración HTTP
  (`tests/test_http.py`: escribe en `auddit.db`, así que solo corre con
  `ATLAS_HTTP_TEST_PORT` y sobre una copia del proyecto; ver su docstring).

## Arquitectura y convenciones

Capas, de afuera hacia adentro. Cada una solo importa de las que están debajo:

```text
core/      HTTP: rutas, sesión, CSRF, rol → llama a database/ y services/
views/     HTML por pantalla              → usa ui/, services/, database/ (solo lectura)
ui/        componentes HTML sin estado     → nunca importa de views/
services/  reglas de negocio puras, sin SQL
database/  SQL y persistencia (única capa que escribe en SQLite)
```

Excepciones conocidas: `services/company_research.py` orquesta la búsqueda
automática y por eso usa `database`; `ui/components.py` lee
`AUDIT_STATUSES` de `database`.

**Agregar una ruta**

- GET: una línea en `core/router.py` (`GET_ROUTES`).
- POST del jefe: una función `(form, admin) -> mensaje` y una línea en
  `ADMIN_POSTS` (`core/server.py`).
- POST del auditor sobre un expediente: una función
  `(form, audit, user) -> mensaje` y una línea en `RADAR_POSTS`. El
  dispatcher ya valida CSRF, rol, acceso al expediente y redirige a la
  pestaña. Para mostrar un error, basta con lanzar `ValueError`.
- Exportación: una función `build(audit) -> str | bytes` y una línea en `EXPORTS`
  (con su tipo de contenido en `_CONTENT_TYPES` de `core/server.py`).

**Reutilizar antes de escribir HTML**

| Necesidad | Componente |
| --- | --- |
| CSRF + campos ocultos de un formulario | `ui.helpers.hidden_inputs(csrf_token, audit_id=…)` |
| Campo etiquetado (input o textarea) | `ui.components.form_field` |
| Formulario plegable "✎ Editar …" de un bloque | `ui.components.edit_panel` |
| Fecha de consulta de la fuente | `ui.components.fecha_consulta_field` |
| Dato de solo lectura con traza | `ui.components.info_card` |
| Historial de un bloque | `ui.components.provenance_history` |
| Lectura segura de filas | `services.rowutil.row_get` |
| Mapa de fuentes y requisitos del expediente | `services.company_search.source_map_from_context` |

Un bloque del levantamiento se define una sola vez como tupla `CAMPOS`
`(campo, etiqueta, …)` en su pestaña. De ella salen las tarjetas, el
formulario de edición y las etiquetas del historial.

**Estilos**

- Un color o una medida que se repite va como variable en `tokens.css`, y se
  usa con `var(--…)`.
- Una regla nueva va en el archivo de su pantalla o de su componente, y sus
  `@media` al final de ese mismo archivo.
- Un archivo nuevo se agrega a `CSS_FILES`. El orden importa: lo más
  específico va después.

**Nomenclatura**

- Términos del requisito de levantamiento en español, tal como aparecen en el
  requisito: columnas, constantes y funciones de dominio (`razon_social_sri`,
  `validar_fecha_consulta`, `CAMPOS`, `BLOQUE_SRI`).
- Infraestructura genérica en inglés (`connect`, `get_audit`, `send_html`,
  `hidden_inputs`).
- Constantes en `MAYUSCULAS`; lo privado de un módulo con prefijo `_`.
- Las vistas de pestaña se llaman `tab_<bloque>.py` y exponen `build(...)`.
- No renombrar código existente solo por uniformidad: se aplica a código
  nuevo o al que ya se está modificando.
