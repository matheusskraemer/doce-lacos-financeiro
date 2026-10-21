"""Autenticação: login, logout e limitação de tentativas."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import delete, func, select

from .extensions import db, login_manager
from .models import LoginAttempt, User, utcnow

bp = Blueprint("auth", __name__)

GENERIC_ERROR = "Usuário ou senha inválidos."
LOCKED_ERROR = "Muitas tentativas sem sucesso. Aguarde alguns minutos e tente novamente."

# Hash de referência para equalizar o tempo de resposta quando o usuário não existe.
_DUMMY_HASH = None


@login_manager.user_loader
def load_user(user_key: str):
    try:
        uid, version = user_key.split(":")
        user = db.session.get(User, int(uid))
    except (ValueError, TypeError):
        return None
    if user is None or not user.active or str(user.session_version) != version:
        return None
    return user


def _client_ip() -> str:
    return (request.remote_addr or "desconhecido")[:64]


def _ip_blocked(ip: str) -> bool:
    cfg = current_app.config
    window = utcnow() - timedelta(minutes=cfg["LOGIN_LOCK_MINUTES"])
    failures = db.session.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.ip == ip, LoginAttempt.success.is_(False), LoginAttempt.created_at >= window
        )
    )
    return failures >= cfg["LOGIN_MAX_FAILURES_PER_IP"]


def _safe_next(target: str | None) -> str:
    if target:
        parsed = urlparse(target)
        if not parsed.netloc and not parsed.scheme and target.startswith("/") and not target.startswith("//"):
            return target
    return url_for("dashboard.index")


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    global _DUMMY_HASH
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    has_user = db.session.scalar(select(func.count(User.id))) > 0
    if request.method == "POST":
        ip = _client_ip()
        username = (request.form.get("username") or "").strip().lower()[:80]
        password = request.form.get("password") or ""
        # limpeza de registros antigos
        db.session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < utcnow() - timedelta(days=30)))

        if _ip_blocked(ip):
            db.session.commit()
            flash(LOCKED_ERROR, "danger")
            return render_template("auth/login.html", username=username, has_user=has_user), 429

        user = db.session.scalar(select(User).where(func.lower(User.username) == username))
        now = utcnow()
        if user and user.locked_until and user.locked_until > now:
            db.session.add(LoginAttempt(ip=ip, username=username, success=False))
            db.session.commit()
            flash(LOCKED_ERROR, "danger")
            return render_template("auth/login.html", username=username, has_user=has_user), 429

        if user is not None and not user.active:
            user.check_password(password)  # mesmo custo de tempo
            db.session.add(LoginAttempt(ip=ip, username=username, success=False))
            db.session.commit()
            flash(GENERIC_ERROR, "danger")
            return render_template("auth/login.html", username=username, has_user=has_user), 401

        if user is None:
            from werkzeug.security import check_password_hash, generate_password_hash
            if _DUMMY_HASH is None:
                _DUMMY_HASH = generate_password_hash("dummy-password", method="scrypt")
            check_password_hash(_DUMMY_HASH, password)
            ok = False
        else:
            ok = user.check_password(password)

        if not ok:
            db.session.add(LoginAttempt(ip=ip, username=username, success=False))
            if user:
                user.failed_attempts = (user.failed_attempts or 0) + 1
                if user.failed_attempts >= current_app.config["LOGIN_MAX_FAILURES_PER_USER"]:
                    user.locked_until = now + timedelta(minutes=current_app.config["LOGIN_LOCK_MINUTES"])
                    user.failed_attempts = 0
            db.session.commit()
            flash(GENERIC_ERROR, "danger")
            return render_template("auth/login.html", username=username, has_user=has_user), 401

        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        db.session.add(LoginAttempt(ip=ip, username=username, success=True))
        db.session.commit()
        session.clear()  # evita fixação de sessão
        login_user(user, remember=False)
        session.permanent = True
        return redirect(_safe_next(request.args.get("next")))

    return render_template("auth/login.html", username="", has_user=has_user)


@bp.post("/sair")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("Você saiu do sistema.", "success")
    return redirect(url_for("auth.login"))
