"""Visão geral."""
from __future__ import annotations

import json

from flask import Blueprint, render_template, request
from sqlalchemy import func, select

from ..extensions import db
from ..models import Account, CompanySettings, Sale, Settlement, Title
from ..services import metrics as m
from ..utils import PERIOD_CHOICES, ZERO, resolve_period, today

bp = Blueprint("dashboard", __name__)


def period_from_request(default_key: str = "mes"):
    return resolve_period(request.args.get("periodo") or default_key, request.args.get("inicio"), request.args.get("fim"))


@bp.get("/")
def index():
    settings = db.session.get(CompanySettings, 1)
    period = period_from_request()
    ref = today()
    horizon = settings.projection_days if settings else 30
    upcoming_days = settings.upcoming_days if settings else 7

    sales = m.sales_totals(period.start, period.end)
    cash = m.cash_totals(period.start, period.end)
    paid_groups = m.paid_by_group(period.start, period.end)
    purchases = m.purchases_total(period.start, period.end)
    payables = m.open_summary("pagar", ref, upcoming_days)
    receivables = m.open_summary("receber", ref, upcoming_days)
    proj = m.projection(horizon, ref)
    balances = m.account_balances(ref)
    balance_now = m.total_balance(ref)

    # Séries para gráficos (sempre calculadas do banco)
    cash_series = m.series_cash(period.start, min(period.end, ref) if period.start <= ref else period.end)
    sales_series = m.series_sales(period.start, period.end)
    months = m.last_months(6, ref)
    by_cat = m.paid_by_category(period.start, period.end)

    charts = {
        "sales": sales_series,
        "cash": cash_series,
        "months": {
            "labels": [x.month.strftime("%m/%y") for x in months],
            "received": [float(x.received) for x in months],
            "paid": [float(x.paid) for x in months],
            "sales": [float(x.sales) for x in months],
        },
        "categories": {"labels": [c["name"] for c in by_cat[:8]], "values": [float(c["total"]) for c in by_cat[:8]]},
        "split": {"labels": ["Despesas operacionais", "Compras de mercadorias"],
                  "values": [float(paid_groups.get("operacional", ZERO)), float(paid_groups.get("mercadoria", ZERO))]},
    }

    has_accounts = db.session.scalar(select(func.count(Account.id))) > 0
    has_movements = bool(db.session.scalar(select(Title.id).limit(1)))
    recent = db.session.scalars(select(Settlement).order_by(Settlement.date.desc(), Settlement.id.desc()).limit(8)).all()

    from ..services import orders as osvc
    order_alerts = osvc.alerts(settings.order_alert_days if settings else 2)

    return render_template(
        "dashboard.html", order_alerts=order_alerts,
        period=period, period_choices=PERIOD_CHOICES, sales=sales, cash=cash, paid_groups=paid_groups,
        purchases=purchases, payables=payables, receivables=receivables, proj=proj, balances=balances,
        balance_now=balance_now, charts=charts, has_accounts=has_accounts,
        has_movements=has_movements, recent=recent, upcoming_days=upcoming_days,
    )
