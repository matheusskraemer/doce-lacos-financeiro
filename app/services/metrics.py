"""Consultas e indicadores financeiros (somente leitura).

Regras (também documentadas em docs/REGRAS_FINANCEIRAS.md):

* Saldo atual = saldos iniciais das contas no escopo + entradas efetivadas
  − saídas efetivadas ± transferências, até a data de referência.
* Entradas/saídas efetivadas = liquidações (menos estornos) pela data efetiva.
* Resultado de caixa = recebido − pago no período. Não é lucro contábil.
* Vendas do período = valor líquido (bruto − desconto) das vendas não canceladas
  pela data da venda.
* Em aberto = valor do título − valor líquido já liquidado (cancelados = 0).
* Saldo projetado = saldo atual + a receber em aberto − a pagar em aberto
  com vencimento até o horizonte (inclui vencidos ainda não liquidados).
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, func, select

from ..extensions import db
from ..models import Account, Category, Purchase, Sale, Settlement, Title, Transfer
from ..utils import ZERO, add_months, month_end, month_label, month_start, q, today

SIGNED_SETTLEMENT = case((Settlement.is_reversal.is_(True), -Settlement.amount), else_=Settlement.amount)
CASH_EFFECT = case((Title.kind == "receber", SIGNED_SETTLEMENT), else_=-SIGNED_SETTLEMENT)


def _in_scope_ids() -> list[int]:
    return list(db.session.scalars(select(Account.id).where(Account.include_in_total.is_(True))))


# --------------------------------------------------------------------------- saldos
def account_balances(as_of: date | None = None) -> list[dict]:
    """Saldo por conta (todas as contas), conciliável lançamento a lançamento."""
    as_of = as_of or today()
    accounts = db.session.scalars(select(Account).order_by(Account.active.desc(), Account.name)).all()
    settled = dict(db.session.execute(
        select(Settlement.account_id, func.coalesce(func.sum(CASH_EFFECT), 0))
        .join(Title, Title.id == Settlement.title_id)
        .where(Settlement.date <= as_of)
        .group_by(Settlement.account_id)
    ).all())
    t_in = dict(db.session.execute(
        select(Transfer.to_account_id, func.sum(Transfer.amount)).where(Transfer.date <= as_of).group_by(Transfer.to_account_id)
    ).all())
    t_out = dict(db.session.execute(
        select(Transfer.from_account_id, func.sum(Transfer.amount)).where(Transfer.date <= as_of).group_by(Transfer.from_account_id)
    ).all())
    rows = []
    for a in accounts:
        mov = q(settled.get(a.id, 0))
        tr = q(t_in.get(a.id, 0)) - q(t_out.get(a.id, 0))
        rows.append({
            "account": a,
            "opening": q(a.opening_balance),
            "movements": mov,
            "transfers": q(tr),
            "balance": q(a.opening_balance + mov + tr),
        })
    return rows


def total_balance(as_of: date | None = None) -> Decimal:
    return q(sum((r["balance"] for r in account_balances(as_of) if r["account"].include_in_total), ZERO))


# --------------------------------------------------------------------------- fluxo realizado
def cash_totals(start: date, end: date) -> dict:
    """Entradas e saídas efetivadas no período (contas no escopo)."""
    scope = _in_scope_ids()
    if not scope:
        return {"in": ZERO, "out": ZERO, "result": ZERO}
    row = db.session.execute(
        select(
            func.coalesce(func.sum(case((Title.kind == "receber", SIGNED_SETTLEMENT), else_=0)), 0),
            func.coalesce(func.sum(case((Title.kind == "pagar", SIGNED_SETTLEMENT), else_=0)), 0),
        )
        .join(Title, Title.id == Settlement.title_id)
        .where(Settlement.date.between(start, end), Settlement.account_id.in_(scope))
    ).one()
    cin, cout = q(row[0]), q(row[1])
    return {"in": cin, "out": cout, "result": q(cin - cout)}


def paid_by_group(start: date, end: date) -> dict:
    """Pagamentos efetivados separados em operacionais x mercadorias."""
    scope = _in_scope_ids()
    res = {"operacional": ZERO, "mercadoria": ZERO}
    if not scope:
        return res
    for grp, total in db.session.execute(
        select(Category.group, func.coalesce(func.sum(SIGNED_SETTLEMENT), 0))
        .join(Title, Title.id == Settlement.title_id)
        .join(Category, Category.id == Title.category_id)
        .where(Title.kind == "pagar", Settlement.date.between(start, end), Settlement.account_id.in_(scope))
        .group_by(Category.group)
    ).all():
        res[grp] = q(total)
    return res


def paid_by_category(start: date, end: date, basis: str = "caixa") -> list[dict]:
    """Despesas por categoria. basis='caixa' (pagas) ou 'competencia' (lançadas)."""
    if basis == "competencia":
        rows = db.session.execute(
            select(Category.id, Category.name, Category.group, func.sum(Title.amount))
            .join(Title, Title.category_id == Category.id)
            .where(Title.kind == "pagar", Title.canceled_at.is_(None), Title.competence_date.between(start, end))
            .group_by(Category.id, Category.name, Category.group)
        ).all()
    else:
        scope = _in_scope_ids() or [-1]
        rows = db.session.execute(
            select(Category.id, Category.name, Category.group, func.sum(SIGNED_SETTLEMENT))
            .join(Title, Title.category_id == Category.id)
            .join(Settlement, Settlement.title_id == Title.id)
            .where(Title.kind == "pagar", Settlement.date.between(start, end), Settlement.account_id.in_(scope))
            .group_by(Category.id, Category.name, Category.group)
        ).all()
    out = [{"id": r[0], "name": r[1], "group": r[2], "total": q(r[3])} for r in rows if q(r[3]) != 0]
    out.sort(key=lambda r: r["total"], reverse=True)
    return out


# --------------------------------------------------------------------------- vendas e compras
def sales_totals(start: date, end: date) -> dict:
    row = db.session.execute(
        select(func.count(Sale.id), func.coalesce(func.sum(Sale.gross_amount), 0),
               func.coalesce(func.sum(Sale.discount), 0), func.coalesce(func.sum(Sale.net_amount), 0))
        .where(Sale.status == "ativa", Sale.sale_date.between(start, end))
    ).one()
    canceled = db.session.scalar(
        select(func.coalesce(func.sum(Sale.net_amount), 0)).where(Sale.status == "cancelada", Sale.sale_date.between(start, end))
    )
    return {"count": row[0], "gross": q(row[1]), "discount": q(row[2]), "net": q(row[3]), "canceled": q(canceled)}


def other_revenue_competence(start: date, end: date) -> Decimal:
    return q(db.session.scalar(
        select(func.coalesce(func.sum(Title.amount), 0))
        .where(Title.kind == "receber", Title.origin == "receita", Title.canceled_at.is_(None),
               Title.competence_date.between(start, end))
    ))


def expenses_competence(start: date, end: date) -> dict:
    res = {"operacional": ZERO, "mercadoria": ZERO}
    for grp, total in db.session.execute(
        select(Category.group, func.coalesce(func.sum(Title.amount), 0))
        .join(Category, Category.id == Title.category_id)
        .where(Title.kind == "pagar", Title.canceled_at.is_(None), Title.competence_date.between(start, end))
        .group_by(Category.group)
    ).all():
        res[grp] = q(total)
    return res


def purchases_total(start: date, end: date) -> dict:
    row = db.session.execute(
        select(func.count(Purchase.id), func.coalesce(func.sum(Purchase.total), 0))
        .where(Purchase.status == "ativa", Purchase.purchase_date.between(start, end))
    ).one()
    return {"count": row[0], "total": q(row[1])}


# --------------------------------------------------------------------------- em aberto
def open_titles_query(kind: str):
    open_expr = Title.amount - Title.settled_amount
    return select(Title).where(Title.kind == kind, Title.canceled_at.is_(None), open_expr > 0)


def open_total(kind: str, due_until: date | None = None, due_from: date | None = None) -> Decimal:
    stmt = open_titles_query(kind)
    if due_until:
        stmt = stmt.where(Title.due_date <= due_until)
    if due_from:
        stmt = stmt.where(Title.due_date >= due_from)
    return q(sum((t.open_amount for t in db.session.scalars(stmt)), ZERO))


def open_summary(kind: str, ref: date | None = None, upcoming_days: int = 7) -> dict:
    ref = ref or today()
    titles = db.session.scalars(open_titles_query(kind).order_by(Title.due_date)).all()
    overdue = [t for t in titles if t.due_date < ref]
    upcoming = [t for t in titles if ref <= t.due_date <= ref + timedelta(days=upcoming_days)]
    return {
        "total": q(sum((t.open_amount for t in titles), ZERO)),
        "count": len(titles),
        "overdue_total": q(sum((t.open_amount for t in overdue), ZERO)),
        "overdue_count": len(overdue),
        "upcoming_total": q(sum((t.open_amount for t in upcoming), ZERO)),
        "upcoming": upcoming,
        "overdue": overdue,
    }


def projection(horizon_days: int = 30, ref: date | None = None) -> dict:
    ref = ref or today()
    until = ref + timedelta(days=horizon_days)
    bal = total_balance(ref)
    rec = open_total("receber", due_until=until)
    pay = open_total("pagar", due_until=until)
    return {"balance": bal, "receivable": rec, "payable": pay, "projected": q(bal + rec - pay), "until": until,
            "days": horizon_days}


# --------------------------------------------------------------------------- séries para gráficos
def _bucket_key(d: date, gran: str) -> date:
    if gran == "mes":
        return month_start(d)
    if gran == "semana":
        return d - timedelta(days=d.weekday())
    return d


def bucket_starts(start: date, end: date, gran: str) -> list[date]:
    out = []
    cur = _bucket_key(start, gran)
    while cur <= end:
        out.append(cur)
        cur = add_months(cur, 1) if gran == "mes" else cur + timedelta(days=7 if gran == "semana" else 1)
    return out


def bucket_label(d: date, gran: str) -> str:
    if gran == "mes":
        return month_label(d, short=True)
    if gran == "semana":
        return f"sem. {d.strftime('%d/%m')}"
    return d.strftime("%d/%m")


def bucket_end(d: date, gran: str) -> date:
    if gran == "mes":
        return month_end(d)
    if gran == "semana":
        return d + timedelta(days=6)
    return d


def auto_granularity(start: date, end: date) -> str:
    days = (end - start).days + 1
    if days <= 45:
        return "dia"
    if days <= 120:
        return "semana"
    return "mes"


def _settlement_rows(start: date, end: date):
    scope = _in_scope_ids() or [-1]
    return db.session.execute(
        select(Settlement.date, Title.kind, SIGNED_SETTLEMENT)
        .join(Title, Title.id == Settlement.title_id)
        .where(Settlement.date.between(start, end), Settlement.account_id.in_(scope))
    ).all()


def series_cash(start: date, end: date, gran: str | None = None) -> dict:
    """Entradas, saídas e saldo ao fim de cada intervalo (realizado)."""
    gran = gran or auto_granularity(start, end)
    keys = bucket_starts(start, end, gran)
    ins, outs = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for d, kind, val in _settlement_rows(start, end):
        k = _bucket_key(d, gran)
        if kind == "receber":
            ins[k] += q(val)
        else:
            outs[k] += q(val)
    ref = today()
    balances = []
    for k in keys:
        e = min(bucket_end(k, gran), end)
        balances.append(float(total_balance(e)) if k <= ref else None)
    return {
        "labels": [bucket_label(k, gran) for k in keys],
        "in": [float(ins[k]) for k in keys],
        "out": [float(outs[k]) for k in keys],
        "balance": balances,
        "granularity": gran,
    }


def series_sales(start: date, end: date, gran: str | None = None) -> dict:
    gran = gran or auto_granularity(start, end)
    keys = bucket_starts(start, end, gran)
    vals = defaultdict(lambda: ZERO)
    for d, net in db.session.execute(
        select(Sale.sale_date, Sale.net_amount).where(Sale.status == "ativa", Sale.sale_date.between(start, end))
    ).all():
        vals[_bucket_key(d, gran)] += q(net)
    return {"labels": [bucket_label(k, gran) for k in keys], "values": [float(vals[k]) for k in keys], "granularity": gran}


@dataclass
class MonthSummary:
    month: date
    sales: Decimal
    received: Decimal
    paid: Decimal
    paid_operational: Decimal
    paid_goods: Decimal
    purchases: Decimal
    result: Decimal
    closing_balance: Decimal | None

    @property
    def label(self) -> str:
        return month_label(self.month)


def month_summary(m: date) -> MonthSummary:
    s, e = month_start(m), month_end(m)
    cash = cash_totals(s, e)
    groups = paid_by_group(s, e)
    ref = today()
    return MonthSummary(
        month=s,
        sales=sales_totals(s, e)["net"],
        received=cash["in"],
        paid=cash["out"],
        paid_operational=groups.get("operacional", ZERO),
        paid_goods=groups.get("mercadoria", ZERO),
        purchases=purchases_total(s, e)["total"],
        result=cash["result"],
        closing_balance=total_balance(min(e, ref)) if s <= ref else None,
    )


def last_months(n: int = 12, ref: date | None = None) -> list[MonthSummary]:
    ref = ref or today()
    first = add_months(month_start(ref), -(n - 1))
    return [month_summary(add_months(first, i)) for i in range(n)]


# --------------------------------------------------------------------------- fluxo de caixa (realizado + projetado)
def cash_flow(start: date, end: date, gran: str = "dia", ref: date | None = None) -> dict:
    """Tabela de fluxo de caixa por intervalo.

    * Intervalos até hoje mostram o realizado (liquidações pela data efetiva).
    * Do dia de hoje em diante, soma-se o previsto: saldo em aberto dos títulos
      pelo vencimento. Títulos vencidos e não liquidados entram como previstos
      no intervalo que contém hoje (ainda são esperados).
    * Nada é contado duas vezes: o previsto usa só o saldo em aberto.
    """
    ref = ref or today()
    keys = bucket_starts(start, end, gran)
    realized_in, realized_out = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for d, kind, val in _settlement_rows(start, min(end, ref)):
        k = _bucket_key(d, gran)
        (realized_in if kind == "receber" else realized_out)[k] += q(val)

    proj_in, proj_out = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    overdue_in = overdue_out = ZERO
    if end >= ref:
        first_future_key = _bucket_key(max(start, ref), gran)
        for kind, target in (("receber", proj_in), ("pagar", proj_out)):
            for t in db.session.scalars(open_titles_query(kind).where(Title.due_date <= end)):
                if t.due_date < max(start, ref):
                    target[first_future_key] += t.open_amount
                    if kind == "receber":
                        overdue_in += t.open_amount
                    else:
                        overdue_out += t.open_amount
                else:
                    target[_bucket_key(t.due_date, gran)] += t.open_amount

    opening = total_balance(start - timedelta(days=1)) if start <= ref else None
    balance_today = total_balance(ref)
    rows = []
    projected_running = None
    for k in keys:
        b_end = min(bucket_end(k, gran), end)
        is_past = b_end < ref
        has_today = k <= ref <= b_end
        is_future = k > ref
        realized_balance = total_balance(b_end) if not is_future else None
        if has_today or (is_future and projected_running is None):
            base = balance_today if has_today else balance_today
            projected_running = q(base + proj_in[k] - proj_out[k])
        elif is_future:
            projected_running = q(projected_running + proj_in[k] - proj_out[k])
        rows.append({
            "start": k,
            "end": b_end,
            "label": bucket_label(k, gran),
            "realized_in": realized_in[k],
            "realized_out": realized_out[k],
            "realized_balance": realized_balance if not is_future else None,
            "projected_in": proj_in[k],
            "projected_out": proj_out[k],
            "projected_balance": projected_running if (has_today or is_future) else None,
            "is_past": is_past,
            "has_today": has_today,
            "is_future": is_future,
        })
    tot_rin = q(sum((r["realized_in"] for r in rows), ZERO))
    tot_rout = q(sum((r["realized_out"] for r in rows), ZERO))
    return {
        "rows": rows,
        "opening": opening,
        "realized_in": tot_rin,
        "realized_out": tot_rout,
        "realized_closing": total_balance(min(end, ref)) if start <= ref else None,
        "projected_in": q(sum(proj_in.values(), ZERO)),
        "projected_out": q(sum(proj_out.values(), ZERO)),
        "projected_closing": rows[-1]["projected_balance"] if rows and end >= ref else None,
        "overdue_in": q(overdue_in),
        "overdue_out": q(overdue_out),
        "balance_today": balance_today,
        "granularity": gran,
    }


# --------------------------------------------------------------------------- conferência
def integrity_report() -> list[str]:
    """Verificações de consistência. Lista vazia = tudo certo."""
    problems = []
    for t in db.session.scalars(select(Title)):
        if q(t.settled_amount) > q(t.amount):
            problems.append(f"Título #{t.id} liquidado acima do valor ({t.settled_amount} > {t.amount}).")
        if q(t.settled_amount) < 0:
            problems.append(f"Título #{t.id} com liquidação líquida negativa.")
        if t.is_canceled and q(t.settled_amount) != 0:
            problems.append(f"Título #{t.id} cancelado com valor liquidado.")
    for s in db.session.scalars(select(Settlement).where(Settlement.is_reversal.is_(True))):
        orig = s.reverses
        if orig is None or orig.is_reversal or orig.amount != s.amount or orig.title_id != s.title_id:
            problems.append(f"Estorno #{s.id} inconsistente com o lançamento original.")
    for sale in db.session.scalars(select(Sale)):
        total = q(sum((t.amount for t in sale.titles), ZERO))
        if total != q(sale.net_amount):
            problems.append(f"Venda #{sale.id}: parcelas ({total}) diferem do valor líquido ({sale.net_amount}).")
    for p in db.session.scalars(select(Purchase)):
        total = q(sum((t.amount for t in p.titles), ZERO))
        if total != q(p.total):
            problems.append(f"Compra #{p.id}: parcelas ({total}) diferem do total ({p.total}).")
    # conciliação: saldo por conta = inicial + soma de cada lançamento
    for r in account_balances(date(2100, 12, 31)):
        a = r["account"]
        manual = q(a.opening_balance)
        for s in db.session.scalars(select(Settlement).where(Settlement.account_id == a.id)):
            manual += s.cash_effect
        for t in db.session.scalars(select(Transfer).where(Transfer.to_account_id == a.id)):
            manual += t.amount
        for t in db.session.scalars(select(Transfer).where(Transfer.from_account_id == a.id)):
            manual -= t.amount
        if q(manual) != r["balance"]:
            problems.append(f"Conta {a.name}: saldo calculado {r['balance']} difere da soma dos lançamentos {manual}.")
    return problems
