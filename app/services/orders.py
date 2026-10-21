"""Encomendas: regras de negócio.

Integração com o financeiro (sem segundo mecanismo de caixa):
* A encomenda gera UMA venda vinculada (orders.sale_id, único) quando é
  confirmada ou quando recebe o primeiro pagamento (sinal), o que vier antes.
* Pagamentos são liquidações dos títulos dessa venda (finance.settle).
  Recebido e saldo pendente vêm sempre dessas liquidações.
* Mudança de status nunca registra entrada de caixa. "Entregue" não é "pago".
* Cancelar exige motivo; se houver valores recebidos, exige registrar o
  reembolso (estorno explícito). Nada é apagado.
* Valores (itens e descontos) só mudam enquanto não houver nenhum movimento de
  caixa na venda vinculada; se a venda já existe sem movimentos, ela é
  cancelada e gerada de novo com os valores atualizados, com registro.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from ..extensions import db
from ..models import (DELIVERY_MODES, ORDER_CHANNELS, ORDER_OPEN_STATUSES, ORDER_PRIORITIES, ORDER_STATUS_LABELS,
                      PRIORITY_RANK, Order, OrderEvent, OrderItem, PaymentMethod, Product, Sale, Title, utcnow)
from ..utils import ZERO, ValidationError, brl, fmt_date, q, today
from . import finance
from .audit import current_actor, record, snapshot

TZ = ZoneInfo("America/Sao_Paulo")

ORDER_FIELDS = {
    "customer_name": "Cliente", "customer_phone": "Telefone", "channel_label": "Canal", "priority_label": "Prioridade",
    "due_date": "Prazo", "due_time": "Horário", "delivery_label": "Modalidade", "delivery_address": "Endereço",
    "pickup_location": "Local de retirada", "delivery_person": "Responsável pela entrega",
    "payment_method.name": "Forma de pagamento", "balance_due_date": "Vencimento do saldo", "total": "Total",
    "total_discount": "Descontos",
}


def now_local() -> datetime:
    return datetime.now(TZ)


# --------------------------------------------------------------------------- entrada de dados
@dataclass
class OrderItemInput:
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal = ZERO
    product_id: int | None = None
    customization: str | None = None
    notes: str | None = None


@dataclass
class OrderInput:
    customer_name: str
    due_date: date
    items: list[OrderItemInput] = field(default_factory=list)
    customer_phone: str | None = None
    contact_notes: str | None = None
    channel: str = "whatsapp"
    priority: str = "normal"
    due_time: time | None = None
    delivery_mode: str = "retirada"
    delivery_address: str | None = None
    pickup_location: str | None = None
    delivery_person: str | None = None
    discount: Decimal = ZERO
    payment_method_id: int | None = None
    balance_due_date: date | None = None
    production_notes: str | None = None
    customer_references: str | None = None
    internal_notes: str | None = None
    initial_status: str = "novo"
    request_key: str | None = None
    # sinal no cadastro (opcional)
    deposit_amount: Decimal | None = None
    deposit_date: date | None = None
    deposit_account_id: int | None = None


def _clean(v, n=None):
    v = (v or "").strip() if isinstance(v, str) or v is None else v
    if isinstance(v, str):
        v = v[:n] if n else v
        return v or None
    return v


def _build_items(items: list[OrderItemInput]) -> list[OrderItem]:
    out = []
    for i, it in enumerate(items, start=1):
        product = db.session.get(Product, it.product_id) if it.product_id else None
        if it.product_id and product is None:
            raise ValidationError(f"Item {i}: produto não encontrado.")
        desc = (it.description or "").strip() or (product.name if product else "")
        if not desc:
            raise ValidationError(f"Item {i}: informe o produto ou a descrição.")
        price, disc = q(it.unit_price), q(it.discount or ZERO)
        if price < 0:
            raise ValidationError(f"Item {i}: o valor unitário não pode ser negativo.")
        if disc < 0 or disc > q(it.quantity * price):
            raise ValidationError(f"Item {i}: o desconto não pode passar do valor do item.")
        out.append(OrderItem(product_id=product.id if product else None, description=desc[:160],
                             customization=_clean(it.customization), quantity=it.quantity, unit_price=price,
                             discount=disc, notes=_clean(it.notes)))
    if not out:
        raise ValidationError("Adicione ao menos um item à encomenda.")
    return out


def _validate_header(data: OrderInput) -> None:
    if not (data.customer_name or "").strip():
        raise ValidationError("Cliente: informe o nome.")
    if data.channel not in ORDER_CHANNELS:
        raise ValidationError("Canal de origem inválido.")
    if data.priority not in ORDER_PRIORITIES:
        raise ValidationError("Prioridade inválida.")
    if data.delivery_mode not in DELIVERY_MODES:
        raise ValidationError("Modalidade de entrega inválida.")
    if data.delivery_mode in ("entrega", "transportadora") and not (data.delivery_address or "").strip():
        raise ValidationError("Endereço de entrega: obrigatório para entrega própria ou transportadora.")
    if data.payment_method_id and db.session.get(PaymentMethod, data.payment_method_id) is None:
        raise ValidationError("Forma de pagamento não encontrada.")
    if data.discount is not None and q(data.discount) < 0:
        raise ValidationError("Desconto: não pode ser negativo.")


def _apply_header(order: Order, data: OrderInput) -> None:
    order.customer_name = data.customer_name.strip()[:120]
    order.customer_phone = _clean(data.customer_phone, 40)
    order.contact_notes = _clean(data.contact_notes)
    order.channel = data.channel
    order.priority = data.priority
    order.due_date = data.due_date
    order.due_time = data.due_time
    order.delivery_mode = data.delivery_mode
    order.delivery_address = _clean(data.delivery_address) if data.delivery_mode != "retirada" else None
    order.pickup_location = _clean(data.pickup_location, 200) if data.delivery_mode == "retirada" else None
    order.delivery_person = _clean(data.delivery_person, 120)
    order.payment_method_id = data.payment_method_id or None
    order.balance_due_date = data.balance_due_date
    order.production_notes = _clean(data.production_notes)
    order.customer_references = _clean(data.customer_references)
    order.internal_notes = _clean(data.internal_notes)


def _check_total(order: Order) -> None:
    if q(order.discount) > q(order.gross - order.items_discount):
        raise ValidationError("Desconto do pedido: não pode passar do valor dos itens.")
    if order.total <= 0:
        raise ValidationError("O valor total da encomenda deve ser maior que zero.")


def add_event(order: Order, kind: str, summary: str, *, from_status=None, to_status=None, before=None, after=None):
    uid, name = current_actor()
    db.session.add(OrderEvent(
        order_id=order.id, user_id=uid, username=name, kind=kind, summary=summary, from_status=from_status,
        to_status=to_status, before=json.dumps(before, ensure_ascii=False) if before else None,
        after=json.dumps(after, ensure_ascii=False) if after else None,
    ))


def _locked(order_id: int) -> Order:
    order = db.session.execute(select(Order).where(Order.id == order_id).with_for_update()).scalar_one_or_none()
    if order is None:
        raise ValidationError("Encomenda não encontrada.")
    return order


def _commit():
    finance._commit()


# --------------------------------------------------------------------------- venda vinculada
def ensure_sale(order: Order) -> Sale:
    """Gera (uma única vez) a venda vinculada à encomenda. Não confirma a transação."""
    if order.sale is not None and order.sale.status == "ativa":
        return order.sale
    _check_total(order)
    items = [finance.ItemInput(i.description + (f" ({i.customization})" if i.customization else ""),
                               i.quantity, i.unit_price, i.product_id)
             for i in order.items if i.unit_price > 0]
    sale = finance.create_sale(finance.SaleInput(
        description=f"Encomenda {order.code} — {order.customer_name}"[:160],
        sale_date=today(),
        customer=order.customer_name,
        items=items,
        discount=order.total_discount,
        payment_method_id=order.payment_method_id,
        installments=1,
        first_due_date=order.balance_due_date or order.due_date,
        notes=f"Gerada pela encomenda {order.code}.",
    ), _commit_now=False)
    order.sale_id = sale.id
    db.session.flush()
    db.session.refresh(order, ["sale"])
    add_event(order, "venda", f"Venda #{sale.id} gerada no financeiro: {brl(sale.net_amount)} a receber"
                              f" até {fmt_date(order.balance_due_date or order.due_date)}.")
    return sale


def _sale_has_movements(order: Order) -> bool:
    return bool(order.sale and any(t.settlements for t in order.sale.titles))


# --------------------------------------------------------------------------- operações
def create_order(data: OrderInput) -> Order:
    _validate_header(data)
    if data.request_key and db.session.scalar(select(Order.id).where(Order.request_key == data.request_key)):
        raise finance.DuplicateSubmission("Esta encomenda já foi registrada (o formulário foi enviado duas vezes).")
    if data.initial_status not in ("novo", "aguardando", "confirmado"):
        raise ValidationError("Status inicial inválido.")
    order = Order(request_key=data.request_key, status=data.initial_status, discount=q(data.discount or ZERO),
                  items=_build_items(data.items))
    _apply_header(order, data)
    _check_total(order)
    db.session.add(order)
    db.session.flush()
    add_event(order, "criado", f"Encomenda criada ({ORDER_STATUS_LABELS[order.status]}), total {brl(order.total)}, "
                               f"prazo {fmt_date(order.due_date)}.", to_status=order.status)
    record("criar", "encomenda", order.id, f"Encomenda {order.code} — {order.customer_name}, {brl(order.total)}")
    if order.status == "confirmado":
        ensure_sale(order)
    if data.deposit_amount:
        if not data.deposit_account_id:
            raise ValidationError("Sinal: escolha a conta onde o dinheiro entrou.")
        _pay(order, data.deposit_amount, data.deposit_date or today(), data.deposit_account_id,
             data.payment_method_id, "Sinal no cadastro da encomenda", None)
    _commit()
    return order


def update_order(order_id: int, data: OrderInput) -> Order:
    order = _locked(order_id)
    if not order.is_open:
        raise ValidationError("Encomendas entregues ou canceladas não podem ser editadas.")
    _validate_header(data)
    before = snapshot(order, ORDER_FIELDS)
    old_items = [(i.product_id, i.description, i.customization, q(i.quantity * 1), i.unit_price, i.discount, i.notes)
                 for i in order.items]
    new_items = _build_items(data.items)
    new_sig = [(i.product_id, i.description, i.customization, q(i.quantity * 1), i.unit_price, i.discount, i.notes)
               for i in new_items]
    values_changed = new_sig != old_items or q(data.discount or ZERO) != q(order.discount)
    if values_changed:
        if _sale_has_movements(order):
            raise ValidationError("Há pagamentos (ou estornos) registrados nesta encomenda. Para alterar itens ou "
                                  "valores, cancele a encomenda com reembolso e registre uma nova.")
        order.items = new_items
        order.discount = q(data.discount or ZERO)
    _apply_header(order, data)
    db.session.flush()
    _check_total(order)
    regenerated = False
    if values_changed and order.sale is not None and order.sale.status == "ativa":
        old_sale = order.sale
        finance.cancel_sale(old_sale.id, f"Valores da encomenda {order.code} alterados", reverse_payments=False,
                            _from_order=True, _commit_now=False)
        order.sale_id = None
        db.session.flush()
        db.session.refresh(order, ["sale"])
        ensure_sale(order)
        regenerated = True
    elif order.sale is not None and order.sale.status == "ativa":
        # mantém vencimento e forma do recebimento alinhados ao pedido
        for t in order.sale.titles:
            if t.open_amount > 0 and not t.is_canceled:
                t.due_date = order.balance_due_date or order.due_date
                t.expected_method_id = order.payment_method_id
                t.counterparty = order.customer_name
    db.session.flush()
    db.session.refresh(order, ["payment_method"])
    after = snapshot(order, ORDER_FIELDS)
    summary = "Dados da encomenda alterados" + (" (venda vinculada refeita com os novos valores)" if regenerated else "")
    add_event(order, "edicao", summary, before=before, after=after)
    record("editar", "encomenda", order.id, f"{order.code}: {summary}", before=before, after=after)
    _commit()
    return order


def change_status(order_id: int, to_status: str, *, note: str | None = None, delivered_at: datetime | None = None,
                  delivery_notes: str | None = None) -> Order:
    order = _locked(order_id)
    frm = order.status
    if to_status == "cancelado":
        raise ValidationError("Para cancelar, use a ação Cancelar encomenda (exige motivo).")
    if to_status not in ORDER_STATUS_LABELS:
        raise ValidationError("Etapa inválida.")
    if to_status == frm:
        raise ValidationError("A encomenda já está nesta etapa.")
    if to_status not in order.allowed_next:
        raise ValidationError(f"Não é possível passar de “{ORDER_STATUS_LABELS[frm]}” para "
                              f"“{ORDER_STATUS_LABELS[to_status]}”.")
    if to_status in ("confirmado", "producao", "pronto", "saiu", "entregue"):
        ensure_sale(order)  # gera a venda (a receber); não registra entrada de caixa
    order.status = to_status
    order.status_changed_at = utcnow()
    extra = ""
    if to_status == "entregue":
        order.delivered_at = delivered_at or utcnow()
        order.delivery_notes = _clean(delivery_notes) or order.delivery_notes
        order.blocked, order.blocked_reason = False, None
        if order.pending > 0:
            extra = f" Saldo de {brl(order.pending)} continua a receber."
    summary = f"{ORDER_STATUS_LABELS[frm]} → {ORDER_STATUS_LABELS[to_status]}." + extra + (f" {note.strip()}" if note and note.strip() else "")
    add_event(order, "status", summary, from_status=frm, to_status=to_status)
    record("status", "encomenda", order.id, f"{order.code}: {summary}",
           before={"Status": ORDER_STATUS_LABELS[frm]}, after={"Status": ORDER_STATUS_LABELS[to_status]})
    _commit()
    return order


def set_blocked(order_id: int, blocked: bool, reason: str | None) -> Order:
    order = _locked(order_id)
    if not order.is_open:
        raise ValidationError("Só encomendas em aberto podem ter pendência.")
    reason = (reason or "").strip()
    if blocked and not reason:
        raise ValidationError("Informe o que está pendente.")
    order.blocked, order.blocked_reason = blocked, (reason[:300] if blocked else None)
    add_event(order, "pendencia", f"Pendência marcada: {reason}" if blocked else "Pendência resolvida.")
    _commit()
    return order


def _pay(order: Order, amount: Decimal, pay_date: date, account_id: int, method_id, notes, request_key):
    amount = q(amount)
    if amount <= 0:
        raise ValidationError("Valor: deve ser maior que zero.")
    ensure_sale(order)
    pending = order.pending
    if amount > pending:
        raise ValidationError(f"Valor maior que o saldo pendente ({brl(pending)}).")
    remaining = amount
    first = True
    for t in sorted(order.sale.titles, key=lambda t: t.installment_no):
        if remaining <= 0:
            break
        if t.is_canceled or t.open_amount <= 0:
            continue
        part = min(remaining, t.open_amount)
        finance.settle(t.id, amount=part, settle_date=pay_date, account_id=account_id, payment_method_id=method_id,
                       notes=notes, request_key=request_key if first else None, _commit_now=False)
        first = False
        remaining -= part
        db.session.expire(t)


def register_payment(order_id: int, *, amount: Decimal, pay_date: date, account_id: int, method_id=None,
                     notes: str | None = None, request_key: str | None = None) -> Order:
    order = _locked(order_id)
    if order.status == "cancelado":
        raise ValidationError("Encomenda cancelada não recebe pagamentos.")
    if request_key:
        from ..models import Settlement
        if db.session.scalar(select(Settlement.id).where(Settlement.request_key == request_key)):
            raise finance.DuplicateSubmission("Este pagamento já foi registrado (formulário enviado duas vezes).")
    _pay(order, amount, pay_date, account_id, method_id, notes, request_key)
    _commit()
    return order


def cancel_order(order_id: int, reason: str, *, refund: bool, refund_date: date | None = None) -> Order:
    order = _locked(order_id)
    if not order.is_open:
        raise ValidationError("Esta encomenda já foi entregue ou cancelada.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Informe o motivo do cancelamento.")
    received = order.received
    if received > 0 and not refund:
        raise ValidationError(f"Há {brl(received)} recebidos nesta encomenda. Confirme o reembolso ao cliente "
                              "(estorno) para cancelar.")
    frm = order.status
    if order.sale is not None and order.sale.status == "ativa":
        finance.cancel_sale(order.sale.id, f"Encomenda {order.code} cancelada: {reason}", reverse_payments=True,
                            reversal_date=refund_date or today(), _from_order=True, _commit_now=False)
    order.status = "cancelado"
    order.status_changed_at = order.canceled_at = utcnow()
    order.cancel_reason = reason
    order.canceled_by_id = current_actor()[0]
    order.blocked, order.blocked_reason = False, None
    summary = f"Encomenda cancelada. Motivo: {reason}." + (f" Reembolso de {brl(received)} registrado." if received > 0 else "")
    add_event(order, "cancelamento", summary, from_status=frm, to_status="cancelado")
    record("cancelar", "encomenda", order.id, f"{order.code}: {summary}")
    _commit()
    return order


# --------------------------------------------------------------------------- consultas
def search_orders(*, status: str = "", q_text: str = "", priority: str = "", mode: str = "", prazo: str = "",
                  start: date | None = None, end: date | None = None, sort: str = "prazo", include_closed: bool = True):
    stmt = select(Order)
    if status == "abertas":
        stmt = stmt.where(Order.status.in_(ORDER_OPEN_STATUSES))
    elif status:
        stmt = stmt.where(Order.status == status)
    elif not include_closed:
        stmt = stmt.where(Order.status.in_(ORDER_OPEN_STATUSES))
    if priority:
        stmt = stmt.where(Order.priority == priority)
    if mode:
        stmt = stmt.where(Order.delivery_mode == mode)
    if start:
        stmt = stmt.where(Order.created_at >= datetime.combine(start, time.min, tzinfo=TZ))
    if end:
        stmt = stmt.where(Order.created_at < datetime.combine(end + timedelta(days=1), time.min, tzinfo=TZ))
    ref = today()
    if prazo == "atrasadas":
        stmt = stmt.where(Order.status.in_(ORDER_OPEN_STATUSES), Order.due_date <= ref)
    elif prazo == "hoje":
        stmt = stmt.where(Order.due_date == ref)
    elif prazo == "3dias":
        stmt = stmt.where(Order.due_date.between(ref, ref + timedelta(days=3)))
    elif prazo == "semana":
        stmt = stmt.where(Order.due_date.between(ref, ref + timedelta(days=7)))
    if q_text:
        txt = q_text.strip()
        like = f"%{txt}%"
        conds = [Order.customer_name.ilike(like), Order.customer_phone.ilike(like),
                 Order.id.in_(select(OrderItem.order_id).where(OrderItem.description.ilike(like)))]
        digits = "".join(ch for ch in txt if ch.isdigit())
        if digits and (txt.upper().startswith("DL") or txt.isdigit()):
            conds.append(Order.number == int(digits))
        stmt = stmt.where(or_(*conds))
    rows = db.session.scalars(stmt.limit(2000)).all()
    now = now_local()
    if prazo == "atrasadas":
        rows = [o for o in rows if o.is_late(now)]
    status_order = {k: i for i, k in enumerate(ORDER_STATUS_LABELS)}
    keys = {
        "prazo": lambda o: (o.due_date, o.due_time or time(23, 59), PRIORITY_RANK[o.priority]),
        "prioridade": lambda o: (PRIORITY_RANK[o.priority], o.due_date),
        "entrada": lambda o: o.created_at,
        "status": lambda o: (status_order.get(o.status, 99), o.due_date),
        "atualizacao": lambda o: o.updated_at or o.created_at,
    }
    reverse = sort in ("entrada", "atualizacao")
    rows.sort(key=keys.get(sort, keys["prazo"]), reverse=reverse)
    return rows


def metrics(start: date, end: date) -> dict:
    now = now_local()
    ref = now.date()
    open_orders = db.session.scalars(select(Order).where(Order.status.in_(ORDER_OPEN_STATUSES))).all()
    lo = datetime.combine(start, time.min, tzinfo=TZ)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=TZ)
    delivered = db.session.scalars(select(Order).where(Order.status == "entregue", Order.delivered_at >= lo,
                                                       Order.delivered_at < hi)).all()
    canceled = db.session.scalars(select(Order).where(Order.status == "cancelado", Order.canceled_at >= lo,
                                                      Order.canceled_at < hi)).all()
    received_in = db.session.scalars(select(Order).where(Order.created_at >= lo, Order.created_at < hi)).all()
    return {
        "open": len(open_orders),
        "open_value": q(sum((o.total for o in open_orders), ZERO)),
        "new": sum(1 for o in open_orders if o.status == "novo"),
        "waiting": sum(1 for o in open_orders if o.status == "aguardando"),
        "production": sum(1 for o in open_orders if o.status == "producao"),
        "ready": sum(1 for o in open_orders if o.status in ("pronto", "saiu")),
        "due_today": sum(1 for o in open_orders if o.due_date == ref),
        "late": sum(1 for o in open_orders if o.is_late(now)),
        "pending_value": q(sum((o.pending for o in open_orders), ZERO)),
        "delivered": len(delivered),
        "canceled": len(canceled),
        "received_count": len(received_in),
    }


def alerts(alert_days: int = 2) -> list[dict]:
    """Encomendas que precisam de atenção (alertas dentro da aplicação)."""
    now = now_local()
    ref = now.date()
    out = []
    candidates = db.session.scalars(select(Order).where(or_(Order.status.in_(ORDER_OPEN_STATUSES),
                                                           Order.status == "entregue"))).all()
    for o in candidates:
        if o.status == "entregue":
            if o.pending > 0:
                out.append({"order": o, "kind": "saldo", "label": f"Entregue com saldo de {brl(o.pending)} a receber",
                            "rank": 3})
            continue
        if o.is_late(now):
            days = (ref - o.due_date).days
            out.append({"order": o, "kind": "atrasada",
                        "label": "Prazo vencido hoje" if days == 0 else f"Atrasada há {days} dia{'s' if days > 1 else ''}",
                        "rank": 0})
        elif o.due_date <= ref + timedelta(days=alert_days):
            d = (o.due_date - ref).days
            out.append({"order": o, "kind": "proxima",
                        "label": "Entrega hoje" if d == 0 else ("Entrega amanhã" if d == 1 else f"Entrega em {d} dias"),
                        "rank": 1})
        if o.status in ("novo", "aguardando"):
            out.append({"order": o, "kind": "confirmacao", "label": "Aguardando confirmação" if o.status == "aguardando"
                        else "Pedido novo, ainda não confirmado", "rank": 2})
        if o.status == "pronto":
            out.append({"order": o, "kind": "pronta", "label": "Pronta para entrega", "rank": 2})
        if o.blocked:
            out.append({"order": o, "kind": "pendencia", "label": f"Pendência: {o.blocked_reason}", "rank": 1})
    out.sort(key=lambda a: (a["rank"], a["order"].due_date))
    return out
