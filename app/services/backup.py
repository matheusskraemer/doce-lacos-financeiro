"""Cópia completa e restauração lógica do banco em JSON.

Complementa (não substitui) os backups automáticos da hospedagem e o pg_dump.
O arquivo contém todos os dados financeiros e o hash da senha: trate-o como
confidencial e guarde-o protegido (pasta criptografada ou cofre).
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import LargeBinary, select, text

from ..extensions import db
from ..models import (Account, AuditLog, Category, CompanySettings, PaymentMethod, Product, Purchase, PurchaseItem,
                      Sale, SaleItem, Settlement, Title, Transfer, User, utcnow)

FORMAT = "doce-lacos-backup"
VERSION = 1

# Ordem respeita as chaves estrangeiras.
MODELS = [User, CompanySettings, Account, Category, PaymentMethod, Product, Sale, SaleItem, Purchase, PurchaseItem,
          Title, Settlement, Transfer, AuditLog]


def _encode(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return value


def _decode(column, value):
    if value is None:
        return None
    pytype = None
    try:
        pytype = column.type.python_type
    except NotImplementedError:
        pass
    if isinstance(column.type, LargeBinary):
        return base64.b64decode(value)
    if pytype is Decimal:
        return Decimal(value)
    if pytype is datetime:
        return datetime.fromisoformat(value)
    if pytype is date:
        return date.fromisoformat(value)
    return value


def export_all() -> dict:
    tables = {}
    for model in MODELS:
        cols = model.__table__.columns
        rows = []
        for obj in db.session.execute(select(model.__table__).order_by(model.__table__.c.id)).mappings():
            rows.append({c.name: _encode(obj[c.name]) for c in cols})
        tables[model.__tablename__] = rows
    payload = {"format": FORMAT, "version": VERSION, "created_at": utcnow().isoformat(), "tables": tables}
    body = json.dumps(tables, sort_keys=True, ensure_ascii=False).encode()
    payload["sha256"] = hashlib.sha256(body).hexdigest()
    return payload


def dumps(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")


class RestoreError(Exception):
    pass


def restore_all(payload: dict, *, force: bool = False) -> dict:
    """Restaura em um banco vazio (ou apaga tudo antes, com force=True). Tudo numa transação."""
    if payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise RestoreError("Arquivo não é uma cópia de segurança válida da Doce Laços.")
    tables = payload.get("tables") or {}
    body = json.dumps(tables, sort_keys=True, ensure_ascii=False).encode()
    if payload.get("sha256") and hashlib.sha256(body).hexdigest() != payload["sha256"]:
        raise RestoreError("A soma de verificação não confere: o arquivo foi alterado ou está corrompido.")

    has_data = any(db.session.scalar(select(m.id).limit(1)) for m in MODELS if m is not CompanySettings)
    if has_data and not force:
        raise RestoreError("O banco de destino já tem dados. Use --force para substituí-los.")
    counts = {}
    try:
        for model in reversed(MODELS):
            db.session.execute(model.__table__.delete())
        for model in MODELS:
            rows = tables.get(model.__tablename__, [])
            cols = {c.name: c for c in model.__table__.columns}
            if rows:
                db.session.execute(model.__table__.insert(), [
                    {k: _decode(cols[k], v) for k, v in r.items() if k in cols} for r in rows
                ])
            counts[model.__tablename__] = len(rows)
        if db.engine.dialect.name == "postgresql":
            for model in MODELS:
                t = model.__tablename__
                db.session.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)"
                ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return counts
