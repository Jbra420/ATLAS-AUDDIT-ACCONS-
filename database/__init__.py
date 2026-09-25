"""
database — Capa de persistencia de Atlas (sqlite3 puro, sin dependencias externas).

Es la única capa que escribe en SQLite. Cada módulo agrupa un tema y solo
importa de los que están antes en este orden:

    base → trazabilidad → usuarios, personas, perfil, financiero, certificados
         → catalogos → expedientes → investigacion → esquema

Este archivo re-exporta la API pública: el resto de la app importa siempre
`from database import …`. Para parchear en tests una función que otra usa
internamente, parchee el módulo donde se usa (p. ej. database.esquema).
"""
from __future__ import annotations

from database.base import (
    BASE_DIR,
    DB_PATH,
    now_iso,
    connect,
)
from database.trazabilidad import (
    list_provenance,
)
from database.usuarios import (
    USERNAME_RE,
    hash_password,
    verify_password,
    session_hash,
    create_user,
    authenticate,
    change_password,
    list_users,
    list_auditors,
    deactivate_user,
    reactivate_user,
    soft_delete_user,
    create_session,
    get_csrf_token,
    validate_csrf_token,
    user_from_session,
    destroy_session,
)
from database.personas import (
    list_administrators,
    list_shareholders,
    add_administrator,
    update_administrator,
    delete_administrator,
    add_shareholder,
    update_shareholder,
    delete_shareholder,
)
from database.perfil import (
    get_company_profile,
    upsert_company_profile,
    PROFILE_FORM_FIELDS,
    LOCATION_FORM_FIELDS,
    PROFILE_CLOSED_VALUES,
    update_company_profile_fields,
    update_company_location_fields,
    get_company_location,
)
from database.financiero import (
    get_financial_snapshot,
    FUENTE_AUDITOR_PARAMETRO,
    FUENTE_AUDITOR_TRATAMIENTO,
    fuente_documentos_economicos,
    set_audit_fiscal_year,
    upsert_financial_statement,
    list_financial_statements,
    get_financial_context,
    ALERTAS_CRITICAS,
    register_alert_treatment,
)
from database.certificados import (
    ADJUNTOS_DIR,
    close_certificate_import,
    get_certificate_import,
    save_certificate,
)
from database.catalogos import (
    SRI_CATASTRO_PATH,
    lookup_catastro,
    apply_sri_research_result,
    SUPERCIAS_CATALOG_PATH,
    BALANCES_CATALOG_PATH,
    BALANCES_SOURCE_URL,
    BALANCES_FIELDS,
    lookup_supercias_catalog,
    apply_supercias_research_result,
    lookup_balances_catalog,
    lookup_balance_details,
    apply_balances_catalog_result,
)
from database.expedientes import (
    AUDIT_STATUSES,
    DEFAULT_ECONOMIC_DOCUMENTS,
    create_company_audit,
    register_audit_ruc,
    list_admin_audits,
    reassign_audit,
    archive_audit,
    restore_audit,
    list_auditor_audits,
    get_audit,
    get_audit_context,
    list_economic_documents,
    mark_document_reviewed,
    mark_document_pending,
    load_demo_if_ruc_matches,
)
from database.investigacion import (
    SOURCE_TYPES,
    RESEARCH_FIELDS,
    get_research,
    update_research,
    patch_research,
    refresh_summary,
    list_sources,
    add_source,
    append_research_source_note,
    list_source_checks,
    mark_source_checked,
    mark_matching_source_checked,
    mark_source_pending,
)
from database.esquema import (
    SCHEMA_PATH,
    init_db,
)
