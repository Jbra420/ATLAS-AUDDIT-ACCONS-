"""
ui/layout.py — Layout HTML global de Atlas (topbar, shell, flash messages).
Este módulo es el "frame" de todas las páginas de la aplicación.
"""
from __future__ import annotations

import sqlite3

from ui.components import avatar_initials
from ui.helpers import esc
from ui.icons import SVG_ALERT, SVG_CHECK, SVG_CROSS, SVG_LOGO, SVG_LOGOUT

# Tiempo que un toast (.flash / .error-msg con clase .toast) permanece
# visible antes de desvanecerse solo. Se pausa mientras el cursor está encima.
TOAST_AUTO_DISMISS_MS = 4500

_TOAST_SCRIPT = f"""
<script>
(function() {{
  document.querySelectorAll('.toast').forEach(function(el) {{
    var timer;
    function hide() {{
      el.classList.add('toast-hide');
      el.addEventListener('animationend', function() {{ el.remove(); }}, {{ once: true }});
    }}
    function schedule() {{ timer = setTimeout(hide, {TOAST_AUTO_DISMISS_MS}); }}
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'toast-close';
    closeBtn.setAttribute('aria-label', 'Cerrar aviso');
    closeBtn.innerHTML = '{SVG_CROSS}';
    closeBtn.addEventListener('click', function() {{ clearTimeout(timer); hide(); }});
    el.appendChild(closeBtn);
    el.addEventListener('mouseenter', function() {{ clearTimeout(timer); }});
    el.addEventListener('mouseleave', schedule);
    schedule();
  }});
}})();
</script>
"""

# CSS se inyecta desde afuera (cargado en startup desde static/atlas.css)
_CSS_CONTENT: str = ""


def set_css(css: str) -> None:
    """Registra el contenido CSS a inyectar en el layout. Llamar en startup."""
    global _CSS_CONTENT
    _CSS_CONTENT = css


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

        initials = esc(avatar_initials(user["full_name"]))

        topbar_html = f"""
        <header class="topbar">
          <div class="topbar-left">
            <a href="/" class="topbar-brand" style="display: flex; align-items: center; text-decoration: none;">
              <img src="/static/atlas_logo.jpg" alt="Atlas" style="height: 28px; border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.15); margin-right: 12px; display: block;">
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
            flash_html = f'<div class="error-msg toast">{SVG_ALERT} {esc(flash.replace("✗ ", ""))}</div>'
        else:
            flash_html = f'<div class="flash toast">{SVG_CHECK} {esc(flash.replace("✓ ", ""))}</div>'

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Atlas — Plataforma de auditoría · Auddit">
  <title>{esc(title)} | Atlas</title>
  <style>{_CSS_CONTENT}</style>
</head>
<body class="has-auddit-bg">
<div class="atlas-wrap">
  {topbar_html}
  <main class="main-content" style="position: relative; z-index: 1;">
    <div class="shell">
      {flash_html}
      {content}
    </div>
  </main>
</div>
{_TOAST_SCRIPT}
</body>
</html>"""
