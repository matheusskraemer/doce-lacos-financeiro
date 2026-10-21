"""Despesas e outras receitas (lançamentos avulsos)."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import or_, select

from ..extensions import db
from ..models import Category, Title
from ..services import finance
from ..utils import ValidationError, resolve_period, today
from . import accounts, categories, f, f_bool, f_date, f_int, f_money, methods

bp = Blueprint("despesas", __name__)

CONFIG = {
    "pagar": {"origin": "despesa", "cat_kind": "despesa", "groups": ("operacional", "mercadoria"),
              "title": "Nova despesa", "list_endpoint": "despesas.index", "verb_now": "Já está paga",
              "noun": "Despesa"},
    "receber": {"origin": "receita", "cat_kind": "receita", "groups": ("outra_receita",),
                "title": "Nova receita", "list_endpoint": "vendas.receitas", "verb_now": "Já foi recebida",
                "noun": "Receita"},
}


def title_form(kind: str):
    cfg = CONFIG[kind]
    form = request.form if request.method == "POST" else {}
    if request.method == "POST":
        try:
            settle_now = f_bool("settle_now")
            data = finance.TitleInput(
                kind=kind, origin=cfg["origin"], description=f("description"),
                category_id=f_int("category_id", field="Categoria"),
                amount=f_money("amount", field="Valor"),
                competence_date=f_date("competence_date", field="Data de competência"),
                due_date=f_date("due_date", field="Vencimento"),
                counterparty=f("counterparty"),
                expected_method_id=f_int("method_id"), expected_account_id=f_int("account_id"),
                notes=f("notes"),
                installments=f_int("installments", 1, field="Parcelas", lo=1, hi=48),
                recurrence_months=f_int("recurrence", 1, field="Recorrência", lo=1, hi=36),
                settle_now=settle_now,
                settle_date=f_date("settle_date", field="Data do pagamento", required=False) if settle_now else None,
                settle_account_id=f_int("account_id") if settle_now else None,
                request_key=f("request_key") or None,
            )
            if settle_now and not data.settle_account_id:
                raise ValidationError("Conta financeira: escolha a conta de onde saiu/entrou o dinheiro.")
            titles = finance.create_titles(data)
            n = len(titles)
            flash(f"{cfg['noun']} registrada" + (f" em {n} lançamentos." if n > 1 else "."), "success")
            if request.form.get("again"):
                return redirect(request.path)
            return redirect(url_for("titulos.detail", title_id=titles[0].id))
        except finance.DuplicateSubmission as exc:
            flash(str(exc), "warning")
            return redirect(url_for(cfg["list_endpoint"]))
        except ValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    cats = categories(cfg["cat_kind"], cfg["groups"])
    return render_template("titles/form.html", kind=kind, cfg=cfg, form=form, cats=cats,
                           accounts=accounts(), methods=methods(), today=today())


@bp.route("/despesas/nova", methods=["GET", "POST"])
def new():
    return title_form("pagar")


@bp.get("/despesas")
def index():
    return title_list("pagar", origins=("despesa", "compra"), template_title="Despesas",
                      new_url=url_for("despesas.new"))


def title_list(kind: str, *, origins: tuple[str, ...], template_title: str, new_url: str, extra_tabs=None):
    period = resolve_period(request.args.get("periodo") or "mes", request.args.get("inicio"), request.args.get("fim"))
    basis = request.args.get("base") or "competencia"
    status = request.args.get("status") or ""
    cat_id = request.args.get("categoria", type=int)
    search = (request.args.get("q") or "").strip()
    date_col = Title.competence_date if basis == "competencia" else Title.due_date
    stmt = (select(Title).where(Title.kind == kind, Title.origin.in_(origins), date_col.between(period.start, period.end))
            .order_by(date_col.desc(), Title.id.desc()))
    if cat_id:
        stmt = stmt.where(Title.category_id == cat_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Title.description.ilike(like), Title.counterparty.ilike(like), Title.notes.ilike(like)))
    rows = db.session.scalars(stmt.limit(1000)).all()
    ref = today()
    if status:
        rows = [t for t in rows if t.status_on(ref) == status]
    cat_kind = "despesa" if kind == "pagar" else "receita"
    cats = db.session.scalars(select(Category).where(Category.kind == cat_kind).order_by(Category.name)).all()
    from ..utils import ZERO, q
    totals = {
        "amount": q(sum((t.amount for t in rows if not t.is_canceled), ZERO)),
        "settled": q(sum((t.settled_amount for t in rows), ZERO)),
        "open": q(sum((t.open_amount for t in rows), ZERO)),
    }
    return render_template("titles/list.html", rows=rows, kind=kind, period=period, basis=basis, status=status,
                           cat_id=cat_id, cats=cats, search=search, totals=totals, page_title=template_title,
                           new_url=new_url, extra_tabs=extra_tabs, ref=ref)
