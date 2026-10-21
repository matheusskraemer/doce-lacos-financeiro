"""Regras de negócio que alteram dados financeiros.

Todas as funções aqui:
* validam os dados no servidor (levantam ValidationError com mensagem amigável);
* trabalham dentro de uma única transação (commit no fim, rollback em erro);
* bloqueiam o título com SELECT ... FOR UPDATE antes de liquidar/estornar,
  para que duas requisições simultâneas não liquidem o mesmo saldo duas vezes;
* aceitam `request_key` (token único do formulário) para que um duplo clique ou
  reenvio do formulário não crie o mesmo lançamento duas vezes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (Account, AuditLog, Category, PaymentMethod, Product, Purchase, PurchaseItem, Sale, SaleItem,
                      Settlement, Title, Transfer, new_uid, utcnow)
from ..utils import ZERO, ValidationError, add_months, brl, fmt_date, q, split_installments


class DuplicateSubmission(ValidationError):
    """O mesmo formulário foi enviado mais de uma vez."""


# ------------------------------------------------------------------ auxiliares

def audit(action: str, entity: str, entity_id: int | None, summary: str) -> None:
    uid = None
    try:
        if current_user and current_user.is_authenticated:
            uid = current_user.id
    except Exception:  # fora de contexto de requisição (CLI, testes)
        uid = None
    db.session.add(AuditLog(user_id=uid, action=action, entity=entity, entity_id=entity_id, summary=summary))


def _check_request_key(model, request_key: str | None) -> None:
    if request_key and db.session.scalar(select(model.id).where(model.request_key == request_key)):
        raise DuplicateSubmission("Este lançamento já foi registrado (o formulário foi enviado duas vezes).")


def _commit():
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        if "request_key" in str(exc.orig):
            raise DuplicateSubmission("Este lançamento já foi registrado (o formulário foi enviado duas vezes).") from exc
        if "reverses_id" in str(exc.orig):
            raise ValidationError("Este movimento já foi estornado.") from exc
        raise ValidationError("Não foi possível salvar: os dados conflitam com registros existentes.") from exc


def _get(model, obj_id, label: str):
    obj = db.session.get(model, obj_id) if obj_id else None
    if obj is None:
        raise ValidationError(f"{label} não encontrado(a).")
    return obj


def _active_account(account_id) -> Account:
    acc = _get(Account, account_id, "Conta financeira")
    if not acc.active:
        raise ValidationError(f"A conta “{acc.name}” está desativada.")
    return acc


def _category(category_id, kind: str, groups: tuple[str, ...] | None = None) -> Category:
    cat = _get(Category, category_id, "Categoria")
    if cat.kind != kind:
        raise ValidationError("Categoria incompatível com o tipo de lançamento.")
    if groups and cat.group not in groups:
        raise ValidationError("Categoria incompatível com este tipo de lançamento.")
    return cat


def _method(method_id) -> PaymentMethod | None:
    if not method_id:
        return None
    return _get(PaymentMethod, method_id, "Forma de pagamento")


def system_category(group: str, kind: str) -> Category:
    cat = db.session.scalar(
        select(Category).where(Category.group == group, Category.kind == kind, Category.system.is_(True))
        .order_by(Category.id)
    )
    if cat is None:
        raise ValidationError("Categorias padrão não encontradas. Rode o comando de configuração inicial.")
    return cat


def locked_title(title_id) -> Title:
    title = db.session.execute(
        select(Title).where(Title.id == title_id).with_for_update()
    ).scalar_one_or_none()
    if title is None:
        raise ValidationError("Lançamento não encontrado.")
    db.session.refresh(title)  # garante settled_amount atualizado sob o bloqueio
    return title


# ------------------------------------------------------------------ títulos (receitas e despesas)

@dataclass
class TitleInput:
    kind: str  # receber | pagar
    origin: str  # receita | despesa
    description: str
    category_id: int
    amount: Decimal
    competence_date: date
    due_date: date
    counterparty: str | None = None
    expected_method_id: int | None = None
    expected_account_id: int | None = None
    notes: str | None = None
    installments: int = 1
    recurrence_months: int = 1  # repetir mensalmente (1 = sem recorrência)
    settle_now: bool = False
    settle_date: date | None = None
    settle_account_id: int | None = None
    request_key: str | None = None


def create_titles(data: TitleInput) -> list[Title]:
    """Cria uma receita/despesa (com parcelas ou recorrência) e, se pedido, já liquida."""
    if data.kind not in ("receber", "pagar") or data.origin not in ("receita", "despesa"):
        raise ValidationError("Tipo de lançamento inválido.")
    desc = (data.description or "").strip()
    if not desc:
        raise ValidationError("Descrição: informe uma descrição.")
    if len(desc) > 160:
        raise ValidationError("Descrição: use no máximo 160 caracteres.")
    if data.installments < 1 or data.installments > 48:
        raise ValidationError("Parcelas: informe entre 1 e 48.")
    if data.recurrence_months < 1 or data.recurrence_months > 36:
        raise ValidationError("Recorrência: informe entre 1 e 36 meses.")
    if data.installments > 1 and data.recurrence_months > 1:
        raise ValidationError("Use parcelamento ou recorrência, não os dois ao mesmo tempo.")
    if data.settle_now and (data.installments > 1 or data.recurrence_months > 1):
        raise ValidationError("Lançamentos parcelados ou recorrentes são quitados parcela a parcela.")
    cat_kind = "receita" if data.kind == "receber" else "despesa"
    groups = ("outra_receita", "venda") if cat_kind == "receita" else ("operacional", "mercadoria")
    category = _category(data.category_id, cat_kind, groups)
    _method(data.expected_method_id)
    if data.expected_account_id:
        _active_account(data.expected_account_id)
    _check_request_key(Title, data.request_key)

    amount = q(data.amount)
    titles: list[Title] = []
    group_id = new_uid() if data.recurrence_months > 1 else None
    if data.installments > 1:
        parts = split_installments(amount, data.installments)
        plan = [(parts[i], add_months(data.due_date, i), data.competence_date, i + 1, data.installments)
                for i in range(data.installments)]
    else:
        plan = [(amount, add_months(data.due_date, i), add_months(data.competence_date, i), 1, 1)
                for i in range(data.recurrence_months)]

    for idx, (value, due, comp, no, count) in enumerate(plan):
        t = Title(
            kind=data.kind, origin=data.origin, description=desc, category_id=category.id,
            counterparty=(data.counterparty or "").strip() or None, competence_date=comp, due_date=due,
            amount=value, expected_method_id=data.expected_method_id or None,
            expected_account_id=data.expected_account_id or None, installment_no=no, installment_count=count,
            recurrence_group=group_id, notes=(data.notes or "").strip() or None,
            request_key=data.request_key if idx == 0 else None,
        )
        db.session.add(t)
        titles.append(t)
    db.session.flush()

    if data.settle_now:
        account = _active_account(data.settle_account_id or data.expected_account_id)
        sd = data.settle_date or data.due_date
        db.session.add(Settlement(title_id=titles[0].id, account_id=account.id, date=sd, amount=amount,
                                  payment_method_id=data.expected_method_id or None))
    label = "Receita" if data.kind == "receber" else "Despesa"
    audit("criar", "titulo", titles[0].id, f"{label} “{desc}” de {brl(amount)}"
          + (f" em {len(titles)} lançamentos" if len(titles) > 1 else "")
          + (" (quitada no registro)" if data.settle_now else ""))
    _commit()
    return titles


def update_title(title_id: int, *, description: str, category_id: int, counterparty: str | None,
                 competence_date: date, due_date: date, amount: Decimal, expected_method_id=None,
                 expected_account_id=None, notes: str | None = None) -> Title:
    title = locked_title(title_id)
    if title.is_canceled:
        raise ValidationError("Lançamentos cancelados não podem ser editados.")
    desc = (description or "").strip()
    if not desc:
        raise ValidationError("Descrição: informe uma descrição.")
    cat_kind = "receita" if title.kind == "receber" else "despesa"
    cat = _category(category_id, cat_kind)
    amount = q(amount)
    if title.origin in ("venda", "compra") and amount != title.amount:
        raise ValidationError("O valor de parcelas de vendas e compras não é editável. Cancele e registre novamente.")
    if amount < q(title.settled_amount):
        raise ValidationError(f"O valor não pode ser menor do que o já liquidado ({brl(title.settled_amount)}).")
    _method(expected_method_id)
    before = f"{brl(title.amount)} venc. {fmt_date(title.due_date)}"
    title.description = desc
    title.category_id = cat.id
    title.counterparty = (counterparty or "").strip() or None
    title.competence_date = competence_date
    title.due_date = due_date
    title.amount = amount
    title.expected_method_id = expected_method_id or None
    title.expected_account_id = expected_account_id or None
    title.notes = (notes or "").strip() or None
    audit("editar", "titulo", title.id, f"“{desc}”: {before} → {brl(amount)} venc. {fmt_date(due_date)}")
    _commit()
    return title


def delete_title(title_id: int) -> None:
    """Exclusão definitiva só para lançamentos sem nenhum movimento de caixa (nem estornado)."""
    title = locked_title(title_id)
    if title.settlements:
        raise ValidationError("Este lançamento tem movimentações de caixa. Use cancelar (com estorno) para manter o histórico.")
    if title.sale_id or title.purchase_id:
        raise ValidationError("Parcelas de vendas e compras são canceladas pela própria venda/compra.")
    audit("excluir", "titulo", title.id, f"“{title.description}” de {brl(title.amount)} excluído (sem movimentações)")
    db.session.delete(title)
    _commit()


def cancel_title(title_id: int, reason: str, *, reverse_payments: bool = False, reversal_date: date | None = None) -> Title:
    title = locked_title(title_id)
    if title.is_canceled:
        raise ValidationError("Este lançamento já está cancelado.")
    if title.sale_id or title.purchase_id:
        raise ValidationError("Parcelas de vendas e compras são canceladas pela própria venda/compra.")
    _cancel_title_locked(title, reason, reverse_payments, reversal_date)
    _commit()
    return title


def _cancel_title_locked(title: Title, reason: str, reverse_payments: bool, reversal_date: date | None) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Informe o motivo do cancelamento.")
    if q(title.settled_amount) > 0:
        if not reverse_payments:
            raise ValidationError("Há valores já liquidados. Estorne-os antes ou marque a opção de estornar ao cancelar.")
        for s in list(title.settlements):
            if not s.is_reversal and s.reversal is None:
                _reverse_settlement_locked(s, reversal_date, f"Cancelamento: {reason}")
    title.canceled_at = utcnow()
    title.cancel_reason = reason
    audit("cancelar", "titulo", title.id, f"“{title.description}” cancelado. Motivo: {reason}")


# ------------------------------------------------------------------ liquidações

def settle(title_id: int, *, amount: Decimal, settle_date: date, account_id: int, payment_method_id=None,
           notes: str | None = None, request_key: str | None = None) -> Settlement:
    """Registra pagamento/recebimento total ou parcial de um título."""
    _check_request_key(Settlement, request_key)
    title = locked_title(title_id)
    if title.is_canceled:
        raise ValidationError("Não é possível liquidar um lançamento cancelado.")
    amount = q(amount)
    if amount <= 0:
        raise ValidationError("Valor: deve ser maior que zero.")
    open_amount = title.open_amount
    if open_amount <= 0:
        raise ValidationError("Este lançamento já está totalmente liquidado.")
    if amount > open_amount:
        raise ValidationError(f"Valor maior do que o saldo em aberto ({brl(open_amount)}).")
    account = _active_account(account_id)
    _method(payment_method_id)
    s = Settlement(title_id=title.id, account_id=account.id, date=settle_date, amount=amount,
                   payment_method_id=payment_method_id or None, notes=(notes or "").strip() or None,
                   request_key=request_key)
    db.session.add(s)
    db.session.flush()
    verb = "Recebimento" if title.kind == "receber" else "Pagamento"
    partial = " parcial" if amount < open_amount else ""
    audit("liquidar", "liquidacao", s.id,
          f"{verb}{partial} de {brl(amount)} em “{title.description}” ({account.name}, {fmt_date(settle_date)})")
    _commit()
    return s


def _reverse_settlement_locked(original: Settlement, reversal_date: date | None, reason: str) -> Settlement:
    if original.is_reversal:
        raise ValidationError("Um estorno não pode ser estornado. Registre uma nova liquidação.")
    if original.reversal is not None:
        raise ValidationError("Este movimento já foi estornado.")
    from ..utils import today
    rev = Settlement(title_id=original.title_id, account_id=original.account_id,
                     payment_method_id=original.payment_method_id, date=reversal_date or today(),
                     amount=original.amount, is_reversal=True, reverses_id=original.id,
                     notes=reason or "Estorno")
    db.session.add(rev)
    db.session.flush()
    audit("estornar", "liquidacao", original.id,
          f"Estorno de {brl(original.amount)} em “{original.title.description}”. Motivo: {reason or '—'}")
    return rev


def reverse_settlement(settlement_id: int, reason: str, reversal_date: date | None = None) -> Settlement:
    original = db.session.get(Settlement, settlement_id)
    if original is None:
        raise ValidationError("Movimento não encontrado.")
    locked_title(original.title_id)  # bloqueia o título
    original = db.session.execute(select(Settlement).where(Settlement.id == settlement_id).with_for_update()).scalar_one()
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Informe o motivo do estorno.")
    rev = _reverse_settlement_locked(original, reversal_date, reason)
    _commit()
    return rev


# ------------------------------------------------------------------ vendas

@dataclass
class ItemInput:
    description: str
    quantity: Decimal
    unit_value: Decimal
    product_id: int | None = None


@dataclass
class SaleInput:
    description: str
    sale_date: date
    customer: str | None = None
    items: list[ItemInput] = field(default_factory=list)
    gross_amount: Decimal | None = None  # usado quando não há itens
    discount: Decimal = ZERO
    payment_method_id: int | None = None
    account_id: int | None = None
    received_now: bool = False
    installments: int = 1
    first_due_date: date | None = None
    notes: str | None = None
    request_key: str | None = None


def create_sale(data: SaleInput) -> Sale:
    desc = (data.description or "").strip()
    if not desc:
        raise ValidationError("Descrição: identifique a venda.")
    _check_request_key(Sale, data.request_key)
    items = []
    for it in data.items:
        if not it.description.strip() and not it.product_id:
            continue
        product = _get(Product, it.product_id, "Produto") if it.product_id else None
        qty = it.quantity
        price = q(it.unit_value)
        if price <= 0:
            raise ValidationError("Itens: o preço unitário deve ser maior que zero.")
        items.append(SaleItem(product_id=product.id if product else None,
                              description=(it.description.strip() or product.name)[:160],
                              quantity=qty, unit_price=price, total=q(qty * price)))
    gross = q(sum((i.total for i in items), ZERO)) if items else q(data.gross_amount or ZERO)
    if gross <= 0:
        raise ValidationError("Valor bruto: informe o valor da venda ou adicione itens.")
    discount = q(data.discount or ZERO)
    if discount < 0 or discount >= gross:
        raise ValidationError("Desconto: deve ser menor que o valor bruto.")
    net = q(gross - discount)
    if data.installments < 1 or data.installments > 24:
        raise ValidationError("Parcelas: informe entre 1 e 24.")
    if data.received_now and data.installments > 1:
        raise ValidationError("Venda recebida à vista não pode ser parcelada. Desmarque “recebida agora”.")
    _method(data.payment_method_id)
    if data.received_now:
        _active_account(data.account_id)
    elif data.account_id:
        _active_account(data.account_id)

    cat = system_category("venda", "receita")
    sale = Sale(description=desc[:160], sale_date=data.sale_date, customer=(data.customer or "").strip() or None,
                gross_amount=gross, discount=discount, net_amount=net, payment_method_id=data.payment_method_id or None,
                notes=(data.notes or "").strip() or None, request_key=data.request_key, items=items)
    db.session.add(sale)
    db.session.flush()

    first_due = data.sale_date if data.received_now else (data.first_due_date or data.sale_date)
    parts = split_installments(net, data.installments)
    titles = []
    for i, value in enumerate(parts):
        suffix = f" ({i + 1}/{data.installments})" if data.installments > 1 else ""
        t = Title(kind="receber", origin="venda", description=f"{desc[:150]}{suffix}", category_id=cat.id,
                  counterparty=sale.customer, competence_date=data.sale_date, due_date=add_months(first_due, i),
                  amount=value, expected_method_id=data.payment_method_id or None,
                  expected_account_id=data.account_id or None, sale_id=sale.id,
                  installment_no=i + 1, installment_count=data.installments)
        db.session.add(t)
        titles.append(t)
    db.session.flush()
    if data.received_now:
        db.session.add(Settlement(title_id=titles[0].id, account_id=data.account_id, date=data.sale_date,
                                  amount=net, payment_method_id=data.payment_method_id or None))
    audit("criar", "venda", sale.id, f"Venda “{desc}” de {brl(net)}"
          + (" recebida à vista" if data.received_now else f" em {data.installments}x a receber"))
    _commit()
    return sale


def update_sale_info(sale_id: int, *, description: str, customer: str | None, notes: str | None) -> Sale:
    sale = _get(Sale, sale_id, "Venda")
    desc = (description or "").strip()
    if not desc:
        raise ValidationError("Descrição: identifique a venda.")
    sale.description = desc[:160]
    sale.customer = (customer or "").strip() or None
    sale.notes = (notes or "").strip() or None
    for t in sale.titles:
        t.counterparty = sale.customer
    audit("editar", "venda", sale.id, f"Dados da venda “{desc}” atualizados")
    _commit()
    return sale


def cancel_sale(sale_id: int, reason: str, *, reverse_payments: bool = True, reversal_date: date | None = None) -> Sale:
    sale = db.session.execute(select(Sale).where(Sale.id == sale_id).with_for_update()).scalar_one_or_none()
    if sale is None:
        raise ValidationError("Venda não encontrada.")
    if sale.status == "cancelada":
        raise ValidationError("Esta venda já está cancelada.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Informe o motivo do cancelamento.")
    for t in sale.titles:
        locked = locked_title(t.id)
        if not locked.is_canceled:
            _cancel_title_locked(locked, reason, reverse_payments, reversal_date)
    sale.status = "cancelada"
    sale.canceled_at = utcnow()
    sale.cancel_reason = reason
    audit("cancelar", "venda", sale.id, f"Venda “{sale.description}” cancelada. Motivo: {reason}")
    _commit()
    return sale


# ------------------------------------------------------------------ compras de mercadorias

@dataclass
class PurchaseInput:
    purchase_date: date
    supplier: str | None
    items: list[ItemInput]
    installments: int = 1
    first_due_date: date | None = None
    paid_now: bool = False
    account_id: int | None = None
    payment_method_id: int | None = None
    notes: str | None = None
    update_product_cost: bool = True
    request_key: str | None = None


def create_purchase(data: PurchaseInput) -> Purchase:
    _check_request_key(Purchase, data.request_key)
    items = []
    for it in data.items:
        if not it.product_id and not it.description.strip():
            continue
        product = _get(Product, it.product_id, "Produto") if it.product_id else None
        cost = q(it.unit_value)
        if cost <= 0:
            raise ValidationError("Itens: o custo unitário deve ser maior que zero.")
        items.append(PurchaseItem(product_id=product.id if product else None,
                                  description=(it.description.strip() or product.name)[:160],
                                  quantity=it.quantity, unit_cost=cost, total=q(it.quantity * cost)))
        if product and data.update_product_cost:
            product.unit_cost = cost
            if data.supplier and not product.supplier:
                product.supplier = data.supplier.strip()
    if not items:
        raise ValidationError("Adicione ao menos um item à compra.")
    total = q(sum((i.total for i in items), ZERO))
    if data.installments < 1 or data.installments > 24:
        raise ValidationError("Parcelas: informe entre 1 e 24.")
    if data.paid_now and data.installments > 1:
        raise ValidationError("Compra paga à vista não pode ser parcelada.")
    if data.paid_now:
        _active_account(data.account_id)
    _method(data.payment_method_id)

    supplier = (data.supplier or "").strip() or None
    purchase = Purchase(purchase_date=data.purchase_date, supplier=supplier, total=total,
                        notes=(data.notes or "").strip() or None, request_key=data.request_key, items=items)
    db.session.add(purchase)
    db.session.flush()
    cat = system_category("mercadoria", "despesa")
    first_due = data.purchase_date if data.paid_now else (data.first_due_date or data.purchase_date)
    parts = split_installments(total, data.installments)
    label = f"Compra de mercadorias{(' — ' + supplier) if supplier else ''}"
    titles = []
    for i, value in enumerate(parts):
        suffix = f" ({i + 1}/{data.installments})" if data.installments > 1 else ""
        t = Title(kind="pagar", origin="compra", description=f"{label[:150]}{suffix}", category_id=cat.id,
                  counterparty=supplier, competence_date=data.purchase_date, due_date=add_months(first_due, i),
                  amount=value, expected_method_id=data.payment_method_id or None,
                  expected_account_id=data.account_id or None, purchase_id=purchase.id,
                  installment_no=i + 1, installment_count=data.installments)
        db.session.add(t)
        titles.append(t)
    db.session.flush()
    if data.paid_now:
        db.session.add(Settlement(title_id=titles[0].id, account_id=data.account_id, date=data.purchase_date,
                                  amount=total, payment_method_id=data.payment_method_id or None))
    audit("criar", "compra", purchase.id, f"{label} de {brl(total)}"
          + (" paga à vista" if data.paid_now else f" em {data.installments}x a pagar"))
    _commit()
    return purchase


def cancel_purchase(purchase_id: int, reason: str, *, reverse_payments: bool = True,
                    reversal_date: date | None = None) -> Purchase:
    purchase = db.session.execute(select(Purchase).where(Purchase.id == purchase_id).with_for_update()).scalar_one_or_none()
    if purchase is None:
        raise ValidationError("Compra não encontrada.")
    if purchase.status == "cancelada":
        raise ValidationError("Esta compra já está cancelada.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Informe o motivo do cancelamento.")
    for t in purchase.titles:
        locked = locked_title(t.id)
        if not locked.is_canceled:
            _cancel_title_locked(locked, reason, reverse_payments, reversal_date)
    purchase.status = "cancelada"
    purchase.canceled_at = utcnow()
    purchase.cancel_reason = reason
    audit("cancelar", "compra", purchase.id, f"Compra de {brl(purchase.total)} cancelada. Motivo: {reason}")
    _commit()
    return purchase


# ------------------------------------------------------------------ transferências

def create_transfer(*, from_account_id: int, to_account_id: int, transfer_date: date, amount: Decimal,
                    notes: str | None = None, request_key: str | None = None) -> Transfer:
    _check_request_key(Transfer, request_key)
    if not from_account_id or not to_account_id or int(from_account_id) == int(to_account_id):
        raise ValidationError("Escolha contas de origem e destino diferentes.")
    src = _active_account(from_account_id)
    dst = _active_account(to_account_id)
    amount = q(amount)
    if amount <= 0:
        raise ValidationError("Valor: deve ser maior que zero.")
    t = Transfer(from_account_id=src.id, to_account_id=dst.id, date=transfer_date, amount=amount,
                 notes=(notes or "").strip() or None, request_key=request_key)
    db.session.add(t)
    db.session.flush()
    audit("criar", "transferencia", t.id, f"Transferência de {brl(amount)}: {src.name} → {dst.name}")
    _commit()
    return t


def reverse_transfer(transfer_id: int, reason: str, reversal_date: date | None = None) -> Transfer:
    from ..utils import today
    original = db.session.execute(select(Transfer).where(Transfer.id == transfer_id).with_for_update()).scalar_one_or_none()
    if original is None:
        raise ValidationError("Transferência não encontrada.")
    if original.reverses_id:
        raise ValidationError("Um estorno de transferência não pode ser estornado.")
    if original.reversal is not None:
        raise ValidationError("Esta transferência já foi estornada.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Informe o motivo do estorno.")
    rev = Transfer(from_account_id=original.to_account_id, to_account_id=original.from_account_id,
                   date=reversal_date or today(), amount=original.amount, reverses_id=original.id,
                   notes=f"Estorno: {reason}")
    db.session.add(rev)
    db.session.flush()
    audit("estornar", "transferencia", original.id, f"Estorno da transferência de {brl(original.amount)}. Motivo: {reason}")
    _commit()
    return rev
