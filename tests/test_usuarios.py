"""Usuários individuais, proteção administrativa e autoria."""
from __future__ import annotations

from decimal import Decimal as D

import pytest
from flask import g
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.extensions import db
from app.models import AuditLog, Sale, Title, User
from app.utils import today

from .conftest import PASSWORD

T = today()
PW2 = "OutraPessoa2026"


def _create_second(client, **over):
    data = {"display_name": "Carolina Eloi", "username": "carolina", "password": PW2, "confirm_password": PW2,
            "admin_password": PASSWORD}
    data.update(over)
    return client.post("/configuracoes/usuarios", data=data)


def _fresh(app):
    g.pop("_login_user", None)  # o contexto de aplicação é compartilhado nos testes
    c = app.test_client()
    return c


def test_cadastro_e_login_de_segundo_usuario(app, client):
    r = _create_second(client)
    assert r.status_code == 302
    u = db.session.scalar(select(User).where(User.username == "carolina"))
    assert u and u.active and u.password_hash.startswith("scrypt:") and PW2 not in u.password_hash
    assert u.created_by_id == db.session.scalar(select(User.id).where(User.username == "carol"))
    c2 = _fresh(app)
    assert c2.post("/entrar", data={"username": "carolina", "password": PW2}).status_code == 302
    g.pop("_login_user", None)
    assert c2.get("/").status_code == 200


def test_login_unico_e_senha_forte(client):
    _create_second(client)
    _create_second(client, display_name="Outra")  # mesmo login
    assert db.session.query(User).filter(User.username == "carolina").count() == 1
    _create_second(client, username="fraca", password="123", confirm_password="123")
    assert db.session.scalar(select(User).where(User.username == "fraca")) is None


def test_operacao_administrativa_exige_senha_de_quem_esta_logado(client):
    _create_second(client, admin_password="errada")
    assert db.session.scalar(select(User).where(User.username == "carolina")) is None
    _create_second(client)
    u = db.session.scalar(select(User).where(User.username == "carolina"))
    client.post(f"/configuracoes/usuarios/{u.id}", data={"action": "toggle", "admin_password": "errada"})
    db.session.refresh(u)
    assert u.active


def test_rotas_administrativas_exigem_login(app, user):
    c = app.test_client()
    r = c.post("/configuracoes/usuarios", data={"display_name": "X", "username": "x", "password": PW2,
                                                "confirm_password": PW2, "admin_password": PASSWORD})
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    assert db.session.query(User).count() == 1
    runner = app.test_cli_runner()
    res = runner.invoke(args=["create-user", "--username", "outro"], input="x\n")
    assert res.exit_code != 0 and db.session.query(User).count() == 1  # comando só cria o primeiro usuário


def test_usuario_desativado_perde_acesso_na_hora(app, client):
    _create_second(client)
    u = db.session.scalar(select(User).where(User.username == "carolina"))
    c2 = _fresh(app)
    c2.post("/entrar", data={"username": "carolina", "password": PW2})
    g.pop("_login_user", None)
    assert c2.get("/").status_code == 200
    client.post(f"/configuracoes/usuarios/{u.id}", data={"action": "toggle", "admin_password": PASSWORD})
    db.session.refresh(u)
    assert not u.active and u.deactivated_at
    g.pop("_login_user", None)
    assert c2.get("/").status_code == 302  # sessão aberta derrubada
    r = _fresh(app).post("/entrar", data={"username": "carolina", "password": PW2})
    assert r.status_code == 401 and "Usuário ou senha inválidos" in r.get_data(as_text=True)


def test_nao_desativa_o_proprio_acesso(client):
    me = db.session.scalar(select(User).where(User.username == "carol"))
    client.post(f"/configuracoes/usuarios/{me.id}", data={"action": "toggle", "admin_password": PASSWORD})
    db.session.refresh(me)
    assert me.active


