"""Fixtures de teste. Usam PostgreSQL real (TEST_DATABASE_URL), como em produção."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app import create_app
from app.cli import seed_defaults
from app.extensions import db
from app.models import Account, User
from app.utils import today


@pytest.fixture(scope="session")
def app():
    app = create_app("testing")

    # Nos testes, as requisições reaproveitam o contexto da aplicação aberto pela fixture;
    # sem isto, o usuário carregado numa requisição "vazaria" para a próxima (de outro cliente).
    from flask import g
    app.before_request_funcs.setdefault(None, []).insert(0, lambda: g.pop("_login_user", None) and None)
    with app.app_context():
        db.drop_all()
        db.session.execute(text("DROP SEQUENCE IF EXISTS order_number_seq"))
        db.session.commit()
        db.create_all()
        from app.services.audit import install_history_guard
        with db.engine.begin() as conn:
            install_history_guard(conn)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def clean(app):
    with app.app_context():
        tables = ", ".join(t.name for t in reversed(db.metadata.sorted_tables))
        db.session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        db.session.execute(text("ALTER SEQUENCE order_number_seq RESTART WITH 1"))
        db.session.commit()
        seed_defaults()
        yield
        db.session.rollback()
        db.session.remove()


@pytest.fixture
def accounts(app):
    banco = Account(name="Banco", kind="banco", opening_balance=1000, opening_date=today() - timedelta(days=60))
    caixa = Account(name="Caixa", kind="caixa", opening_balance=200, opening_date=today() - timedelta(days=60))
    db.session.add_all([banco, caixa])
    db.session.commit()
    return banco.id, caixa.id


PASSWORD = "SenhaForte2026"


@pytest.fixture
def user(app):
    u = User(username="carol", password_hash="!")
    u.set_password(PASSWORD)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def client(app, user):
    c = app.test_client()
    resp = c.post("/entrar", data={"username": "carol", "password": PASSWORD})
    assert resp.status_code == 302
    return c
