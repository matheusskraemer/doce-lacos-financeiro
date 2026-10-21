"""Configurações: identidade, contas financeiras, categorias, formas de pagamento, senha e backup."""
from __future__ import annotations

import re
from datetime import datetime

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, logout_user
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..cli import validate_password
from ..extensions import db
from ..models import (ACCOUNT_KINDS, CATEGORY_GROUPS, Account, AuditLog, Category, CompanySettings, PaymentMethod,
                      Settlement, Title, Transfer, utcnow)
from ..services import metrics as m
from ..services.finance import audit
from ..utils import ZERO, ValidationError, parse_money, today
from . import f, f_bool, f_date, f_int

bp = Blueprint("config", __name__, url_prefix="/configuracoes")

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
LOGO_TYPES = {"image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}


def _settings() -> CompanySettings:
    s = db.session.get(CompanySettings, 1)
    if s is None:
        s = CompanySettings(id=1)
        db.session.add(s)
        db.session.flush()
    return s


@bp.get("/")
def index():
    tab = request.args.get("aba") or "empresa"
    usage = dict(db.session.execute(select(Title.category_id, func.count(Title.id)).group_by(Title.category_id)).all())
    acc_usage = {}
    for a_id, n in db.session.execute(select(Settlement.account_id, func.count(Settlement.id)).group_by(Settlement.account_id)):
        acc_usage[a_id] = acc_usage.get(a_id, 0) + n
    for col in (Transfer.from_account_id, Transfer.to_account_id):
        for a_id, n in db.session.execute(select(col, func.count(Transfer.id)).group_by(col)):
            acc_usage[a_id] = acc_usage.get(a_id, 0) + n
    log = db.session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(60)).all()
    return render_template(
        "config/index.html", tab=tab, s=_settings(), balances=m.account_balances(today()),
        categories=db.session.scalars(select(Category).order_by(Category.kind, Category.group, Category.name)).all(),
        methods=db.session.scalars(select(PaymentMethod).order_by(PaymentMethod.name)).all(),
        kinds=ACCOUNT_KINDS, groups=CATEGORY_GROUPS, usage=usage, acc_usage=acc_usage, log=log, today=today(),
    )


# ------------------------------------------------------------------ empresa e identidade
@bp.post("/empresa")
def empresa():
    s = _settings()
    try:
        name = f("company_name")
        if not name:
            raise ValidationError("Nome da empresa: informe o nome.")
        s.company_name = name[:120]
        s.tagline = f("tagline")[:160] or None
        for field, label in (("color_primary", "Cor principal"), ("color_accent", "Cor de destaque"), ("color_ink", "Cor do menu")):
            val = f(field)
            if not HEX.match(val):
                raise ValidationError(f"{label}: use o formato #RRGGBB.")
            setattr(s, field, val.upper())
        s.projection_days = f_int("projection_days", 30, field="Horizonte de projeção", lo=7, hi=180)
        s.upcoming_days = f_int("upcoming_days", 7, field="Aviso de vencimento", lo=1, hi=60)
        upload = request.files.get("logo")
        if upload and upload.filename:
            data = upload.read()
            mime = upload.mimetype
            if mime == "image/svg+xml" or upload.filename.lower().endswith(".svg"):
                text = data.decode("utf-8", errors="ignore").lower()
                if "<svg" not in text or "<script" in text or "javascript:" in text or "onload" in text or "<foreignobject" in text:
                    raise ValidationError("Logotipo: arquivo SVG inválido ou com conteúdo ativo não permitido.")
                mime = "image/svg+xml"
            elif mime not in LOGO_TYPES or not data.startswith(LOGO_TYPES[mime]):
                raise ValidationError("Logotipo: envie PNG, JPG, WEBP ou SVG.")
            s.logo_data, s.logo_mime, s.logo_updated_at = data, mime, utcnow()
        if f_bool("remove_logo"):
            s.logo_data = s.logo_mime = None
        audit("editar", "configuracoes", 1, "Identidade e preferências atualizadas")
        db.session.commit()
        flash("Configurações salvas.", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("config.index", aba="empresa"))


# ------------------------------------------------------------------ contas financeiras
@bp.post("/contas")
def nova_conta():
    try:
        name = f("name")
        if not name:
            raise ValidationError("Nome da conta: informe o nome.")
        kind = f("kind") if f("kind") in ACCOUNT_KINDS else "outra"
        acc = Account(name=name[:80], kind=kind,
                      opening_balance=parse_money(request.form.get("opening_balance") or "0", field="Saldo inicial",
                                                  allow_zero=True, allow_negative=True),
                      opening_date=f_date("opening_date", field="Data do saldo inicial"),
                      include_in_total=f_bool("include_in_total"), notes=f("notes") or None)
        db.session.add(acc)
        db.session.flush()
        audit("criar", "conta", acc.id, f"Conta “{acc.name}” com saldo inicial {acc.opening_balance}")
        db.session.commit()
        flash("Conta financeira cadastrada.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Já existe uma conta com este nome.", "danger")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("config.index", aba="contas"))


