"""Telas da aplicação."""
from __future__ import annotations

from flask import Flask, request
from flask_login import current_user
from sqlalchemy import select

from ..extensions import db, login_manager
from ..models import Account, Category, PaymentMethod, Product
from ..utils import ValidationError, parse_date, parse_money, today


def register_views(app: Flask) -> None:
    from . import (brand, config_views, contas, dashboard, despesas, encomendas, fluxo, produtos, relatorios, titulos,
                   vendas)

    protected = set()
    for mod in (dashboard, encomendas, vendas, despesas, produtos, contas, titulos, fluxo, relatorios, config_views):
        app.register_blueprint(mod.bp)
        protected.add(mod.bp.name)
    app.register_blueprint(brand.bp)

    @app.before_request
    def _require_login():
        # Todas as rotas dessas áreas exigem login (inclusive POST).
        if request.blueprint in protected and not current_user.is_authenticated:
            return login_manager.unauthorized()
        return None


# --------------------------------------------------------------------------- listas para formulários
def accounts(active_only: bool = True):
    stmt = select(Account).order_by(Account.name)
    if active_only:
        stmt = stmt.where(Account.active.is_(True))
    return db.session.scalars(stmt).all()


def categories(kind: str, groups: tuple[str, ...] | None = None, active_only: bool = True):
    stmt = select(Category).where(Category.kind == kind).order_by(Category.name)
    if groups:
        stmt = stmt.where(Category.group.in_(groups))
    if active_only:
        stmt = stmt.where(Category.active.is_(True))
    return db.session.scalars(stmt).all()


def methods(active_only: bool = True):
    stmt = select(PaymentMethod).order_by(PaymentMethod.name)
    if active_only:
        stmt = stmt.where(PaymentMethod.active.is_(True))
    return db.session.scalars(stmt).all()


def products(active_only: bool = True):
    stmt = select(Product).order_by(Product.name)
    if active_only:
        stmt = stmt.where(Product.active.is_(True))
    return db.session.scalars(stmt).all()


# --------------------------------------------------------------------------- leitura de formulário
def f(name: str, default: str = "") -> str:
    return (request.form.get(name) or default).strip()


def f_int(name: str, default: int | None = None, *, field: str | None = None, lo: int | None = None,
          hi: int | None = None) -> int | None:
    raw = f(name)
    if raw == "":
        return default
    try:
        val = int(raw)
    except ValueError as exc:
        raise ValidationError(f"{field or name}: informe um número inteiro.") from exc
    if (lo is not None and val < lo) or (hi is not None and val > hi):
        raise ValidationError(f"{field or name}: informe um valor entre {lo} e {hi}.")
    return val


def f_money(name: str, *, field: str, required: bool = True, allow_zero: bool = False):
    return parse_money(request.form.get(name), field=field, required=required, allow_zero=allow_zero)


def f_date(name: str, *, field: str, required: bool = True):
    return parse_date(request.form.get(name), field=field, required=required)


def f_bool(name: str) -> bool:
    return request.form.get(name) in ("1", "on", "true", "sim")


def arg_date(name: str, default=None):
    try:
        return parse_date(request.args.get(name), required=False) or default
    except ValidationError:
        return default


def default_date():
    return today()
