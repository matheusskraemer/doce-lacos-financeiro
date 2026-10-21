"""Contas a pagar e a receber."""
from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, render_template, request
from sqlalchemy import or_, select

from ..extensions import db
from ..models import Category, CompanySettings, Title
from ..utils import ZERO, parse_date, q, today
from . import accounts, methods

bp = Blueprint("contas", __name__)

STATUS_LABELS = {"vencido": "Vencidas", "pendente": "A vencer", "parcial": "Liquidadas em parte",
                 "liquidado": "Liquidadas", "cancelado": "Canceladas"}


def _list(kind: str):
    ref = today()
    settings = db.session.get(CompanySettings, 1)
    upcoming_days = settings.upcoming_days if settings else 7
    view = request.args.get("ver") or "abertas"  # abertas | todas | liquidadas
    cat_id = request.args.get("categoria", type=int)
    search = (request.args.get("q") or "").strip()
    try:
        start = parse_date(request.args.get("inicio"), required=False)
        end = parse_date(request.args.get("fim"), required=False)
    except Exception:
        start = end = None

    stmt = select(Title).where(Title.kind == kind).order_by(Title.due_date, Title.id)
    if view == "abertas":
        stmt = stmt.where(Title.canceled_at.is_(None), (Title.amount - Title.settled_amount) > 0)
    elif view == "liquidadas":
        stmt = stmt.where(Title.canceled_at.is_(None), (Title.amount - Title.settled_amount) <= 0)
    if start:
        stmt = stmt.where(Title.due_date >= start)
    if end:
        stmt = stmt.where(Title.due_date <= end)
    if cat_id:
        stmt = stmt.where(Title.category_id == cat_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Title.description.ilike(like), Title.counterparty.ilike(like)))
    rows = db.session.scalars(stmt.limit(2000)).all()

    status_filter = request.args.get("status") or ""
    if status_filter:
        rows = [t for t in rows if t.status_on(ref) == status_filter]

    open_rows = [t for t in rows if t.open_amount > 0]
    soon = ref + timedelta(days=upcoming_days)
    summary = {
        "open": q(sum((t.open_amount for t in open_rows), ZERO)),
        "overdue": q(sum((t.open_amount for t in open_rows if t.due_date < ref), ZERO)),
        "overdue_count": sum(1 for t in open_rows if t.due_date < ref),
        "soon": q(sum((t.open_amount for t in open_rows if ref <= t.due_date <= soon), ZERO)),
        "soon_count": sum(1 for t in open_rows if ref <= t.due_date <= soon),
    }
    cat_kind = "despesa" if kind == "pagar" else "receita"
    cats = db.session.scalars(select(Category).where(Category.kind == cat_kind).order_by(Category.name)).all()
    return render_template("contas/list.html", kind=kind, rows=rows, ref=ref, soon=soon, view=view, cats=cats,
                           cat_id=cat_id, search=search, start=start, end=end, summary=summary,
                           status_filter=status_filter, status_labels=STATUS_LABELS, accounts=accounts(),
                           methods=methods(), upcoming_days=upcoming_days)


@bp.get("/contas-a-pagar")
def pagar():
    return _list("pagar")


@bp.get("/contas-a-receber")
def receber():
    return _list("receber")