@bp.post("/contas/<int:account_id>")
def editar_conta(account_id: int):
    acc = db.session.get(Account, account_id) or abort(404)
    try:
        if request.form.get("action") == "toggle":
            acc.active = not acc.active
            audit("editar", "conta", acc.id, f"Conta “{acc.name}” {'reativada' if acc.active else 'desativada'}")
        else:
            name = f("name")
            if not name:
                raise ValidationError("Nome da conta: informe o nome.")
            new_opening = parse_money(request.form.get("opening_balance") or "0", field="Saldo inicial",
                                      allow_zero=True, allow_negative=True)
            if new_opening != acc.opening_balance:
                audit("editar", "conta", acc.id, f"Saldo inicial de “{acc.name}”: {acc.opening_balance} → {new_opening}")
            acc.name = name[:80]
            acc.kind = f("kind") if f("kind") in ACCOUNT_KINDS else acc.kind
            acc.opening_balance = new_opening
            acc.opening_date = f_date("opening_date", field="Data do saldo inicial")
            acc.include_in_total = f_bool("include_in_total")
            acc.notes = f("notes") or None
        db.session.commit()
        flash("Conta atualizada.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Já existe uma conta com este nome.", "danger")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("config.index", aba="contas"))


# ------------------------------------------------------------------ categorias
@bp.post("/categorias")
def nova_categoria():
    try:
        name = f("name")
        group = f("group")
        if not name:
            raise ValidationError("Nome da categoria: informe o nome.")
        if group not in CATEGORY_GROUPS or group in ("venda", "mercadoria"):
            raise ValidationError("Tipo de categoria inválido.")
        kind = CATEGORY_GROUPS[group][0]
        db.session.add(Category(name=name[:80], kind=kind, group=group))
        db.session.commit()
        flash("Categoria criada.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Já existe uma categoria com este nome.", "danger")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("config.index", aba="categorias"))


@bp.post("/categorias/<int:cat_id>")
def editar_categoria(cat_id: int):
    cat = db.session.get(Category, cat_id) or abort(404)
    try:
        if request.form.get("action") == "toggle":
            if cat.system:
                raise ValidationError("Categorias do sistema (Vendas e Compra de mercadorias) não podem ser desativadas.")
            cat.active = not cat.active
        else:
            name = f("name")
            if not name:
                raise ValidationError("Nome da categoria: informe o nome.")
            cat.name = name[:80]
        db.session.commit()
        flash("Categoria atualizada. Lançamentos antigos mantêm a categoria." if not cat.active else "Categoria atualizada.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Já existe uma categoria com este nome.", "danger")
    except ValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("config.index", aba="categorias"))


# ------------------------------------------------------------------ formas de pagamento
@bp.post("/formas")
def nova_forma():
    try:
        name = f("name")
        if not name:
            raise ValidationError("Nome: informe a forma de pagamento.")
        db.session.add(PaymentMethod(name=name[:60]))
        db.session.commit()
        flash("Forma de pagamento criada.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Esta forma de pagamento já existe.", "danger")
    except ValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("config.index", aba="formas"))


@bp.post("/formas/<int:method_id>")
def editar_forma(method_id: int):
    pm = db.session.get(PaymentMethod, method_id) or abort(404)
    if request.form.get("action") == "toggle":
        pm.active = not pm.active
    else:
        name = f("name")
        if name:
            pm.name = name[:60]
    try:
        db.session.commit()
        flash("Forma de pagamento atualizada.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Esta forma de pagamento já existe.", "danger")
    return redirect(url_for("config.index", aba="formas"))


# ------------------------------------------------------------------ segurança
@bp.post("/senha")
def senha():
    user = current_user._get_current_object()
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    if not user.check_password(current):
        flash("Senha atual incorreta.", "danger")
        return redirect(url_for("config.index", aba="seguranca"))
    err = validate_password(new)
    if err or new != confirm:
        flash(err or "A confirmação não confere com a nova senha.", "danger")
        return redirect(url_for("config.index", aba="seguranca"))
    user.set_password(new)
    audit("editar", "usuario", user.id, "Senha alterada")
    db.session.commit()
    logout_user()
    flash("Senha alterada. Entre novamente com a nova senha.", "success")
    return redirect(url_for("auth.login"))


# ------------------------------------------------------------------ backup
@bp.post("/backup")
def backup():
    from ..services.backup import dumps, export_all
    user = current_user._get_current_object()
    if not user.check_password(request.form.get("password") or ""):
        flash("Confirme sua senha para baixar a cópia completa.", "danger")
        return redirect(url_for("config.index", aba="backup"))
    s = _settings()
    s.last_backup_download_at = utcnow()
    audit("exportar", "backup", None, "Cópia completa dos dados baixada")
    db.session.commit()
    payload = export_all()
    name = f"doce-lacos_copia-completa_{datetime.now():%Y%m%d-%H%M}.json"
    return Response(dumps(payload), mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.get("/conferencia")
def conferencia():
    problems = m.integrity_report()
    if problems:
        for p in problems[:10]:
            flash(p, "danger")
    else:
        flash("Conferência concluída: saldos e lançamentos consistentes.", "success")
    return redirect(url_for("config.index", aba="backup"))
