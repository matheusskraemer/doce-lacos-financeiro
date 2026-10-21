"""Identidade visual servida a partir do banco (logo e cores trocáveis em Configurações)."""
from __future__ import annotations

import re

from flask import Blueprint, Response, abort, current_app, url_for

from ..extensions import db
from ..models import CompanySettings

bp = Blueprint("brand", __name__)

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULTS = {"primary": "#8E4A67", "accent": "#B8915A", "ink": "#2E2433"}


def _hex(value: str | None, fallback: str) -> str:
    return value if value and HEX.match(value) else fallback


def _rgb(hexv: str) -> tuple[int, int, int]:
    return tuple(int(hexv[i:i + 2], 16) for i in (1, 3, 5))


def _mix(hexv: str, other: str, t: float) -> str:
    a, b = _rgb(hexv), _rgb(other)
    return "#" + "".join(f"{round(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))


@bp.get("/marca.css")
def brand_css():
    s = db.session.get(CompanySettings, 1)
    primary = _hex(s.color_primary if s else None, DEFAULTS["primary"])
    accent = _hex(s.color_accent if s else None, DEFAULTS["accent"])
    ink = _hex(s.color_ink if s else None, DEFAULTS["ink"])
    r, g, b = _rgb(primary)
    css = f""":root {{
  --brand: {primary};
  --brand-rgb: {r}, {g}, {b};
  --brand-strong: {_mix(primary, '#000000', 0.18)};
  --brand-soft: {_mix(primary, '#ffffff', 0.88)};
  --brand-softer: {_mix(primary, '#ffffff', 0.94)};
  --accent: {accent};
  --accent-soft: {_mix(accent, '#ffffff', 0.82)};
  --ink: {ink};
  --ink-2: {_mix(ink, '#ffffff', 0.12)};
  --ink-3: {_mix(ink, '#ffffff', 0.22)};
}}
"""
    resp = Response(css, mimetype="text/css")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.get("/marca/logo")
def logo():
    s = db.session.get(CompanySettings, 1)
    if not s or not s.logo_data:
        return current_app.send_static_file("img/logo-provisorio.svg")
    resp = Response(s.logo_data, mimetype=s.logo_mime or "image/png")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return resp
