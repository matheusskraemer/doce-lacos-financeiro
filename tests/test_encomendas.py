"""Encomendas: cadastro, valores, fluxo, prazos e integração com o financeiro."""
from __future__ import annotations

import io
from datetime import datetime, time, timedelta
from decimal import Decimal as D

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.extensions import db
from app.models import Order, OrderEvent, Sale, Settlement, Title
from app.services import finance
from app.services import metrics as m
from app.services import orders as svc
from app.utils import ValidationError, today

T = today()


def item(desc="Laço cetim", qty="2", price="18.00", disc="0", **kw):
    return svc.OrderItemInput(description=desc, quantity=D(qty), unit_price=D(price), discount=D(disc), **kw)


def make(items=None, **kw):
    base = dict(customer_name="Ana Souza", due_date=T + timedelta(days=5),
                items=items or [item(), item("Tiara forrada", "1", "29.90", "4.90")])
    base.update(kw)
    return svc.create_order(svc.OrderInput(**base))


def balance():
    return m.total_balance(T)


# ------------------------------------------------------------------ cadastro e valores
def test_encomenda_com_varios_itens_e_descontos(accounts):
    o = make(discount=D("2.00"))
    assert o.code == "DL-0001"
    assert o.gross == D("65.90")             # 2×18 + 29,90
    assert o.total_discount == D("6.90")     # 4,90 do item + 2,00 do pedido
    assert o.total == D("59.00")
    assert [i.subtotal for i in o.items] == [D("36.00"), D("25.00")]
    assert o.status == "novo" and o.sale is None
    assert balance() == D("1200.00")         # cadastrar não mexe no caixa
    assert make().code == "DL-0002"
    assert o.events[0].kind == "criado"


def test_validacoes_de_cadastro(accounts):
    with pytest.raises(ValidationError):
        make(items=[item(disc="99")])        # desconto maior que o item
    with pytest.raises(ValidationError):
        make(delivery_mode="entrega")        # entrega exige endereço
    make(delivery_mode="retirada")           # retirada não exige endereço
    with pytest.raises(ValidationError):
        make(customer_name=" ")
    with pytest.raises(finance.DuplicateSubmission):
        make(request_key="k1"), make(request_key="k1")
    assert db.session.query(Order).count() == 2


# ------------------------------------------------------------------ fluxo
def test_fluxo_de_status_validado_e_sem_caixa(accounts):
    o = make()
    with pytest.raises(ValidationError):
        svc.change_status(o.id, "entregue")  # não pula etapas
    svc.change_status(o.id, "confirmado")
    db.session.refresh(o)
    assert o.sale is not None and o.sale.net_amount == o.total  # venda vinculada, a receber
    assert m.open_total("receber") == o.total
    assert balance() == D("1200.00")     # confirmar não registra entrada de caixa
    for st in ("producao", "pronto"):
        svc.change_status(o.id, st)
    with pytest.raises(ValidationError):
        svc.change_status(o.id, "saiu")      # retirada não passa por "saiu para entrega"
    svc.change_status(o.id, "entregue")
    db.session.refresh(o)
    assert o.status == "entregue" and o.delivered_at is not None
    assert o.pending == o.total and o.payment_status == "a_receber"  # entregue ≠ pago
    assert balance() == D("1200.00")
    with pytest.raises(ValidationError):
        svc.change_status(o.id, "producao")  # etapa final
    kinds = [e.kind for e in o.events]
    assert kinds.count("status") == 4 and "venda" in kinds
    assert all(e.username for e in o.events)
    assert db.session.query(Sale).count() == 1  # nenhuma venda duplicada ao avançar etapas


def test_cancelar_exige_motivo_pelo_fluxo_proprio(accounts):
    o = make()
    with pytest.raises(ValidationError):
        svc.change_status(o.id, "cancelado")
    with pytest.raises(ValidationError):
        svc.cancel_order(o.id, " ", refund=False)
    svc.cancel_order(o.id, "Cliente desistiu", refund=False)
    db.session.refresh(o)
    assert o.status == "cancelado" and o.pending == 0


