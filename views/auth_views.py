"""
views/auth_views.py — Páginas de login y logout de Atlas.
"""
from __future__ import annotations

from ui.helpers import esc, form_value
from ui.icons import SVG_ALERT, SVG_CHECK
import ui.layout as layout_state
import sqlite3


def render_login_page(user: sqlite3.Row | None = None, query: dict = None, active_path: str = "") -> str:
    """Retorna el HTML completo de la página de login (sin layout global)."""
    if query is None:
        query = {}
    err = form_value(query, "err")
    msg = form_value(query, "msg")
    message = err or msg
    error = bool(err)

    # En lugar de un mensaje invasivo, transformamos el botón de ingreso
    # para que muestre el estado de error/éxito temporalmente.
    btn_class = "btn-primary"
    btn_text = "Ingresar a Atlas"
    btn_style = "width:100%; padding:12px; font-size:15px; transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);"
    
    if message:
        if error:
            btn_class = "btn-error-state"
            btn_text = f"{SVG_ALERT} {esc(message)}"
            btn_style += " background-color: #ef4444; color: white; transform: scale(1.02); animation: error-shake 0.4s ease-in-out;"
        else:
            btn_class = "btn-success-state"
            btn_text = f"{SVG_CHECK} {esc(message)}"
            btn_style += " background-color: #10b981; color: white; transform: scale(1.02);"

    auto_hide_js = ""
    if message:
        auto_hide_js = """
        <script>
          setTimeout(() => {
            const btn = document.getElementById('login-btn');
            if (btn) {
              btn.style.backgroundColor = '';
              btn.style.color = '';
              btn.style.transform = 'scale(1)';
              btn.className = 'btn-primary';
              btn.innerHTML = 'Ingresar a Atlas';
            }
          }, 3500);
        </script>
        """

    custom_styles = """
    <style>
      @keyframes error-shake {
        0%, 100% { transform: scale(1.02) translateX(0); }
        25% { transform: scale(1.02) translateX(-4px); }
        75% { transform: scale(1.02) translateX(4px); }
      }
      .btn-error-state, .btn-success-state {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        border: none;
        border-radius: 6px;
        cursor: pointer;
      }
      .btn-error-state svg, .btn-success-state svg {
        width: 18px;
        height: 18px;
      }
    </style>
    """

    body = f"""
    {custom_styles}
    <div class="login-wrap has-auddit-bg">
      <div class="login-card" style="z-index: 1;">
        <div class="login-brand">
          <span class="login-logo">
            <img src="/static/atlas_logo.jpg" alt="Atlas" class="brand-mark brand-mark--login">
            Atlas
          </span>
          <div class="login-tagline">Plataforma de investigación de auditoría · Auddit</div>
        </div>
        <form method="post" action="/login" style="margin-top: 24px;">
          <label for="username">Usuario</label>
          <input id="username" name="username" autocomplete="username"
                 placeholder="nombre de usuario" required>
          <label for="password">Contraseña</label>
          <input id="password" name="password" type="password"
                 autocomplete="current-password" placeholder="••••••••" required>
          <div class="actions" style="margin-top:24px;">
            <button id="login-btn" class="{btn_class}" type="submit" style="{btn_style}">
              {btn_text}
            </button>
          </div>
        </form>
        <p class="login-demo">
          Demo: <code>admin / admin123</code><br><code>auditor / auditor123</code>
        </p>
      </div>
      <div class="login-footer-badge" style="z-index: 1;">
        <img src="/static/logoauddit.jpeg" alt="Auddit" class="login-footer-logo">
        <span>Una plataforma de Auddit</span>
      </div>
    </div>
    """

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ingresar | Atlas</title>
  <style>{layout_state._CSS_CONTENT}</style>
</head>
<body>
  {body}
  {auto_hide_js}
</body>
</html>"""
