"""Comandos de administração (executados no terminal da hospedagem ou localmente).

    flask --app wsgi setup             # categorias e formas de pagamento padrão
    flask --app wsgi create-user       # cria o único usuário (pede a senha)
    flask --app wsgi reset-password    # redefine a senha (recuperação de acesso)
    flask --app wsgi backup-json -o arquivo.json
    flask --app wsgi restore-json arquivo.json [--force]
    flask --app wsgi check             # conferência de integridade financeira
"""
from __future__ import annotations

import getpass
import json
import re
import sys

import click
from sqlalchemy import func, select

from .extensions import db
from .models import Category, CompanySettings, PaymentMethod, User

DEFAULT_CATEGORIES = [
    # (nome, tipo, grupo, sistema)
    ("Vendas", "receita", "venda", True),
    ("Reembolsos", "receita", "outra_receita", False),
    ("Aporte dos sócios", "receita", "outra_receita", False),
    ("Rendimentos", "receita", "outra_receita", False),
    ("Outras receitas", "receita", "outra_receita", False),
    ("Compra de mercadorias", "despesa", "mercadoria", True),
    ("Embalagens", "despesa", "operacional", False),
    ("Frete e entregas", "despesa", "operacional", False),
    ("Aluguel e infraestrutura", "despesa", "operacional", False),
    ("Água, energia e internet", "despesa", "operacional", False),
    ("Marketing e publicidade", "despesa", "operacional", False),
    ("Taxas de cartão e bancárias", "despesa", "operacional", False),
    ("Impostos e taxas", "despesa", "operacional", False),
    ("Sistemas e ferramentas", "despesa", "operacional", False),
    ("Materiais de consumo", "despesa", "operacional", False),
    ("Serviços terceirizados", "despesa", "operacional", False),
    ("Outras despesas", "despesa", "operacional", False),
]

DEFAULT_METHODS = ["Pix", "Dinheiro", "Cartão de crédito", "Cartão de débito", "Boleto", "Transferência bancária"]


def seed_defaults() -> None:
    if db.session.get(CompanySettings, 1) is None:
        db.session.add(CompanySettings(id=1))
    for name, kind, group, system in DEFAULT_CATEGORIES:
        exists = db.session.scalar(select(Category.id).where(Category.name == name, Category.kind == kind))
        if not exists:
            db.session.add(Category(name=name, kind=kind, group=group, system=system))
    for name in DEFAULT_METHODS:
        if not db.session.scalar(select(PaymentMethod.id).where(PaymentMethod.name == name)):
            db.session.add(PaymentMethod(name=name))
    db.session.commit()


def validate_password(pw: str) -> str | None:
    if len(pw) < 10:
        return "A senha precisa ter pelo menos 10 caracteres."
    if pw.lower() == pw or pw.upper() == pw or not re.search(r"\d", pw):
        return "Use letras maiúsculas, minúsculas e ao menos um número."
    return None


def _ask_password() -> str:
    while True:
        pw = getpass.getpass("Nova senha: ")
        err = validate_password(pw)
        if err:
            click.echo(err)
            continue
        if getpass.getpass("Repita a senha: ") != pw:
            click.echo("As senhas não conferem.")
            continue
        return pw


def register_cli(app) -> None:
    @app.cli.command("setup")
    def setup_cmd():
        """Cria configurações, categorias e formas de pagamento padrão (idempotente)."""
        seed_defaults()
        click.echo("Configuração inicial concluída.")

    @app.cli.command("create-user")
    @click.option("--username", prompt="Usuário (login)")
    @click.option("--name", "display_name", default="", help="Nome de exibição")
    def create_user_cmd(username, display_name):
        """Cria o usuário do sistema (somente um)."""
        seed_defaults()
        if db.session.scalar(select(func.count(User.id))) > 0:
            click.echo("Já existe um usuário. Use reset-password para trocar a senha.")
            sys.exit(1)
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9._@-]{3,80}", username):
            click.echo("Usuário inválido: use letras minúsculas, números, ponto, hífen ou @ (3 a 80 caracteres).")
            sys.exit(1)
        user = User(username=username, display_name=display_name or None, password_hash="!")
        user.set_password(_ask_password())
        db.session.add(user)
        db.session.commit()
        click.echo(f"Usuário “{username}” criado.")

    @app.cli.command("reset-password")
    @click.option("--username", prompt="Usuário (login)")
    def reset_password_cmd(username):
        """Redefine a senha e encerra todas as sessões abertas."""
        user = db.session.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
        if not user:
            click.echo("Usuário não encontrado.")
            sys.exit(1)
        user.set_password(_ask_password())
        user.failed_attempts = 0
        user.locked_until = None
        db.session.commit()
        click.echo("Senha redefinida. Sessões anteriores foram encerradas.")

    @app.cli.command("backup-json")
    @click.option("-o", "--output", required=True, help="Arquivo de saída (.json)")
    def backup_cmd(output):
        """Gera cópia completa dos dados em JSON."""
        from .services.backup import dumps, export_all
        payload = export_all()
        with open(output, "wb") as fh:
            fh.write(dumps(payload))
        total = sum(len(v) for v in payload["tables"].values())
        click.echo(f"Cópia gerada em {output} ({total} registros, sha256 {payload['sha256'][:12]}…).")

    @app.cli.command("restore-json")
    @click.argument("path")
    @click.option("--force", is_flag=True, help="Apaga os dados atuais antes de restaurar")
    def restore_cmd(path, force):
        """Restaura uma cópia JSON gerada pelo sistema."""
        from .services.backup import RestoreError, restore_all
        with open(path, "rb") as fh:
            payload = json.load(fh)
        try:
            counts = restore_all(payload, force=force)
        except RestoreError as exc:
            click.echo(f"Restauração cancelada: {exc}")
            sys.exit(1)
        click.echo("Restauração concluída: " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    @app.cli.command("check")
    def check_cmd():
        """Confere a integridade dos cálculos e saldos."""
        from .services.metrics import integrity_report
        problems = integrity_report()
        if problems:
            for p in problems:
                click.echo(f"• {p}")
            sys.exit(1)
        click.echo("Nenhuma inconsistência encontrada.")