# ------------------------------------------------------------------ prazos
def test_identificacao_de_atrasos(accounts):
    late = make(due_date=T - timedelta(days=1))
    today_morning = make(due_date=T, due_time=time(0, 1))
    tonight = make(due_date=T)  # sem horário: vale até o fim do dia
    now = svc.now_local()
    assert late.is_late(now)
    assert today_morning.is_late(now) == (now.time() > time(0, 1))
    assert not tonight.is_late(now)
    svc.change_status(late.id, "confirmado")
    db.session.refresh(late)
    assert late.is_late(now)             # mudar etapa não "resolve" atraso
    kinds = {(a["order"].id, a["kind"]) for a in svc.alerts(2)}
    assert (late.id, "atrasada") in kinds and (tonight.id, "proxima") in kinds and (tonight.id, "confirmacao") in kinds
    assert svc.metrics(T, T)["late"] >= 1
    assert late.id in [o.id for o in svc.search_orders(prazo="atrasadas")]
    assert tonight.id not in [o.id for o in svc.search_orders(prazo="atrasadas")]


# ------------------------------------------------------------------ dinheiro
def test_sinal_antecipado_e_pagamento_parcial(accounts):
    banco, caixa = accounts
    o = make(deposit_amount=D("20.00"), deposit_account_id=banco)   # sinal no cadastro, ainda "novo"
    db.session.refresh(o)
    assert o.status == "novo" and o.sale is not None
    assert o.total == D("61.00")
    assert o.received == D("20.00") and o.pending == D("41.00") and o.payment_status == "parcial"
    assert balance() == D("1220.00")
    with pytest.raises(ValidationError):
        svc.register_payment(o.id, amount=D("41.01"), pay_date=T, account_id=caixa)
    svc.register_payment(o.id, amount=D("41.00"), pay_date=T, account_id=caixa, request_key="p1")
    with pytest.raises(finance.DuplicateSubmission):
        svc.register_payment(o.id, amount=D("1.00"), pay_date=T, account_id=caixa, request_key="p1")
    db.session.refresh(o)
    assert o.pending == 0 and o.payment_status == "pago"
    assert balance() == D("1261.00")
    assert m.cash_totals(T, T)["in"] == D("61.00")
    assert m.sales_totals(T, T)["net"] == D("61.00")      # a venda aparece uma vez só
    svc.change_status(o.id, "confirmado")                   # confirmar depois do sinal não cria outra venda
    assert db.session.query(Sale).count() == 1
    assert db.session.query(Settlement).count() == 2
    assert [e.kind for e in o.events].count("pagamento") == 2
    assert m.integrity_report() == []


def test_cancelamento_com_valores_recebidos(accounts):
    banco, _ = accounts
    o = make(initial_status="confirmado", deposit_amount=D("30.00"), deposit_account_id=banco)
    with pytest.raises(ValidationError):
        svc.cancel_order(o.id, "Desistiu", refund=False)    # exige tratar o reembolso
    svc.cancel_order(o.id, "Desistiu", refund=True)
    db.session.refresh(o)
    assert o.status == "cancelado" and o.canceled_at and o.cancel_reason == "Desistiu"
    assert balance() == D("1200.00")                         # reembolso registrado (estorno)
    rows = db.session.scalars(select(Settlement)).all()
    assert len(rows) == 2 and sum(1 for s in rows if s.is_reversal) == 1  # nada apagado
    assert o.sale.status == "cancelada"
    assert m.sales_totals(T, T)["net"] == 0
    assert "estorno" in [e.kind for e in o.events]
    assert m.integrity_report() == []
    with pytest.raises(ValidationError):
        svc.register_payment(o.id, amount=D("1"), pay_date=T, account_id=banco)


def test_venda_vinculada_so_cancela_pela_encomenda(accounts):
    o = make(initial_status="confirmado")
    with pytest.raises(ValidationError):
        finance.cancel_sale(o.sale_id, "tentativa direta")


