"""Doce Laços — Sistema de Gestão Financeira."""
from __future__ import annotations

import os
import secrets

from flask import Flask, g, redirect, render_template, request
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import CONFIGS
from .extensions import csrf, db, login_manager, migrate


def create_app(config_name: str | None = None, **overrides) -> Flask:
    config_name = config_name or os.environ.get("APP_ENV", "production")
    app = Flask(__name__)
    app.config.from_object(CONFIGS[config_name])
    app.config.update(overrides)

    if config_name == "production":
        if not app.config.get("SECRET_KEY") or len(app.config["SECRET_KEY"]) < 32:
            raise RuntimeError("SECRET_KEY ausente ou curta demais (mínimo 32 caracteres). Defina-a nas variáveis de ambiente.")
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            raise RuntimeError("DATABASE_URL não definida.")

    hops = app.config.get("TRUST_PROXY_HOPS", 0)
    if hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    csrf.init_app(app)
    login_manager.init_app(app)

    from . import models  # noqa: F401  (registra os modelos)
    from .auth import bp as auth_bp
    from .cli import register_cli
    from .views import register_views

    app.register_blueprint(auth_bp)
    register_views(app)
    register_cli(app)
    _register_template_helpers(app)
    _register_security(app)
    _register_errors(app)
    return app


def _register_template_helpers(app: Flask) -> None:
    from .models import CompanySettings
    from .utils import brl, fmt_date, month_label, number_br, today

    app.jinja_env.filters["brl"] = brl
    app.jinja_env.filters["data"] = fmt_date
    app.jinja_env.filters["mes"] = month_label
    app.jinja_env.filters["num"] = number_br
    app.jinja_env.filters["money_input"] = lambda v: "" if v is None else f"{v:.2f}".replace(".", ",")

    @app.context_processor
    def inject_globals():
        settings = None
        try:
            settings = db.session.get(CompanySettings, 1)
        except Exception:
            db.session.rollback()
        return {
            "company": settings,
            "today": today(),
            "new_request_key": lambda: secrets.token_urlsafe(24),
            "csp_nonce": getattr(g, "csp_nonce", ""),
        }


def _register_security(app: Flask) -> None:
    @app.before_request
    def _before():
        g.csp_nonce = secrets.token_urlsafe(16)
        if app.config.get("FORCE_HTTPS") and not request.is_secure and request.endpoint != "health":
            return redirect(request.url.replace("http://", "https://", 1), code=301)
        return None

    @app.after_request
    def _headers(resp):
        csp = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
            "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        resp.headers.setdefault("Content-Security-Policy", csp)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if app.config.get("FORCE_HTTPS"):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.endpoint and not request.endpoint.startswith("static"):
            # Páginas com dados financeiros não ficam em cache compartilhado.
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.get("/healthz", endpoint="health")
    def health():
        from sqlalchemy import text
        db.session.execute(text("select 1"))
        return {"status": "ok"}


def _register_errors(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return render_template("errors/error.html", code=400, title="Sessão expirada",
                               message="O formulário expirou ou foi enviado de fora do sistema. Volte, recarregue a página e tente de novo."), 400

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/error.html", code=403, title="Acesso negado",
                               message="Você não tem permissão para esta ação."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/error.html", code=404, title="Página não encontrada",
                               message="O endereço não existe ou o registro foi removido."), 404

    @app.errorhandler(413)
    def too_large(e):
        return render_template("errors/error.html", code=413, title="Arquivo grande demais",
                               message="Envie um arquivo de até 2 MB."), 413

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return render_template("errors/error.html", code=500, title="Algo deu errado",
                               message="O erro foi registrado e nenhum dado foi alterado. Tente novamente em instantes."), 500
