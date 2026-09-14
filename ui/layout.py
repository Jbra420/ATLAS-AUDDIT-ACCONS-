"""
ui/layout.py — Layout HTML global de Atlas (topbar, shell, flash messages).
Este módulo es el "frame" de todas las páginas de la aplicación.
"""
from __future__ import annotations

import sqlite3

from ui.helpers import esc
from ui.icons import SVG_ALERT, SVG_CHECK, SVG_LOGO, SVG_LOGOUT

# CSS se inyecta desde afuera (cargado en startup desde static/atlas.css)
_CSS_CONTENT: str = ""


def set_css(css: str) -> None:
    """Registra el contenido CSS a inyectar en el layout. Llamar en startup."""
    global _CSS_CONTENT
    _CSS_CONTENT = css


def _avatar_initials(name: str) -> str:
    parts = (name or "?").split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper() if name else "?"


def layout(
    title: str,
    user: sqlite3.Row | None,
    content: str,
    flash: str = "",
    active_path: str = "/",
) -> str:
    """Genera el HTML completo de una página con topbar, nav y contenido."""
    topbar_html = ""

    if user:
        role = user["role"]
        svg_dash = '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>'  # noqa: E501
        svg_users = '<svg viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>'  # noqa: E501
        svg_comp = '<svg viewBox="0 0 24 24"><rect x="4" y="2" width="16" height="20" rx="2" ry="2"></rect><line x1="12" y1="18" x2="12.01" y2="18"></line></svg>'  # noqa: E501
        svg_radar = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="22" y1="12" x2="18" y2="12"></line><line x1="6" y1="12" x2="2" y2="12"></line><line x1="12" y1="6" x2="12" y2="2"></line><line x1="12" y1="22" x2="12" y2="18"></line></svg>'  # noqa: E501
        svg_folder = '<svg viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>'

        if role == "admin":
            nav_items = [
                ("/admin", svg_dash, "Panel General"),
                ("/admin/companies", svg_comp, "Empresas"),
                ("/admin/users", svg_users, "Usuarios"),
            ]
            role_label = "Jefe Auditor"
        else:
            nav_items = [
                ("/auditor", svg_folder, "Mis Expedientes"),
            ]
            role_label = "Auditor"

        nav_html = "".join(
            f'<a href="{href}" class="{"active" if active_path.startswith(href) and (href != "/admin" or active_path == "/admin") else ""}">{icon}{esc(label)}</a>'  # noqa: E501
            for href, icon, label in nav_items
        )

        initials = esc(_avatar_initials(user["full_name"]))

        topbar_html = f"""
        <header class="topbar">
          <div class="topbar-left">
            <a href="/" class="topbar-brand">
              <div class="topbar-brand-icon">{SVG_LOGO}</div>
              <div class="topbar-brand-text">
                Atlas
                <span class="topbar-brand-sub">Auddit</span>
              </div>
            </a>
            <nav class="topbar-nav">
              {nav_html}
            </nav>
          </div>
          <div class="topbar-right">
            <div class="userbox">
              <div class="userbox-avatar">{initials}</div>
              <div class="userbox-info">
                <span class="userbox-name">{esc(user['full_name'])}</span>
                <span class="userbox-role">{esc(role_label)}</span>
              </div>
            </div>
            <a class="logout-link" href="/logout" title="Cerrar sesión">{SVG_LOGOUT}</a>
          </div>
        </header>
        """

    flash_html = ""
    if flash:
        if "✗" in flash or "Error" in flash:
            flash_html = f'<div class="error-msg">{SVG_ALERT} {esc(flash.replace("✗ ", ""))}</div>'
        else:
            flash_html = f'<div class="flash">{SVG_CHECK} {esc(flash.replace("✓ ", ""))}</div>'

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Atlas — Plataforma de auditoría · Auddit">
  <title>{esc(title)} | Atlas</title>
  <style>{_CSS_CONTENT}</style>
</head>
<body>
<div class="atlas-wrap">
  {topbar_html}
  <main class="main-content">
    <div class="shell">
      {flash_html}
      {content}
    </div>
  </main>
</div>
</body>
</html>"""
