"""Vendas e outras receitas."""
from __future__ import annotations

from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import or_, select

from ..extensions import db
from ..models import Sale
from ..services import finance
from ..utils import ZERO, ValidationError, parse_money, parse_quantity, q, resolve_period, today
from . import accounts, f, f_bool, f_date, f_int, f_money, methods, products
from .despesas import title_form, title_list

bp = Blueprint("vendas", __name__)

SALE_STATUS = {"recebida": "Recebida", "parcial": "Recebida em parte", "a_receber": "A receber", "cancelada": "Cancelada"}


def read_items(price_field: str = "item_price") -> list[finance.ItemInput]:
    prods = request.form.getlist("item_product")
    descs = request.form.getlist("item_desc")
    qtys = request.form.getlist("item_qty")
    prices = request.form.getlist(price_field)
    items = []
    for i in range(max(len(descs), len(prods))):
        pid = prods[i] if i < len(prods) else ""
        desc = descs[i] if i < len(descs) else ""
        qty = qtys[i] if i < len(qtys) else ""
        price = prices[i] if i < len(prices) else ""
        if not pid and not desc.strip() and not price.strip():
            continue
        n = i + 1
        items.append(finance.ItemInput(
            description=desc.strip(),
            quantity=parse_quantity(qty or "1", field=f"Item {n} — quantidade"),
            unit_value=parse_money(price, field=f"Item {n} — valor unitário"),
            product_id=int(pid) if pid.isdigit() else None,
        ))
    return items


@bp.get("/vendas")
def index():
    period = resolve_period(request.args.get("periodo") or "mes", request.args.get("inicio"), request.args.get("fim"))
    status = request.args.get("status") or ""
    search = (request.args.get("q") or "").strip()
    stmt = select(Sale).where(Sale.sale_date.between(period.start, period.end)).order_by(Sale.sale_date.desc(), Sale.id.desc())
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Sale.description.ilike(like), Sale.customer.ilike(like)))
    rows = db.session.scalars(stmt.limit(1000)).all()
    if status:
        rows = [s for s in rows if s.receipt_status == status]
    active = [s for s in rows if s.status == "ativa"]
    totals = {
        "net": q(sum((s.net_amount for s in active), ZERO)),
        "received": q(sum((s.received for s in active), ZERO)),
        "to_receive": q(sum((s.to_receive for s in active), ZERO)),
        "count": len(active),
    }
    return render_template("vendas/list.html", rows=rows, period=period, status=status, search=search,
                           totals=totals, statuses=SALE_STATUS)


@bp.route("/vendas/nova", methods=["GET", "POST"])
def new():
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        try:
            received_now = f_bool("received_now")
            data = finance.SaleInput(
                description=f("description"),
                sale_date=f_date("sale_date", field="Data da venda"),
                customer=f("customer"),
                items=read_items(),
                gross_amount=f_money("gross_amount", field="Valor bruto", required=False),
                discount=f_money("discount", field="Desconto", required=False, allow_zero=True) or ZERO,
                payment_method_id=f_int("method_id"),
                account_id=f_int("account_id"),
                received_now=received_now,
                installments=1 if received_now else f_int("installments", 1, field="Parcelas", lo=1, hi=24),
                first_due_date=None if received_now else f_date("first_due_date", field="Data prevista de recebimento", required=False),
                notes=f("notes"),
                request_key=f("request_key") or None,
            )
            if received_now and not data.account_id:
                raise ValidationError("Conta de destino: escolha onde o dinheiro entrou.")
            sale = finance.create_sale(data)
            flash("Venda registrada" + (" e recebida." if received_now else "."), "success")
            if request.form.get("again"):
                return redirect(url_for("vendas.new"))
            return redirect(url_for("vendas.detail", sale_id=sale.id))
        except finance.DuplicateSubmission as exc:
            flash(str(exc), "warning")
            return redirect(url_for("vendas.index"))
        except ValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("vendas/form.html", form=form, accounts=accounts(), methods=methods(),
                           products=products(), today=today())


@bp.get("/vendas/<int:sale_id>")
def detail(sale_id: int):
    sale = db.session.get(Sale, sale_id) or abort(404)
    return render_template("vendas/detail.html", sale=sale, statuses=SALE_STATUS, accounts=accounts(),
                           methods=methods(), ref=today())


@bp.post("/vendas/<int:sale_id>/editar")
def edit(sale_id: int):
    try:
        finance.update_sale_info(sale_id, description=f("description"), customer=f("customer"), notes=f("notes"))
        flash("Dados da venda atualizados.", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("vendas.detail", sale_id=sale_id))


@bp.post("/vendas/<int:sale_id>/cancelar")
def cancel(sale_id: int):
    try:
        finance.cancel_sale(sale_id, f("reason"), reverse_payments=True,
                            reversal_date=f_date("reversal_date", field="Data do estorno", required=False))
        flash("Venda cancelada. Os valores recebidos foram estornados.", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("vendas.detail", sale_id=sale_id))


# ------------------------------------------------------------------ outras receitas
@bp.get("/receitas")
def receitas():
    return title_list("receber", origins=("receita",), template_title="Outras receitas",
                      new_url=url_for("vendas.new_receita"))


@bp.route("/receitas/nova", methods=["GET", "POST"])
def new_receita():
    return title_form("receber")
