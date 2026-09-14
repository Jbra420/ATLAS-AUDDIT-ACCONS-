"""
core/router.py — Tabla declarativa de rutas GET y POST de Atlas.

Cada ruta mapea a un callable que recibe (handler, user, query/form)
y retorna el HTML a enviar, O None si el handler ya se encargó de responder.
"""
from __future__ import annotations

# Las importaciones de vistas se hacen aquí, centralizadas.
# Agregar una nueva ruta = añadir una línea a estas tablas.

from views import auth_views
from views.admin import dashboard as admin_dashboard
from views.admin import users as admin_users
from views.admin import companies as admin_companies
from views.admin import audit_detail as admin_audit_detail
from views.auditor import dashboard as auditor_dashboard
from views.auditor.radar import page as radar_page


# ── Rutas GET ────────────────────────────────────────────────────────────────
# Format: path -> (requires_admin, requires_user, render_fn(user, query) -> str)
GET_ROUTES: dict[str, tuple[bool, bool, object]] = {
    "/login":           (False, False, lambda u, q, p: auth_views.render_login_page(u, q, p)),
    "/admin":           (True,  False, lambda u, q, p: admin_dashboard.render(u, q, p)),
    "/admin/users":     (True,  False, lambda u, q, p: admin_users.render(u, q, p)),
    "/admin/companies": (True,  False, lambda u, q, p: admin_companies.render(u, q, p)),
    "/admin/audit":     (True,  False, lambda u, q, p: radar_page.render(u, q, p)),
    "/auditor":         (False, True,  lambda u, q, p: auditor_dashboard.render(u, q, p)),
    "/auditor/radar":   (False, True,  lambda u, q, p: radar_page.render(u, q, p)),
}


# ── Rutas POST ───────────────────────────────────────────────────────────────
# Format: path -> handler importado por nombre
# Los POST handlers son manejados directamente en core/server.py
# porque realizan redirects, no retornan HTML directamente.
POST_ROUTE_PATHS = [
    "/login",
    "/admin/users",
    "/admin/companies",
    "/auditor/radar",
    "/auditor/source",
    "/auditor/radar/search",
    "/auditor/radar/source-check",
    "/auditor/radar/document",
    "/auditor/radar/financial",
    "/auditor/radar/profile",
    "/auditor/radar/summary",
]
