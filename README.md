# Auddit MVP local

MVP en Python para cubrir la base del proyecto en 45 horas: usuarios con roles,
asignacion de empresas a auditores y herramienta de busqueda asistida para
generar una ficha inicial de la empresa auditada.

## Alcance incluido

- Login local con sesiones.
- Rol `admin`: representa al jefe auditor con acceso de supervisión.
- Rol `auditor`: representa al trabajador/auditor.
- Creacion de usuarios por parte del jefe.
- Registro de empresas y asignacion a auditores.
- Vista del auditor con sus empresas asignadas.
- Vista de supervisión del jefe sobre todos los expedientes en modo lectura.
- Herramienta de busqueda asistida:
  - enlaces a fuentes sugeridas;
  - registro de fuentes consultadas;
  - captura estructurada de informacion;
  - pegado de texto encontrado;
  - extraccion basica de RUC, correos y telefonos;
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

- `app.py`: servidor web local y pantallas.
- `database.py`: SQLite, usuarios, sesiones, empresas, fuentes y resumen.
- `auddit.db`: base local generada al primer arranque.
- `tests/test_database.py`: pruebas basicas del nucleo.
