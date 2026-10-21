"""Produtos e compras de mercadorias."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Product, Purchase, PurchaseItem
from ..services import finance
from ..utils import ZERO, ValidationError, q, resolve_period, today
from . import accounts, f, f_bool, f_date, f_int, f_money, methods, products
from .vendas import read_items

bp = Blueprint("produtos", __name__)

PURCHASE_STATUS = {"paga": "Paga", "parcial": "Paga em parte", "a_pagar": "A pagar", "cancelada": "Cancelada"}


# ------------------------------------------------------------------ produtos
@bp.get("/produtos")
def index():
    search = (request.args.get("q") or "").strip()
    show = request.args.get("mostrar") or "ativos"
    stmt = select(Product).order_by(Product.name)
    if show == "ativos":
        stmt = stmt.where(Product.active.is_(True))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.code.ilike(like), Product.category.ilike(like),
                              Product.supplier.ilike(like)))
    rows = db.session.scalars(stmt).all()
    bought = dict(db.session.execute(
        select(PurchaseItem.product_id, func.sum(PurchaseItem.quantity))
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .where(Purchase.status == "ativa").group_by(PurchaseItem.product_id)
    ).all())
    return render_template("produtos/list.html", rows=rows, search=search, show=show, bought=bought)


def _product_from_form(p: Product) -> Product:
    name = f("name")
    if not name:
        raise ValidationError("Nome do produto: informe o nome.")
    p.name = name[:120]
    p.code = f("code")[:40] or None
    p.category = f("category")[:80] or None
    p.supplier = f("supplier")[:120] or None
    p.unit_cost = f_money("unit_cost", field="Custo unitário", required=False, allow_zero=True) or ZERO
    p.sale_price = f_money("sale_price", field="Preço de venda sugerido", required=False, allow_zero=True)
    p.notes = f("notes") or None
    return p


@bp.route("/produtos/novo", methods=["GET", "POST"])
def new():
    p = Product()
    if request.method == "POST":
        try:
            _product_from_form(p)
            db.session.add(p)
            db.session.commit()
            flash("Produto cadastrado.", "success")
            return redirect(url_for("produtos.detail", product_id=p.id))
        except IntegrityError:
            db.session.rollback()
            flash("Já existe um produto com este código interno.", "danger")
        except ValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("produtos/form.html", p=p, form=request.form)


@bp.route("/produtos/<int:product_id>", methods=["GET", "POST"])
def detail(product_id: int):
    p = db.session.get(Product, product_id) or abort(404)
    if request.method == "POST":
        try:
            if request.form.get("toggle"):
                p.active = not p.active
                db.session.commit()
                flash("Produto reativado." if p.active else "Produto desativado. O histórico foi mantido.", "success")
            else:
                _product_from_form(p)
                db.session.commit()
                flash("Produto atualizado.", "success")
            return redirect(url_for("produtos.detail", product_id=p.id))
        except IntegrityError:
            db.session.rollback()
            flash("Já existe um produto com este código interno.", "danger")
        except ValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    history = db.session.execute(
        select(PurchaseItem, Purchase).join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .where(PurchaseItem.product_id == p.id).order_by(Purchase.purchase_date.desc())
    ).all()
    active_hist = [(i, pu) for i, pu in history if pu.status == "ativa"]
    stats = {
        "qty": sum((i.quantity for i, _ in active_hist), 0),
        "spent": q(sum((i.total for i, _ in active_hist), ZERO)),
    }
    stats["avg"] = q(stats["spent"] / stats["qty"]) if stats["qty"] else None
    return render_template("produtos/form.html", p=p, form={}, history=history, stats=stats)


# ------------------------------------------------------------------ compras
@bp.get("/compras")
def compras():
    period = resolve_period(request.args.get("periodo") or "mes", request.args.get("inicio"), request.args.get("fim"))
    search = (request.args.get("q") or "").strip()
    stmt = (select(Purchase).where(Purchase.purchase_date.between(period.start, period.end))
            .order_by(Purchase.purchase_date.desc(), Purchase.id.desc()))
    if search:
        stmt = stmt.where(Purchase.supplier.ilike(f"%{search}%"))
    rows = db.session.scalars(stmt).all()
    active = [p for p in rows if p.status == "ativa"]
    totals = {"total": q(sum((p.total for p in active), ZERO)), "paid": q(sum((p.paid for p in active), ZERO)),
              "to_pay": q(sum((p.to_pay for p in active), ZERO)), "count": len(active)}
    return render_template("produtos/compras.html", rows=rows, period=period, search=search, totals=totals,
                           statuses=PURCHASE_STATUS)


@bp.route("/compras/nova", methods=["GET", "POST"])
def nova_compra():
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        try:
            paid_now = f_bool("paid_now")
            data = finance.PurchaseInput(
                purchase_date=f_date("purchase_date", field="Data da compra"),
                supplier=f("supplier"),
                items=read_items(price_field="item_price"),
                installments=1 if paid_now else f_int("installments", 1, field="Parcelas", lo=1, hi=24),
                first_due_date=None if paid_now else f_date("first_due_date", field="Primeiro vencimento", required=False),
                paid_now=paid_now,
                account_id=f_int("account_id"),
                payment_method_id=f_int("method_id"),
                notes=f("notes"),
                update_product_cost=f_bool("update_cost"),
                request_key=f("request_key") or None,
            )
            if paid_now and not data.account_id:
                raise ValidationError("Conta financeira: escolha de onde saiu o dinheiro.")
            purchase = finance.create_purchase(data)
            flash("Compra registrada" + (" e paga." if paid_now else ". As parcelas estão em Contas a pagar."), "success")
            return redirect(url_for("produtos.compra", purchase_id=purchase.id))
        except finance.DuplicateSubmission as exc:
            flash(str(exc), "warning")
            return redirect(url_for("produtos.compras"))
        except ValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("produtos/compra_form.html", form=form, accounts=accounts(), methods=methods(),
                           products=products(), today=today())


@bp.get("/compras/<int:purchase_id>")
def compra(purchase_id: int):
    p = db.session.get(Purchase, purchase_id) or abort(404)
    return render_template("produtos/compra_detail.html", p=p, statuses=PURCHASE_STATUS, ref=today(),
                           accounts=accounts(), methods=methods())


@bp.post("/compras/<int:purchase_id>/cancelar")
def cancelar_compra(purchase_id: int):
    try:
        finance.cancel_purchase(purchase_id, f("reason"), reverse_payments=True,
                                reversal_date=f_date("reversal_date", field="Data do estorno", required=False))
        flash("Compra cancelada. Pagamentos feitos foram estornados.", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("produtos.compra", purchase_id=purchase_id))
