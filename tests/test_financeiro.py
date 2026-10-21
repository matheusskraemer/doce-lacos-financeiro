"""Regras financeiras — cenários de aceite 1 a 11 e 16."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Category, Sale, Settlement, Title
from app.services import finance
from app.services import metrics as m
from app.utils import ValidationError, month_end, month_start, split_installments, today

T = today()


def cat(name, kind="despesa"):
    return db.session.scalar(select(Category.id).where(Category.name == name, Category.kind == kind))


def balance():
    return m.total_balance(T)


def sale(**kw):
    base = dict(description="Kit laços", sale_date=T, gross_amount=D("100.00"), discount=D("0"))
    base.update(kw)
    return finance.create_sale(finance.SaleInput(**base))


def expense(amount="50.00", **kw):
    base = dict(kind="pagar", origin="despesa", description="Embalagens", category_id=cat("Embalagens"),
                amount=D(amount), competence_date=T, due_date=T)
    base.update(kw)
    return finance.create_titles(finance.TitleInput(**base))


# 1 ---------------------------------------------------------------------------
def test_venda_recebida_a_vista(accounts):
    banco, _ = accounts
    s = sale(gross_amount=D("150.00"), discount=D("10.00"), received_now=True, account_id=banco)
    assert s.net_amount == D("140.00")
    assert balance() == D("1340.00")  # 1000 + 200 + 140
    assert s.receipt_status == "recebida"
    assert m.sales_totals(T, T)["net"] == D("140.00")
    assert m.cash_totals(T, T)["in"] == D("140.00")
    assert m.open_total("receber") == 0


# 2 ---------------------------------------------------------------------------
def test_venda_com_recebimento_futuro(accounts):
    s = sale(gross_amount=D("300.00"), installments=3, first_due_date=T + timedelta(days=10))
    assert balance() == D("1200.00")  # caixa não muda
    assert m.sales_totals(T, T)["net"] == D("300.00")  # venda conta na data da venda
    assert m.cash_totals(T, T)["in"] == 0  # nada recebido
    assert [t.amount for t in s.titles] == [D("100.00")] * 3
    assert m.open_total("receber") == D("300.00")
    proj = m.projection(30)
    assert proj["receivable"] == D("100.00")  # só a 1ª parcela vence em 30 dias
    assert proj["projected"] == D("1300.00")


# 3 ---------------------------------------------------------------------------
def test_recebimento_parcial(accounts):
    banco, _ = accounts
    s = sale(gross_amount=D("200.00"))
    t = s.titles[0]
    finance.settle(t.id, amount=D("80.00"), settle_date=T, account_id=banco)
    db.session.refresh(t)
    assert t.settled_amount == D("80.00")
    assert t.open_amount == D("120.00")
    assert t.status_on(T) == "parcial"
    assert balance() == D("1280.00")
    with pytest.raises(ValidationError):
        finance.settle(t.id, amount=D("120.01"), settle_date=T, account_id=banco)
    finance.settle(t.id, amount=D("120.00"), settle_date=T, account_id=banco)
    db.session.refresh(t)
    assert t.status_on(T) == "liquidado"
    with pytest.raises(ValidationError):
        finance.settle(t.id, amount=D("1.00"), settle_date=T, account_id=banco)
    assert balance() == D("1400.00")


# 4 ---------------------------------------------------------------------------
def test_despesa_paga(accounts):
    banco, _ = accounts
    (t,) = expense("45.90", settle_now=True, settle_account_id=banco)
    assert balance() == D("1154.10")
    assert m.paid_by_group(T, T)["operacional"] == D("45.90")
    assert t.status_on(T) == "liquidado"


# 5 ---------------------------------------------------------------------------
def test_despesa_pendente(accounts):
    (t,) = expense("89.00", due_date=T + timedelta(days=5))
    assert balance() == D("1200.00")
    assert m.open_total("pagar") == D("89.00")
    assert t.status_on(T) == "pendente"
    assert t.status_on(T + timedelta(days=6)) == "vencido"
    assert m.projection(30)["projected"] == D("1111.00")


# 6 ---------------------------------------------------------------------------
def test_pagamento_parcial(accounts):
    banco, caixa = accounts
    (t,) = expense("500.00", due_date=T)
    finance.settle(t.id, amount=D("200.00"), settle_date=T, account_id=banco)
    finance.settle(t.id, amount=D("100.00"), settle_date=T, account_id=caixa)
    db.session.refresh(t)
    assert t.open_amount == D("200.00")
    assert m.open_total("pagar") == D("200.00")
    assert balance() == D("900.00")
    rows = {r["account"].name: r["balance"] for r in m.account_balances(T)}
    assert rows == {"Banco": D("800.00"), "Caixa": D("100.00")}
    # projeção conta só o que falta pagar
    assert m.projection(30)["projected"] == D("700.00")


# 7 ---------------------------------------------------------------------------
def test_compra_de_mercadorias_com_pagamento_futuro(accounts):
    banco, _ = accounts
    p = finance.create_purchase(finance.PurchaseInput(
        purchase_date=T, supplier="Armarinho", installments=2, first_due_date=T + timedelta(days=15),
        items=[finance.ItemInput("Fita cetim", D("10"), D("3.50")), finance.ItemInput("Presilhas", D("100"), D("0.40"))],
    ))
    assert p.total == D("75.00")
    assert balance() == D("1200.00")  # nada saiu do caixa
    assert m.purchases_total(T, T)["total"] == D("75.00")
    assert m.open_total("pagar") == D("75.00")
    assert m.paid_by_group(T, T + timedelta(days=60))["mercadoria"] == 0
    for t in p.titles:
        finance.settle(t.id, amount=t.open_amount, settle_date=T + timedelta(days=1), account_id=banco)
    assert m.total_balance(T + timedelta(days=1)) == D("1125.00")  # saiu uma única vez
    assert m.paid_by_group(T, T + timedelta(days=1))["mercadoria"] == D("75.00")
    assert m.purchases_total(T, T)["total"] == D("75.00")  # a quitação não soma a compra de novo
    db.session.refresh(p)
    assert p.payment_status == "paga"


# 8 ---------------------------------------------------------------------------
def test_estorno_e_cancelamento(accounts):
    banco, _ = accounts
    s = sale(gross_amount=D("90.00"), received_now=True, account_id=banco)
    st = db.session.scalar(select(Settlement).where(Settlement.title_id == s.titles[0].id))
    finance.reverse_settlement(st.id, "Pix devolvido")
    assert balance() == D("1200.00")
    db.session.refresh(s.titles[0])
    assert s.titles[0].open_amount == D("90.00")
    with pytest.raises(ValidationError):
        finance.reverse_settlement(st.id, "de novo")  # não estorna duas vezes
    rev = db.session.scalar(select(Settlement).where(Settlement.is_reversal.is_(True)))
    with pytest.raises(ValidationError):
        finance.reverse_settlement(rev.id, "estorno do estorno")
    # histórico preservado: original e estorno continuam registrados
    assert db.session.query(Settlement).count() == 2

    s2 = sale(description="Pedido desistido", gross_amount=D("60.00"), received_now=True, account_id=banco)
    assert balance() == D("1260.00")
    finance.cancel_sale(s2.id, "Cliente desistiu")
    db.session.refresh(s2)
    assert s2.status == "cancelada"
    assert balance() == D("1200.00")
    assert m.sales_totals(T, T)["net"] == D("90.00")  # cancelada sai do total de vendas
    assert m.sales_totals(T, T)["canceled"] == D("60.00")
    assert m.open_total("receber") == D("90.00")  # só a primeira venda, cujo recebimento foi estornado
    assert m.integrity_report() == []


def test_cancelar_titulo_exige_estorno(accounts):
    banco, _ = accounts
    (t,) = expense("30.00", settle_now=True, settle_account_id=banco)
    with pytest.raises(ValidationError):
        finance.cancel_title(t.id, "erro de digitação")
    finance.cancel_title(t.id, "erro de digitação", reverse_payments=True)
    assert balance() == D("1200.00")
    db.session.refresh(t)
    assert t.status_on(T) == "cancelado" and t.open_amount == 0


# 9 ---------------------------------------------------------------------------
def test_transferencia_entre_contas(accounts):
    banco, caixa = accounts
    before_result = m.cash_totals(T, T)
    finance.create_transfer(from_account_id=caixa, to_account_id=banco, transfer_date=T, amount=D("150.00"))
    rows = {r["account"].name: r["balance"] for r in m.account_balances(T)}
    assert rows == {"Banco": D("1150.00"), "Caixa": D("50.00")}
    assert balance() == D("1200.00")  # consolidado não muda
    assert m.cash_totals(T, T) == before_result  # resultado não muda
    with pytest.raises(ValidationError):
        finance.create_transfer(from_account_id=banco, to_account_id=banco, transfer_date=T, amount=D("1"))


# 10 --------------------------------------------------------------------------
def test_fluxo_realizado_e_projetado(accounts):
    banco, _ = accounts
    sale(gross_amount=D("100.00"), received_now=True, account_id=banco, sale_date=T - timedelta(days=2))
    sale(gross_amount=D("250.00"), first_due_date=T + timedelta(days=3))
    expense("80.00", due_date=T + timedelta(days=5))
    late, = expense("20.00", due_date=T - timedelta(days=1))  # vencida, ainda não paga
    flow = m.cash_flow(T - timedelta(days=3), T + timedelta(days=7), "dia", T)
    assert flow["opening"] == D("1200.00")
    assert flow["realized_in"] == D("100.00")
    assert flow["balance_today"] == D("1300.00")
    assert flow["projected_in"] == D("250.00")
    assert flow["projected_out"] == D("100.00")  # 80 futura + 20 vencida
    assert flow["overdue_out"] == D("20.00")
    assert flow["projected_closing"] == D("1450.00")
    today_row = next(r for r in flow["rows"] if r["has_today"])
    assert today_row["projected_balance"] == D("1280.00")  # vencida entra hoje
    # pagando a parcial: não contar duas vezes
    finance.settle(late.id, amount=D("5.00"), settle_date=T, account_id=banco)
    flow2 = m.cash_flow(T - timedelta(days=3), T + timedelta(days=7), "dia", T)
    assert flow2["projected_out"] == D("95.00")
    assert flow2["projected_closing"] == D("1450.00")


# 11 --------------------------------------------------------------------------
def test_comparacao_de_periodos(accounts):
    banco, _ = accounts
    prev = month_start(T) - timedelta(days=1)
    sale(gross_amount=D("100.00"), received_now=True, account_id=banco, sale_date=prev)
    sale(gross_amount=D("250.00"), received_now=True, account_id=banco, sale_date=T)
    a, b = m.month_summary(prev), m.month_summary(T)
    assert a.sales == D("100.00") and b.sales == D("250.00")
    assert a.received == D("100.00") and b.received == D("250.00")
    assert m.sales_totals(month_start(prev), month_end(prev))["net"] == D("100.00")
    from app.services.reports import Filters, build
    rep = build("comparativo", Filters(start=T, end=T, month_a=prev, month_b=T))
    row = next(r for r in rep.rows if r["metric"].startswith("Vendas"))
    assert row["diff"] == D("150.00")


# 16 --------------------------------------------------------------------------
def test_sem_duplicidade(accounts):
    banco, _ = accounts
    sale(gross_amount=D("50.00"), received_now=True, account_id=banco, request_key="form-123")
    with pytest.raises(finance.DuplicateSubmission):
        sale(gross_amount=D("50.00"), received_now=True, account_id=banco, request_key="form-123")
    assert db.session.query(Sale).count() == 1
    assert balance() == D("1250.00")

    (t,) = expense("40.00")
    finance.settle(t.id, amount=D("40.00"), settle_date=T, account_id=banco, request_key="pay-1")
    with pytest.raises(ValidationError):
        finance.settle(t.id, amount=D("40.00"), settle_date=T, account_id=banco, request_key="pay-1")
    assert balance() == D("1210.00")
    assert m.integrity_report() == []


def test_parcelas_somam_exatamente():
    for total, n in [(D("100.00"), 3), (D("0.05"), 3), (D("999.99"), 7), (D("10.00"), 1)]:
        parts = split_installments(total, n)
        assert sum(parts) == total and len(parts) == n and all(p >= 0 for p in parts)
    assert split_installments(D("100.00"), 3) == [D("33.34"), D("33.33"), D("33.33")]


def test_competencia_versus_caixa(accounts):
    banco, _ = accounts
    (t,) = expense("120.00", competence_date=T - timedelta(days=40), due_date=T)
    finance.settle(t.id, amount=D("120.00"), settle_date=T, account_id=banco)
    comp = m.expenses_competence(T - timedelta(days=45), T - timedelta(days=35))
    assert comp["operacional"] == D("120.00")
    assert m.cash_totals(T - timedelta(days=45), T - timedelta(days=35))["out"] == 0
    assert m.cash_totals(T, T)["out"] == D("120.00")


def test_conta_fora_do_escopo(accounts):
    from app.models import Account
    pessoal = Account(name="Pessoal", kind="banco", opening_balance=5000, opening_date=T, include_in_total=False)
    db.session.add(pessoal)
    db.session.commit()
    assert balance() == D("1200.00")


def test_recorrencia(accounts):
    titles = expense("99.90", recurrence_months=3)
    assert [t.due_date.month for t in titles] == sorted({t.due_date.month for t in titles}, key=lambda x: [t.due_date.month for t in titles].index(x))
    assert len(titles) == 3 and all(t.amount == D("99.90") for t in titles)
    assert len({t.recurrence_group for t in titles}) == 1