def test_redefinir_senha_derruba_sessoes(app, client):
    _create_second(client)
    u = db.session.scalar(select(User).where(User.username == "carolina"))
    c2 = _fresh(app)
    c2.post("/entrar", data={"username": "carolina", "password": PW2})
    new = "NovaSenha2026x"
    client.post(f"/configuracoes/usuarios/{u.id}", data={"action": "reset", "password": new,
                                                         "confirm_password": new, "admin_password": PASSWORD})
    g.pop("_login_user", None)
    assert c2.get("/").status_code == 302
    assert _fresh(app).post("/entrar", data={"username": "carolina", "password": new}).status_code == 302
    log = db.session.scalars(select(AuditLog).where(AuditLog.entity == "usuario")).all()
    assert not any(new in (l.summary + (l.before or "") + (l.after or "")) for l in log)  # senha nunca no log


def test_autoria_preservada_mesmo_com_usuario_desativado(app, client):
    client.post("/configuracoes/contas", data={"name": "Banco", "kind": "banco", "opening_balance": "100",
                                               "opening_date": T.isoformat(), "include_in_total": "1"})
    _create_second(client)
    u = db.session.scalar(select(User).where(User.username == "carolina"))
    c2 = _fresh(app)
    c2.post("/entrar", data={"username": "carolina", "password": PW2})
    g.pop("_login_user", None)
    c2.post("/vendas/nova", data={"description": "Venda da Carol", "sale_date": T.isoformat(), "gross_amount": "50",
                                  "received_now": "", "installments": "1", "request_key": "rk-a1"})
    sale = db.session.scalar(select(Sale))
    assert sale.created_by_id == u.id and sale.titles[0].created_by_id == u.id
    g.pop("_login_user", None)
    client.post(f"/configuracoes/usuarios/{u.id}", data={"action": "toggle", "admin_password": PASSWORD})
    db.session.expire_all()
    entry = db.session.scalar(select(AuditLog).where(AuditLog.entity == "venda"))
    assert entry.user_id == u.id and entry.author == "Carolina Eloi"
    html = client.get(f"/vendas/{sale.id}").get_data(as_text=True)
    assert "registrada por Carolina Eloi" in html


def test_edicao_registra_antes_e_depois(app, client):
    client.post("/despesas/nova", data={"description": "Frete", "category_id": "8", "amount": "20,00",
                                        "competence_date": T.isoformat(), "due_date": T.isoformat(),
                                        "installments": "1", "recurrence": "1", "request_key": "rk-e1"})
    t = db.session.scalar(select(Title))
    client.post(f"/lancamentos/{t.id}/editar", data={"description": "Frete Correios", "category_id": "8",
                                                     "counterparty": "", "competence_date": T.isoformat(),
                                                     "due_date": T.isoformat(), "amount": "25,00"})
    log = db.session.scalar(select(AuditLog).where(AuditLog.action == "editar", AuditLog.entity == "titulo"))
    changes = {k: (b, a) for k, b, a in log.changes()}
    assert changes["Valor"] == ("R$ 20,00", "R$ 25,00")
    assert changes["Descrição"] == ("Frete", "Frete Correios")
    assert log.author == "carol"


def test_historico_nao_pode_ser_alterado(app, client):
    client.post("/configuracoes/contas", data={"name": "Banco", "kind": "banco", "opening_balance": "0",
                                               "opening_date": T.isoformat(), "include_in_total": "1"})
    assert db.session.query(AuditLog).count() >= 1
    for stmt in ("UPDATE audit_log SET summary = 'falso'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError):
            db.session.execute(text(stmt))
            db.session.flush()
        db.session.rollback()
    assert db.session.query(AuditLog).count() >= 1


def test_tela_de_usuarios(client):
    _create_second(client)
    html = client.get("/configuracoes/?aba=usuarios").get_data(as_text=True)
    assert "Carolina Eloi" in html and "Último acesso" in html and "Você não pode desativar o próprio acesso" in html
