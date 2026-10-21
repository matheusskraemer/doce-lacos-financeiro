"""Detalhe de um lançamento: liquidar, estornar, editar, cancelar e excluir."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..extensions import db
from ..models import Settlement, Title
from ..services import finance
from ..utils import ValidationError, today
from . import accounts, categories, f, f_bool, f_date, f_int, f_money, methods

bp = Blueprint("titulos", __name__)


def _back(title: Title):
    nxt = request.form.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(url_for("titulos.detail", title_id=title.id))


@bp.get("/lancamentos/<int:title_id>")
def detail(title_id: int):
    title = db.session.get(Title, title_id) or abort(404)
    cat_kind = "receita" if title.kind == "receber" else "despesa"
    return render_template("titles/detail.html", t=title, accounts=accounts(), methods=methods(),
                           cats=categories(cat_kind), ref=today())


@bp.post("/lancamentos/<int:title_id>/liquidar")
def settle(title_id: int):
    title = db.session.get(Title, title_id) or abort(404)
    try:
        finance.settle(
            title_id,
            amount=f_money("amount", field="Valor"),
            settle_date=f_date("date", field="Data"),
            account_id=f_int("account_id", field="Conta"),
            payment_method_id=f_int("method_id"),
            notes=f("notes"),
            request_key=f("request_key") or None,
        )
        verb = "Recebimento" if title.kind == "receber" else "Pagamento"
        flash(f"{verb} registrado.", "success")
    except finance.DuplicateSubmission as exc:
        flash(str(exc), "warning")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return _back(title)


@bp.post("/liquidacoes/<int:settlement_id>/estornar")
def reverse(settlement_id: int):
    s = db.session.get(Settlement, settlement_id) or abort(404)
    title = s.title
    try:
        finance.reverse_settlement(settlement_id, f("reason"), f_date("date", field="Data do estorno", required=False))
        flash("Estorno registrado. O valor voltou a ficar em aberto.", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return _back(title)


@bp.post("/lancamentos/<int:title_id>/editar")
def edit(title_id: int):
    title = db.session.get(Title, title_id) or abort(404)
    try:
        finance.update_title(
            title_id,
            description=f("description"),
            category_id=f_int("category_id", field="Categoria"),
            counterparty=f("counterparty"),
            competence_date=f_date("competence_date", field="Competência"),
            due_date=f_date("due_date", field="Vencimento"),
            amount=f_money("amount", field="Valor"),
            expected_method_id=f_int("method_id"),
            expected_account_id=f_int("account_id"),
            notes=f("notes"),
        )
        flash("Lançamento atualizado.", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return _back(title)


@bp.post("/lancamentos/<int:title_id>/cancelar")
def cancel(title_id: int):
    title = db.session.get(Title, title_id) or abort(404)
    try:
        finance.cancel_title(title_id, f("reason"), reverse_payments=f_bool("reverse_payments"),
                             reversal_date=f_date("reversal_date", field="Data do estorno", required=False))
        flash("Lançamento cancelado.", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return _back(title)


@bp.post("/lancamentos/<int:title_id>/excluir")
def delete(title_id: int):
    title = db.session.get(Title, title_id) or abort(404)
    endpoint = "despesas.index" if title.kind == "pagar" else "vendas.receitas"
    try:
        finance.delete_title(title_id)
        flash("Lançamento excluído.", "success")
        return redirect(url_for(endpoint))
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("titulos.detail", title_id=title_id))
