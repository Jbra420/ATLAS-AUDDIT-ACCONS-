"""
core/router.py — Tabla declarativa de rutas GET de Atlas.

Cada ruta mapea a (requires_admin, requires_user, render_fn), donde
render_fn(user, query, path, csrf_token) -> str | None construye la página
completa. Toda ruta recibe el csrf_token de la sesión activa; las vistas
sin formularios POST simplemente lo ignoran. do_GET() en core/server.py
llama siempre render_fn igual, sin casos especiales por ruta: agregar una
ruta nueva es agregar una línea aquí, sin tocar server.py.

Las rutas POST (/login, /admin/users, /auditor/radar/*, etc.) no están en
una tabla: cada una hace su propia validación y redirect en core/server.py,
que es más simple que forzarlas a un formato común.
"""
from __future__ import annotations

# Las importaciones de vistas se hacen aquí, centralizadas.
# Agregar una nueva ruta = añadir una línea a GET_ROUTES.

from views import auth_views
from views.admin import dashboard as admin_dashboard
from views.admin import users as admin_users
from views.admin import companies as admin_companies
from views.auditor import dashboard as auditor_dashboard
from views.auditor.radar import page as radar_page


# ── Rutas GET ────────────────────────────────────────────────────────────────
# Format: path -> (requires_admin, requires_user, render_fn(user, query, path, csrf_token) -> str)
GET_ROUTES: dict[str, tuple[bool, bool, object]] = {
    "/login":           (False, False, lambda u, q, p, csrf: auth_views.render_login_page(u, q, p)),
    "/admin":           (True,  False, lambda u, q, p, csrf: admin_dashboard.render(u, q, p)),
    "/admin/users":     (True,  False, lambda u, q, p, csrf: admin_users.render(u, q, p, csrf_token=csrf)),
    "/admin/companies": (True,  False, lambda u, q, p, csrf: admin_companies.render(u, q, p, csrf_token=csrf)),
    "/admin/audit":     (True,  False, lambda u, q, p, csrf: radar_page.render(u, q, p, csrf_token=csrf)),
    "/auditor":         (False, True,  lambda u, q, p, csrf: auditor_dashboard.render(u, q, p)),
    "/auditor/radar":   (False, True,  lambda u, q, p, csrf: radar_page.render(u, q, p, csrf_token=csrf)),
}