def test_alterar_valores(accounts):
    banco, _ = accounts
    o = make(initial_status="confirmado")
    old_sale = o.sale_id
    data = svc.OrderInput(customer_name="Ana Souza", due_date=o.due_date, items=[item(qty="3")])
    svc.update_order(o.id, data)                              # sem pagamentos: refaz a venda
    db.session.refresh(o)
    assert o.total == D("54.00") and o.sale_id != old_sale and o.sale.net_amount == D("54.00")
    assert db.session.get(Sale, old_sale).status == "cancelada"
    assert m.open_total("receber") == D("54.00")
    svc.register_payment(o.id, amount=D("10.00"), pay_date=T, account_id=banco)
    with pytest.raises(ValidationError):
        svc.update_order(o.id, svc.OrderInput(customer_name="Ana", due_date=o.due_date, items=[item(qty="9")]))
    # dados não financeiros continuam editáveis, com antes/depois no histórico
    svc.update_order(o.id, svc.OrderInput(customer_name="Ana Souza Lima", due_date=o.due_date + timedelta(days=2),
                                          items=[item(qty="3")]))
    db.session.refresh(o)
    ev = [e for e in o.events if e.kind == "edicao"][-1]
    assert ("Cliente", "Ana Souza", "Ana Souza Lima") in ev.changes()
    assert o.sale.titles[0].due_date == o.due_date


def test_compras_e_despesas_nao_sao_inferidas(accounts):
    make(initial_status="confirmado")
    assert db.session.scalar(select(Title).where(Title.kind == "pagar")) is None


