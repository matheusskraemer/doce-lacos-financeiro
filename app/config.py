"""Configurações da aplicação por ambiente.

Todos os segredos vêm de variáveis de ambiente. Nada sensível fica no código.
"""
import os
from datetime import timedelta


def _database_url(default: str | None = None) -> str | None:
    url = os.environ.get("DATABASE_URL", default)
    if not url:
        return url
    # Provedores costumam entregar "postgres://"; o SQLAlchemy 2 exige o driver explícito.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Sessão e cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_NAME = "dl_session"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get("SESSION_HOURS", "12")))
    REMEMBER_COOKIE_DURATION = timedelta(seconds=0)

    # CSRF
    WTF_CSRF_TIME_LIMIT = None  # o token vale enquanto durar a sessão
    WTF_CSRF_SSL_STRICT = True

    # Uploads (logotipo)
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    # Proteção de login
    LOGIN_MAX_FAILURES_PER_IP = int(os.environ.get("LOGIN_MAX_FAILURES_PER_IP", "5"))
    LOGIN_MAX_FAILURES_PER_USER = int(os.environ.get("LOGIN_MAX_FAILURES_PER_USER", "10"))
    LOGIN_LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES", "15"))

    # Atrás do proxy HTTPS da hospedagem (Render, Railway etc.)
    TRUST_PROXY_HOPS = int(os.environ.get("TRUST_PROXY_HOPS", "1"))
    FORCE_HTTPS = True

    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Sao_Paulo")


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")
    SQLALCHEMY_DATABASE_URI = _database_url("postgresql+psycopg://postgres@localhost:5432/docelacos")
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_SSL_STRICT = False
    FORCE_HTTPS = False
    TRUST_PROXY_HOPS = 0


class TestingConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = _database_url(None) if os.environ.get("TEST_USE_DATABASE_URL") else (
        os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/docelacos_test")
    )
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False
    WTF_CSRF_SSL_STRICT = False
    FORCE_HTTPS = False
    TRUST_PROXY_HOPS = 0


class ProductionConfig(BaseConfig):
    DEBUG = False


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
