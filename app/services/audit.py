"""Autoria e trilha de auditoria.

* `record()` grava uma linha em audit_log com autor (id + nome no momento),
  ação, registro afetado e valores antes/depois.
* Um evento `before_flush` preenche `created_by_id` de qualquer registro novo
  que tenha esse campo, com o usuário autenticado da requisição.
* No PostgreSQL, gatilhos impedem UPDATE e DELETE em audit_log e order_events.
  Só a restauração de backup libera, dentro da própria transação, com
  `SET LOCAL docelacos.allow_history_purge = 'on'`.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from flask import has_request_context
from sqlalchemy import event
from sqlalchemy.orm import Session

from ..extensions import db

GUARDED_TABLES = ("audit_log", "order_events")

GUARD_SQL = [
    """
    CREATE OR REPLACE FUNCTION docelacos_protect_history() RETURNS trigger AS $$
    BEGIN
      IF coalesce(current_setting('docelacos.allow_history_purge', true), '') = 'on' THEN
        RETURN COALESCE(OLD, NEW);
      END IF;
      RAISE EXCEPTION 'O histórico de auditoria (%) não pode ser alterado nem apagado.', TG_TABLE_NAME;
    END;
    $$ LANGUAGE plpgsql
    """,
] + [
    stmt
    for t in GUARDED_TABLES
    for stmt in (
        f"DROP TRIGGER IF EXISTS trg_protect_{t} ON {t}",
        f"CREATE TRIGGER trg_protect_{t} BEFORE UPDATE OR DELETE ON {t} "
        f"FOR EACH ROW EXECUTE FUNCTION docelacos_protect_history()",
    )
]

DROP_GUARD_SQL = [f"DROP TRIGGER IF EXISTS trg_protect_{t} ON {t}" for t in GUARDED_TABLES] + [
    "DROP FUNCTION IF EXISTS docelacos_protect_history()"
]


def install_history_guard(connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    for stmt in GUARD_SQL:
        connection.execute(text(stmt))


def current_actor() -> tuple[int | None, str]:
    if has_request_context():
        from flask_login import current_user
        try:
            if current_user and current_user.is_authenticated:
                return current_user.id, current_user.label
        except Exception:  # noqa: BLE001
            pass
        return None, "Anônimo"
    return None, "Sistema"


def _fmt(v):
    from ..utils import brl, fmt_date
    if v is None:
        return "—"
    if isinstance(v, Decimal):
        return brl(v)
    if isinstance(v, (date, datetime)):
        return fmt_date(v)
    if isinstance(v, bool):
        return "sim" if v else "não"
    return str(v)


def snapshot(obj, fields: dict[str, str]) -> dict:
    """{rótulo: valor formatado} para os campos informados (atributo -> rótulo)."""
    out = {}
    for attr, label in fields.items():
        v = obj
        for part in attr.split("."):
            v = getattr(v, part, None) if v is not None else None
        out[label] = _fmt(v)
    return out


def record(action: str, entity: str, entity_id: int | None, summary: str,
           before: dict | None = None, after: dict | None = None) -> None:
    from ..models import AuditLog
    uid, name = current_actor()
    db.session.add(AuditLog(
        user_id=uid, username=name, action=action, entity=entity, entity_id=entity_id, summary=summary,
        before=json.dumps(before, ensure_ascii=False) if before else None,
        after=json.dumps(after, ensure_ascii=False) if after else None,
    ))


def history(entity: str, entity_id: int):
    from sqlalchemy import select

    from ..models import AuditLog
    return db.session.scalars(
        select(AuditLog).where(AuditLog.entity == entity, AuditLog.entity_id == entity_id).order_by(AuditLog.id)
    ).all()


@event.listens_for(Session, "before_flush")
def _stamp_created_by(session, flush_context, instances):  # noqa: ARG001
    uid, _ = current_actor()
    if uid is None:
        return
    for obj in session.new:
        if hasattr(obj, "created_by_id") and getattr(obj, "created_by_id", None) is None \
                and type(obj).__name__ != "User":
            obj.created_by_id = uid
