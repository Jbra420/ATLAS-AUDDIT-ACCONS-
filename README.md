# Auddit MVP local

MVP en Python para cubrir la base del proyecto en 45 horas: usuarios con roles,
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
- `views/`: construcción de HTML por pantalla (`admin/`, `auditor/`,
  `auditor/radar/` para el Radar Empresarial).
- `ui/`: componentes, helpers e íconos HTML reutilizables entre vistas.
- `auddit.db`: base local generada al primer arranque.
- `tests/`: pruebas unitarias (bases temporales) y de integración HTTP
  (`tests/test_http.py`, requiere el servidor corriendo en `localhost:8765`).
