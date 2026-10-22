"""Etapa de build no Vercel: prepara o banco de PRODUÇÃO antes da nova versão entrar no ar.

O Vercel não tem o "pre-deploy" do Render. Este script roda no build e:

* só age quando VERCEL_ENV == "production" (deploys de pré-visualização nunca
  mexem no banco de produção);
* usa a conexão DIRETA do Neon (DATABASE_URL_UNPOOLED), como recomendado para migrações;
* aplica as migrações (flask db upgrade), cria categorias/formas de pagamento
  padrão (setup) e, se as variáveis INITIAL_ADMIN_* estiverem definidas e ainda
  não houver usuário, cria o primeiro usuário (ensure-user).

As migrações deste projeto são aditivas: se o build falhar depois delas, a versão
anterior continua funcionando com o banco já migrado.

Uso local para testar:  VERCEL_ENV=production DATABASE_URL_UNPOOLED=... python scripts/vercel_build.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_dependencies() -> None:
    """Garante que as dependências estejam importáveis neste processo de build."""
    try:
        import flask  # noqa: F401
        import flask_migrate  # noqa: F401
        import psycopg  # noqa: F401
        return
    except ImportError:
        pass
    target = os.path.join("/tmp", "docelacos-build-deps")
    print("Instalando dependências para a etapa de build…", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--target", target, "-r", os.path.join(ROOT, "requirements.txt")], check=True)
    sys.path.insert(0, target)


def main() -> int:
    env = os.environ.get("VERCEL_ENV", "")
    if env != "production":
        print(f"VERCEL_ENV={env or '(vazio)'}: banco não será alterado neste build.")
        return 0

    direct = (os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("POSTGRES_URL_NON_POOLING")
              or os.environ.get("DATABASE_URL"))
    if not direct:
        print("ERRO: nenhuma URL de banco definida (DATABASE_URL_UNPOOLED / DATABASE_URL). "
              "Conecte o Neon ao projeto no Vercel.", file=sys.stderr)
        return 1
    if not os.environ.get("SECRET_KEY") or len(os.environ["SECRET_KEY"]) < 32:
        print("ERRO: defina SECRET_KEY (mínimo 32 caracteres) nas variáveis de ambiente do Vercel.", file=sys.stderr)
        return 1

    os.environ["DATABASE_URL"] = direct
    os.environ["APP_ENV"] = "production"
    os.environ.pop("VERCEL", None)  # pool padrão durante o build
    _ensure_dependencies()
    sys.path.insert(0, ROOT)

    from flask_migrate import upgrade

    from app import create_app
    from app.cli import ensure_user_logic, seed_defaults

    app = create_app("production")
    with app.app_context():
        print("Aplicando migrações do banco…", flush=True)
        upgrade(directory=os.path.join(ROOT, "migrations"))
        seed_defaults()
        print(ensure_user_logic())
        from sqlalchemy import text

        from app.extensions import db
        rev = db.session.execute(text("select version_num from alembic_version")).scalar()
        print(f"Banco pronto (versão {rev}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
