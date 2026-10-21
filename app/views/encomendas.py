"""Encomendas: painel (Kanban e tabela), cadastro, detalhe e ações."""
from __future__ import annotations

from datetime import datetime, time

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from ..extensions import db
from ..models import (DELIVERY_MODES, ORDER_CHANNELS, ORDER_OPEN_STATUSES, ORDER_PRIORITIES, ORDER_STATUS_LABELS,
                      ORDER_STATUSES, CompanySettings, Order)
from ..services import finance
from ..services import orders as svc
from ..utils import ZERO, ValidationError, parse_date, parse_money, parse_quantity, resolve_period, today
from . import accounts, f, f_bool, f_date, f_int, f_money, methods, products

bp = Blueprint("encomendas", __name__, url_prefix="/encomendas")

BOARD_COLUMNS = [k for k, _, is_open in ORDER_STATUSES if is_open] + ["entregue"]


def _parse_time(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError as exc:
        raise ValidationError("Horário: use o formato HH:MM.") from exc


def _read_items() -> list[svc.OrderItemInput]:
    g = request.form.getlist
    prods, descs, custom = g("item_product"), g("item_desc"), g("item_custom")
    qtys, prices, discs, notes = g("item_qty"), g("item_price"), g("item_discount"), g("item_notes")
    out = []
    for i in range(max(len(descs), len(prods))):
        get = lambda lst: lst[i] if i < len(lst) else ""  # noqa: E731
        if not get(prods) and not get(descs).strip() and not get(prices).strip():
            continue
        n = i + 1
        out.append(svc.OrderItemInput(
            description=get(descs).strip(),
            customization=get(custom).strip() or None,
            quantity=parse_quantity(get(qtys) or "1", field=f"Item {n} — quantidade"),
            unit_price=parse_money(get(prices) or "0", field=f"Item {n} — valor unitário", allow_zero=True),
            discount=parse_money(get(discs), field=f"Item {n} — desconto", required=False, allow_zero=True) or ZERO,
            product_id=int(get(prods)) if get(prods).isdigit() else None,
            notes=get(notes).strip() or None,
        ))
    return out


def _read_order(include_initial: bool = True) -> svc.OrderInput:
    deposit = f_money("deposit_amount", field="Sinal", required=False, allow_zero=True) if include_initial else None
    return svc.OrderInput(
        customer_name=f("customer_name"),
        customer_phone=f("customer_phone"),
        contact_notes=f("contact_notes"),
        channel=f("channel") or "outro",
        priority=f("priority") or "normal",
        due_date=f_date("due_date", field="Data prometida"),
        due_time=_parse_time(f("due_time")),
        delivery_mode=f("delivery_mode") or "retirada",
        delivery_address=f("delivery_address"),
        pickup_location=f("pickup_location"),
        delivery_person=f("delivery_person"),
        discount=f_money("discount", field="Desconto do pedido", required=False, allow_zero=True) or ZERO,
        payment_method_id=f_int("method_id"),
        balance_due_date=f_date("balance_due_date", field="Vencimento do saldo", required=False),
        production_notes=f("production_notes"),
        customer_references=f("customer_references"),
        internal_notes=f("internal_notes"),
        items=_read_items(),
        initial_status=(f("initial_status") or "novo") if include_initial else "novo",
        request_key=(f("request_key") or None) if include_initial else None,
        deposit_amount=deposit or None,
        deposit_date=f_date("deposit_date", field="Data do sinal", required=False) if deposit else None,
        deposit_account_id=f_int("deposit_account_id") if deposit else None,
    )


def _settings():
    return db.session.get(CompanySettings, 1)


def _ctx(**kw):
    return dict(statuses=ORDER_STATUS_LABELS, channels=ORDER_CHANNELS, priorities=ORDER_PRIORITIES,
                modes=DELIVERY_MODES, accounts=accounts(), methods=methods(), products=products(), today=today(), **kw)


# ------------------------------------------------------------------ painel
@bp.get("/")
def index():
    view = request.args.get("ver") or "quadro"
    period = resolve_period(request.args.get("periodo") or "mes", request.args.get("inicio"), request.args.get("fim"))
    filters = dict(
        status=request.args.get("status", ""), q_text=(request.args.get("q") or "").strip()[:80],
        priority=request.args.get("prioridade", ""), mode=request.args.get("modalidade", ""),
        prazo=request.args.get("prazo", ""), sort=request.args.get("ordem", "prazo"),
    )
    entry_start = parse_date(request.args.get("entrada_de"), required=False) if request.args.get("entrada_de") else None
    entry_end = parse_date(request.args.get("entrada_ate"), required=False) if request.args.get("entrada_ate") else None
    settings = _settings()
    alert_days = settings.order_alert_days if settings else 2
    now = svc.now_local()
    if view == "tabela":
        rows = svc.search_orders(start=entry_start, end=entry_end, **filters)
        columns = None
    else:
        rows = svc.search_orders(start=entry_start, end=entry_end, include_closed=False,
                                 **{**filters, "status": ""})
        recent_done = [o for o in svc.search_orders(status="entregue", sort="atualizacao", q_text=filters["q_text"],
                                                    priority=filters["priority"], mode=filters["mode"])
                       if o.delivered_at and (now - o.delivered_at.astimezone(svc.TZ)).days < 7]
        columns = {k: [] for k in BOARD_COLUMNS}
        for o in rows + recent_done:
            columns[o.status].append(o)
    return render_template(
        "encomendas/index.html", view=view, rows=rows, columns=columns, period=period, filters=filters,
        entry_start=entry_start, entry_end=entry_end, m=svc.metrics(period.start, period.end),
        alerts=svc.alerts(alert_days), now=now, alert_days=alert_days, **_ctx())


# ------------------------------------------------------------------ cadastro e edição
@bp.route("/nova", methods=["GET", "POST"])
def new():
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        try:
            order = svc.create_order(_read_order())
            flash(f"Encomenda {order.code} registrada.", "success")
            return redirect(url_for("encomendas.detail", order_id=order.id))
        except finance.DuplicateSubmission as exc:
            flash(str(exc), "warning")
            return redirect(url_for("encomendas.index"))
        except ValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("encomendas/form.html", form=form, order=None, **_ctx())


@bp.route("/<int:order_id>/editar", methods=["GET", "POST"])
def edit(order_id: int):
    order = db.session.get(Order, order_id) or abort(404)
    if request.method == "POST":
        try:
            svc.update_order(order_id, _read_order(include_initial=False))
            flash("Encomenda atualizada.", "success")
            return redirect(url_for("encomendas.detail", order_id=order_id))
        except ValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return render_template("encomendas/form.html", form=request.form, order=order, **_ctx())
    if not order.is_open:
        flash("Encomendas entregues ou canceladas não podem ser editadas.", "warning")
        return redirect(url_for("encomendas.detail", order_id=order_id))
    return render_template("encomendas/form.html", form={}, order=order, **_ctx())


@bp.get("/<int:order_id>")
def detail(order_id: int):
    order = db.session.get(Order, order_id) or abort(404)
    from ..services.audit import history
    return render_template("encomendas/detail.html", o=order, now=svc.now_local(), audit=history("encomenda", order.id),
                           **_ctx())


# ------------------------------------------------------------------ ações
def _wants_json() -> bool:
    return request.is_json or request.accept_mimetypes.best == "application/json"


@bp.post("/<int:order_id>/status")
def status(order_id: int):
    data = request.get_json(silent=True) or request.form
    to = (data.get("to") or "").strip()
    try:
        delivered = None
        if to == "entregue" and data.get("delivered_date"):
            d = parse_date(data.get("delivered_date"), field="Data da entrega")
            delivered = datetime.combine(d, _parse_time(data.get("delivered_time")) or svc.now_local().time(),
                                         tzinfo=svc.TZ)
        order = svc.change_status(order_id, to, note=data.get("note"), delivered_at=delivered,
                                  delivery_notes=data.get("delivery_notes"))
        if _wants_json():
            return jsonify(ok=True, status=order.status, label=order.status_label)
        flash(f"{order.code}: {order.status_label}.", "success")
    except ValidationError as exc:
        db.session.rollback()
        if _wants_json():
            return jsonify(ok=False, error=str(exc)), 400
        flash(str(exc), "danger")
    nxt = (data.get("next") or "")
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(url_for("encomendas.detail", order_id=order_id))


@bp.post("/<int:order_id>/pagamento")
def pagamento(order_id: int):
    try:
        svc.register_payment(order_id, amount=f_money("amount", field="Valor"), pay_date=f_date("date", field="Data"),
                             account_id=f_int("account_id", field="Conta"), method_id=f_int("method_id"),
                             notes=f("notes"), request_key=f("request_key") or None)
        flash("Pagamento registrado no financeiro.", "success")
    except finance.DuplicateSubmission as exc:
        flash(str(exc), "warning")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("encomendas.detail", order_id=order_id))


@bp.post("/<int:order_id>/cancelar")
def cancelar(order_id: int):
    try:
        order = svc.cancel_order(order_id, f("reason"), refund=f_bool("refund"),
                                 refund_date=f_date("refund_date", field="Data do reembolso", required=False))
        flash(f"Encomenda {order.code} cancelada.", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("encomendas.detail", order_id=order_id))


@bp.post("/<int:order_id>/pendencia")
def pendencia(order_id: int):
    try:
        svc.set_blocked(order_id, f_bool("blocked"), f("reason"))
        flash("Pendência atualizada.", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("encomendas.detail", order_id=order_id))
