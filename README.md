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
- Exportacion del resumen a TXT.

## Ejecucion local

Desde esta carpeta:

```bash
python3 app.py --port 8765
```

Abrir:

```text
http://127.0.0.1:8765
```

Credenciales de demostracion:

```text
admin / admin123
auditor / auditor123
```

Cambiar estas claves antes de registrar informacion real.

## Catálogo local de Supercías (Directorio de Compañías)

"Iniciar búsqueda" consulta, además del catastro RUC del SRI, un catálogo
local del Directorio de Compañías de Supercías. Ese catálogo no se descarga
automáticamente en cada consulta: se importa manualmente, igual que el
catastro del SRI.

```bash
python3 -m pip install -r requirements.txt   # instala openpyxl (solo lo usa este script)
python3 scripts/update_supercias_catalog.py             # descarga la última versión pública
python3 scripts/update_supercias_catalog.py archivo.xlsx # o usa un archivo ya descargado
```

Si el catálogo no fue importado, o el RUC no consta en él, la búsqueda igual
se completa con los datos del SRI y Supercías queda marcado como pendiente
(nunca produce un error 500 ni bloquea la investigación).

El Directorio de Compañías solo trae el representante legal actual, no la
nómina completa de administradores ni los accionistas. Para esos dos campos,
los tabs "Administradores" y "Accionistas" muestran un flujo asistido: el
auditor obtiene el certificado electrónico oficial (gratuito, sin registro
previo, en el propio portal de Supercías), lo registra como evidencia desde
el tab "Documentos", y luego transcribe la nómina en los formularios ya
existentes. Atlas no extrae datos automáticamente del PDF — no hay parser,
porque no existen todavía muestras reales del certificado para validar uno.

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
- `database.py`: SQLite, usuarios, sesiones, empresas, fuentes y resumen.
- `schema.sql`: esquema de la base de datos.
- `services/`: lógica de negocio pura (validación de RUC, mapa de fuentes,
  indicadores financieros, resumen, ficha final), sin SQL.
- `providers/`: generan enlaces e instrucciones hacia fuentes oficiales
  externas (SRI, Supercías); nunca hacen scraping ni guardan credenciales.
- `views/`: construcción de HTML por pantalla (`admin/`, `auditor/`,
  `auditor/radar/` para el Radar Empresarial).
- `ui/`: componentes, helpers e íconos HTML reutilizables entre vistas.
- `scripts/`: herramientas de importación de catálogos locales
  (`update_catastro.py` para el SRI, `update_supercias_catalog.py` para el
  Directorio de Compañías de Supercías) — se ejecutan manualmente, no en
  cada arranque del servidor.
- `auddit.db`: base local generada al primer arranque. `sri_catastro.db` y
  `supercias_catalog.db` son catálogos locales separados, generados por los
  scripts de arriba (ninguno de los tres se versiona en git).
- `tests/`: pruebas unitarias (bases temporales) y de integración HTTP
  (`tests/test_http.py`, requiere el servidor corriendo en `localhost:8765`).
