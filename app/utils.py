"""Utilitários de dinheiro, datas e períodos.

Valores monetários são sempre Decimal com 2 casas, nunca float.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class ValidationError(ValueError):
    """Erro de validação com mensagem amigável para a usuária."""


# ---------------------------------------------------------------- dinheiro

def q(value) -> Decimal:
    """Normaliza para Decimal com 2 casas (arredondamento comercial)."""
    if value is None:
        return ZERO
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


_MONEY_CLEAN = re.compile(r"[^\d,.\-]")


def parse_money(raw, *, field: str = "Valor", required: bool = True, allow_zero: bool = False,
                allow_negative: bool = False) -> Decimal | None:
    """Converte texto digitado no padrão brasileiro em Decimal.

    Aceita "1.234,56", "1234,56", "1234.56", "R$ 1.234,56" e "1.234" (mil e duzentos e trinta e quatro).
    """
    if raw is None or str(raw).strip() == "":
        if required:
            raise ValidationError(f"{field}: informe um valor.")
        return None
    text = _MONEY_CLEAN.sub("", str(raw).strip())
    if text in ("", "-", ",", "."):
        raise ValidationError(f"{field}: valor inválido.")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        if text.count(",") > 1:
            raise ValidationError(f"{field}: valor inválido.")
        text = text.replace(",", ".")
    elif "." in text:
        parts = text.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            # "1.234" ou "1.234.567": pontos são separadores de milhar
            if all(len(p) == 3 for p in parts[1:]):
                text = "".join(parts)
            else:
                raise ValidationError(f"{field}: valor inválido.")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValidationError(f"{field}: valor inválido.") from exc
    if value != value.quantize(CENT) and len(str(value).split(".")[-1]) > 2:
        raise ValidationError(f"{field}: use no máximo 2 casas decimais.")
    value = q(value)
    if value < 0 and not allow_negative:
        raise ValidationError(f"{field}: o valor não pode ser negativo.")
    if value == 0 and not allow_zero:
        raise ValidationError(f"{field}: o valor deve ser maior que zero.")
    if abs(value) >= Decimal("1000000000"):
        raise ValidationError(f"{field}: valor muito alto.")
    return value


def parse_quantity(raw, *, field: str = "Quantidade") -> Decimal:
    if raw is None or str(raw).strip() == "":
        raise ValidationError(f"{field}: informe a quantidade.")
    text = str(raw).strip().replace(".", "").replace(",", ".") if "," in str(raw) else str(raw).strip()
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValidationError(f"{field}: quantidade inválida.") from exc
    if value <= 0:
        raise ValidationError(f"{field}: a quantidade deve ser maior que zero.")
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def brl(value, *, sign: bool = False) -> str:
    """Formata como moeda brasileira: R$ 1.234,56."""
    v = q(value)
    neg = v < 0
    integer, frac = f"{abs(v):.2f}".split(".")
    groups = []
    while len(integer) > 3:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    groups.insert(0, integer)
    body = f"R$ {'.'.join(groups)},{frac}"
    if neg:
        return f"− {body}"
    if sign and v > 0:
        return f"+ {body}"
    return body


def number_br(value, places: int = 2) -> str:
    v = Decimal(str(value))
    text = f"{v:,.{places}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def split_installments(total: Decimal, count: int) -> list[Decimal]:
    """Divide um valor em parcelas que somam exatamente o total.

    A diferença de centavos fica na primeira parcela.
    """
    total = q(total)
    if count < 1:
        raise ValidationError("Número de parcelas inválido.")
    base = (total / count).quantize(CENT, rounding="ROUND_DOWN")
    parts = [base] * count
    parts[0] = q(total - base * (count - 1))
    return parts


# ---------------------------------------------------------------- datas

def today(tz: str = "America/Sao_Paulo") -> date:
    return datetime.now(ZoneInfo(tz)).date()


def parse_date(raw, *, field: str = "Data", required: bool = True) -> date | None:
    if raw is None or str(raw).strip() == "":
        if required:
            raise ValidationError(f"{field}: informe a data.")
        return None
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            d = datetime.strptime(text, fmt).date()
            if not (date(2000, 1, 1) <= d <= date(2100, 12, 31)):
                raise ValidationError(f"{field}: data fora do intervalo permitido.")
            return d
        except ValueError:
            continue
    raise ValidationError(f"{field}: data inválida.")


def fmt_date(d) -> str:
    if not d:
        return "—"
    if isinstance(d, datetime):
        return d.strftime("%d/%m/%Y %H:%M")
    return d.strftime("%d/%m/%Y")


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


MONTHS_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
             "agosto", "setembro", "outubro", "novembro", "dezembro"]
MONTHS_SHORT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def month_label(d: date, short: bool = False) -> str:
    if short:
        return f"{MONTHS_SHORT[d.month - 1]}/{str(d.year)[2:]}"
    return f"{MONTHS_PT[d.month - 1]} de {d.year}"


@dataclass
class Period:
    key: str
    start: date
    end: date
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


PERIOD_CHOICES = [
    ("hoje", "Hoje"),
    ("7d", "Últimos 7 dias"),
    ("mes", "Mês atual"),
    ("mes_anterior", "Mês anterior"),
    ("ano", "Ano atual"),
    ("personalizado", "Período personalizado"),
]


def resolve_period(key: str | None, start_raw=None, end_raw=None, ref: date | None = None) -> Period:
    ref = ref or today()
    key = key or "mes"
    if key == "hoje":
        return Period(key, ref, ref, "Hoje")
    if key == "7d":
        return Period(key, ref - timedelta(days=6), ref, "Últimos 7 dias")
    if key == "mes_anterior":
        prev = month_start(ref) - timedelta(days=1)
        return Period(key, month_start(prev), month_end(prev), month_label(prev).capitalize())
    if key == "ano":
        return Period(key, date(ref.year, 1, 1), date(ref.year, 12, 31), f"Ano de {ref.year}")
    if key == "personalizado":
        try:
            start = parse_date(start_raw, field="Data inicial")
            end = parse_date(end_raw, field="Data final")
        except ValidationError:
            start, end = month_start(ref), month_end(ref)
        if start > end:
            start, end = end, start
        return Period(key, start, end, f"{fmt_date(start)} a {fmt_date(end)}")
    return Period("mes", month_start(ref), month_end(ref), month_label(ref).capitalize())
