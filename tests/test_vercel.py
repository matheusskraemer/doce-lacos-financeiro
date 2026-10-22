"""Adaptações para o Vercel: restauração pela tela (migração de dados) e configuração do banco."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

from flask import g
from sqlalchemy import select

from app.extensions import db
from app.models import Account, AuditLog, Sale, User
from app.services import metrics as m
from app.utils import today

from .conftest import PASSWORD

FIXTURE = Path(__file__).parent / "fixtures" / "backup_v1.json"
ROOT = Path(__file__).parent.parent


def _upload(client, data: bytes, password=PASSWORD):
    return client.post("/configuracoes/restaurar", data={"password": password,
                                                         "arquivo": (io.BytesIO(data), "copia.json")},
                       content_type="multipart/form-data")


def test_restaura_copia_da_versao_anterior_em_banco_novo(app, client):
    page = client.get("/configuracoes/?aba=backup").get_data(as_text=True)
    assert "Restaurar cópia" in page
    r = _upload(client, FIXTURE.read_bytes())
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    # dados da versão anterior, com os campos novos preenchidos pelos padrões
    u = db.session.scalar(select(User))
    assert u.username == "carolina" and u.active is True and u.check_password("SenhaAntiga2026")
    assert db.session.query(Sale).count() == 1
    assert m.total_balance(today()) == D("580.00")
    assert m.open_total("pagar") == D("30.00")
    assert m.integrity_report() == []
    assert db.session.scalar(select(AuditLog).where(AuditLog.action == "restaurar")) is not None
    # quem restaurou foi desconectado; entra com a senha da cópia
    g.pop("_login_user", None)
    assert client.get("/").status_code == 302
    assert client.post("/entrar", data={"username": "carolina", "password": "SenhaAntiga2026"}).status_code == 302
    g.pop("_login_user", None)
    # com lançamentos, a restauração pela tela deixa de existir
    assert "Restaurar cópia" not in client.get("/configuracoes/?aba=backup").get_data(as_text=True)
    r = _upload(client, FIXTURE.read_bytes(), password="SenhaAntiga2026")
    assert db.session.query(Sale).count() == 1


def test_restauracao_recusa_banco_com_dados_senha_errada_e_arquivo_alterado(app, client):
    assert _upload(client, FIXTURE.read_bytes(), password="errada").status_code == 302
    assert db.session.query(Sale).count() == 0
    bad = json.loads(FIXTURE.read_text())
    bad["tables"]["sales"][0]["net_amount"] = "1.00"
    _upload(client, json.dumps(bad).encode())
    assert db.session.query(Sale).count() == 0
    _upload(client, b"isto nao e json")
    assert db.session.query(User).count() == 1
    db.session.add(Account(name="Banco", opening_balance=0, opening_date=today()))
    db.session.commit()
    _upload(client, FIXTURE.read_bytes())
    assert db.session.scalar(select(User)).username == "carol"


def test_restauracao_exige_login(app, user):
    c = app.test_client()
    r = _upload(c, FIXTURE.read_bytes())
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    assert db.session.query(Sale).count() == 0


def test_estaticos_servidos_de_public(client):
    r = client.get("/static/vendor/bootstrap/bootstrap.min.css")
    assert r.status_code == 200 and (ROOT / "public" / "static" / "vendor" / "bootstrap" / "bootstrap.min.css").exists()


def test_opcoes_de_conexao_serverless(monkeypatch):
    from app import config
    monkeypatch.setenv("VERCEL", "1")
    opts = config._engine_options("postgresql+psycopg://u:p@ep-x-pooler.sa-east-1.aws.neon.tech/db?sslmode=require")
    assert opts["pool_size"] == 1 and opts["connect_args"] == {"prepare_threshold": None}
    monkeypatch.delenv("VERCEL")
    assert "connect_args" not in config._engine_options("postgresql+psycopg://u:p@localhost/db")


def test_build_nao_mexe_no_banco_fora_de_producao():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VERCEL", "DATABASE_URL"))}
    env["VERCEL_ENV"] = "preview"
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "vercel_build.py")], env=env, capture_output=True,
                         text=True)
    assert out.returncode == 0 and "não será alterado" in out.stdout
