"""Fluxo de caixa e transferências entre contas."""
from __future__ import annotations

import json
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from ..extensions import db
from ..models import Transfer
from ..services import finance
from ..services import metrics as m
from ..utils import ValidationError, add_months, month_end, month_start, parse_date, today
from . import accounts, f, f_date, f_int, f_money

bp = Blueprint("fluxo", __name__)


@bp.get("/fluxo-de-caixa")
def index():
    ref = today()
    gran = request.args.get("visao") or "dia"
    if gran not in ("dia", "semana", "mes"):
        gran = "dia"
    defaults = {
        "dia": (ref - timedelta(days=7), ref + timedelta(days=30)),
        "semana": (ref - timedelta(days=28), ref + timedelta(days=56)),
        "mes": (add_months(month_start(ref), -5), month_end(add_months(ref, 3))),
    }
    try:
        start = parse_date(request.args.get("inicio"), required=False) or defaults[gran][0]
        end = parse_date(request.args.get("fim"), required=False) or defaults[gran][1]
    except ValidationError:
        start, end = defaults[gran]
    if start > end:
        start, end = end, start
    if (end - start).days > 800:
        end = start + timedelta(days=800)
    flow = m.cash_flow(start, end, gran, ref)
    chart = {
        "labels": [r["label"] for r in flow["rows"]],
        "realized": [float(r["realized_balance"]) if r["realized_balance"] is not None else None for r in flow["rows"]],
        "projected": [float(r["projected_balance"]) if r["projected_balance"] is not None else None for r in flow["rows"]],
        "in": [float(r["realized_in"]) for r in flow["rows"]],
        "out": [float(r["realized_out"]) for r in flow["rows"]],
        "pin": [float(r["projected_in"]) for r in flow["rows"]],
        "pout": [float(r["projected_out"]) for r in flow["rows"]],
    }
    return render_template("fluxo/index.html", flow=flow, gran=gran, start=start, end=end, ref=ref,
                           chart=chart, balances=m.account_balances(ref))


@bp.get("/transferencias")
def transferencias():
    rows = db.session.scalars(select(Transfer).order_by(Transfer.date.desc(), Transfer.id.desc()).limit(500)).all()
    return render_template("fluxo/transferencias.html", rows=rows, accounts=accounts(), today=today())


@bp.post("/transferencias")
def nova_transferencia():
    try:
        finance.create_transfer(
            from_account_id=f_int("from_account_id", field="Conta de origem"),
            to_account_id=f_int("to_account_id", field="Conta de destino"),
            transfer_date=f_date("date", field="Data"),
            amount=f_money("amount", field="Valor"),
            notes=f("notes"),
            request_key=f("request_key") or None,
        )
        flash("Transferência registrada. O resultado financeiro não muda; só os saldos das contas.", "success")
    except finance.DuplicateSubmission as exc:
        flash(str(exc), "warning")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("fluxo.transferencias"))


@bp.post("/transferencias/<int:transfer_id>/estornar")
def estornar_transferencia(transfer_id: int):
    db.session.get(Transfer, transfer_id) or abort(404)
    try:
        finance.reverse_transfer(transfer_id, f("reason"))
        flash("Transferência estornada.", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("fluxo.transferencias"))
