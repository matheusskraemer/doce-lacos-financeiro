"""Relatórios com filtros e exportação."""
from __future__ import annotations

from flask import Blueprint, Response, abort, render_template, request
from sqlalchemy import select

from ..extensions import db
from ..models import Category
from ..services import reports as rp
from ..utils import PERIOD_CHOICES, ValidationError, add_months, fmt_date, parse_date, resolve_period, today
from . import accounts

bp = Blueprint("relatorios", __name__)


def _filters():
    period = resolve_period(request.args.get("periodo") or "mes", request.args.get("inicio"), request.args.get("fim"))
    def _month(name):
        raw = request.args.get(name)
        if not raw:
            return None
        try:
            return parse_date(raw + "-01" if len(raw) == 7 else raw, required=False)
        except ValidationError:
            return None
    flt = rp.Filters(
        start=period.start, end=period.end,
        category_id=request.args.get("categoria", type=int),
        account_id=request.args.get("conta", type=int),
        status=request.args.get("status") or "",
        search=(request.args.get("q") or "").strip()[:80],
        basis=request.args.get("base") or "caixa",
        month_a=_month("mes_a"), month_b=_month("mes_b"),
    )
    return period, flt


@bp.get("/relatorios")
def index():
    return render_template("relatorios/index.html", catalog=rp.CATALOG)


@bp.get("/relatorios/<key>")
def show(key: str):
    if key not in rp.CATALOG_KEYS:
        abort(404)
    period, flt = _filters()
    rep = rp.build(key, flt)
    cats = db.session.scalars(select(Category).order_by(Category.kind, Category.name)).all()
    ref = today()
    return render_template("relatorios/report.html", rep=rep, period=period, flt=flt, period_choices=PERIOD_CHOICES,
                           cats=cats, accounts=accounts(active_only=False), catalog=rp.CATALOG,
                           default_a=add_months(ref, -1).strftime("%Y-%m"), default_b=ref.strftime("%Y-%m"))


@bp.get("/relatorios/<key>/exportar.<fmt>")
def export(key: str, fmt: str):
    if key not in rp.CATALOG_KEYS or fmt not in ("csv", "xlsx"):
        abort(404)
    period, flt = _filters()
    rep = rp.build(key, flt)
    stamp = f"{flt.start:%Y%m%d}-{flt.end:%Y%m%d}"
    filename = f"doce-lacos_{key}_{stamp}.{fmt}"
    if fmt == "csv":
        data, mime = rp.to_csv(rep), "text/csv; charset=utf-8"
    else:
        sub = f"Período: {fmt_date(flt.start)} a {fmt_date(flt.end)} · gerado em {fmt_date(today())}"
        data, mime = rp.to_xlsx(rep, sub), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(data, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