# ------------------------------------------------------------------ telas, filtros e relatórios
def test_telas_filtros_e_kanban(client, accounts):
    banco, _ = accounts
    r = client.post("/encomendas/nova", data={
        "customer_name": "Escola Pequeno Príncipe", "customer_phone": "51999990000", "channel": "whatsapp",
        "priority": "urgente", "due_date": (T + timedelta(days=1)).isoformat(), "delivery_mode": "entrega",
        "delivery_address": "Rua A, 10", "item_product": ["", ""], "item_desc": ["Kit escolar", "Laço extra"],
        "item_custom": ["Azul marinho", ""], "item_qty": ["10", "2"], "item_price": ["45,00", "18,00"],
        "item_discount": ["", "6,00"], "item_notes": ["", ""], "discount": "10,00", "initial_status": "novo",
        "request_key": "web-1", "deposit_amount": "100,00", "deposit_date": T.isoformat(),
        "deposit_account_id": str(banco)})
    assert r.status_code == 302
    o = db.session.scalar(select(Order))
    assert o.total == D("470.00") and o.received == D("100.00")
    assert o.created_by is not None and o.created_by.username == "carol"
    for url in ["/encomendas/", "/encomendas/?ver=tabela", f"/encomendas/{o.id}", f"/encomendas/{o.id}/editar",
                "/encomendas/nova", "/encomendas/?ver=tabela&q=DL-0001", "/encomendas/?ver=tabela&q=kit",
                "/encomendas/?ver=tabela&prioridade=urgente&modalidade=entrega&prazo=3dias&ordem=prioridade",
                "/", "/configuracoes/?aba=historico"]:
        assert client.get(url).status_code == 200, url
    row = f'class="row-link" href="/encomendas/{o.id}">DL-0001'
    assert row in client.get("/encomendas/?ver=tabela&q=kit").get_data(as_text=True)
    assert row in client.get("/encomendas/?ver=tabela&q=escola").get_data(as_text=True)
    assert row in client.get("/encomendas/?ver=tabela&q=DL-0001").get_data(as_text=True)
    assert row not in client.get("/encomendas/?ver=tabela&prioridade=normal").get_data(as_text=True)
    assert row not in client.get("/encomendas/?ver=tabela&q=inexistente").get_data(as_text=True)
    # mover pelo Kanban (JSON): etapa inválida é recusada, válida é aceita
    bad = client.post(f"/encomendas/{o.id}/status", json={"to": "entregue"})
    assert bad.status_code == 400 and bad.get_json()["ok"] is False
    ok = client.post(f"/encomendas/{o.id}/status", json={"to": "confirmado"})
    assert ok.get_json() == {"ok": True, "status": "confirmado", "label": "Confirmado"}
    ev = db.session.scalar(select(OrderEvent).where(OrderEvent.kind == "status"))
    assert ev.username == "carol" and ev.from_status == "novo" and ev.to_status == "confirmado"
    # celular
    for url in ["/encomendas/", f"/encomendas/{o.id}"]:
        html = client.get(url, headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"}).get_data(as_text=True)
        assert 'name="viewport"' in html


def test_relatorios_de_encomendas(client, accounts):
    banco, _ = accounts
    a = make(deposit_amount=D("20.00"), deposit_account_id=banco)
    b = make(customer_name="Bruna", items=[item("Tiara forrada", "3", "29.90")])
    for st in ("confirmado", "pronto", "entregue"):
        svc.change_status(a.id, st)
    svc.cancel_order(b.id, "Duplicada", refund=False)
    from app.services.reports import CATALOG
    for key in [k for k, _, _ in CATALOG if k.startswith("enc_")]:
        assert client.get(f"/relatorios/{key}?periodo=mes").status_code == 200, key
        x = client.get(f"/relatorios/{key}/exportar.xlsx?periodo=mes")
        assert x.status_code == 200
        load_workbook(io.BytesIO(x.data))
        assert client.get(f"/relatorios/{key}/exportar.csv?periodo=mes").status_code == 200
    csv = client.get("/relatorios/enc_recebidas/exportar.csv?periodo=mes").data.decode("utf-8-sig")
    assert "DL-0001" in csv and "DL-0002" in csv and "Valor do pedido" in csv and "Recebido" in csv
    csv = client.get("/relatorios/enc_recebidas/exportar.csv?periodo=mes&status=cancelado").data.decode("utf-8-sig")
    assert "DL-0002" in csv and "DL-0001" not in csv
    assert "DL-0001" in client.get("/relatorios/enc_entregues/exportar.csv?periodo=mes").data.decode("utf-8-sig")
    assert "Duplicada" in client.get("/relatorios/enc_canceladas/exportar.csv?periodo=mes").data.decode("utf-8-sig")
    top = client.get("/relatorios/enc_produtos/exportar.csv?periodo=mes").data.decode("utf-8-sig")
    assert "Laço cetim" in top and "Tiara forrada" in top  # tiara da encomenda cancelada não soma 3


def test_persistencia_apos_reinicio(app, accounts):
    o = make(initial_status="confirmado")
    code = o.code
    db.session.remove()
    db.engine.dispose()
    from app import create_app
    with create_app("testing").app_context():
        again = db.session.scalar(select(Order).where(Order.number == 1))
        assert again.code == code and again.sale is not None and len(again.items) == 2


def test_backup_inclui_encomendas(app, accounts):
    banco, _ = accounts
    make(deposit_amount=D("10"), deposit_account_id=banco)
    from app.services import backup
    payload = backup.export_all()
    assert len(payload["tables"]["orders"]) == 1 and payload["tables"]["order_events"]
    url = app.config["SQLALCHEMY_DATABASE_URI"].rsplit("/", 1)[0] + "/docelacos_restore"
    from app import create_app
    app_r = create_app("testing", SQLALCHEMY_DATABASE_URI=url)
    with app_r.app_context():
        from sqlalchemy import text
        db.drop_all()
        db.session.execute(text("DROP SEQUENCE IF EXISTS order_number_seq"))
        db.session.commit()
        db.create_all()
        from app.services.audit import install_history_guard
        with db.engine.begin() as conn:
            install_history_guard(conn)
        backup.restore_all(payload)
        o = db.session.scalar(select(Order))
        assert o.code == "DL-0001" and o.received == D("10.00")
        n = svc.create_order(svc.OrderInput(customer_name="Nova", due_date=T, items=[item()]))
        assert n.code == "DL-0002"  # sequência ajustada
        db.session.remove()
        db.drop_all()
