"""Modelo de dados.

Conceitos centrais
------------------
* Title (título): um compromisso financeiro — algo a receber ou a pagar.
  Vendas, outras receitas, despesas e compras de mercadoria geram títulos.
  Um título NÃO movimenta o caixa.
* Settlement (liquidação): o dinheiro que efetivamente entrou ou saiu de uma
  conta financeira para quitar (total ou parcialmente) um título. É a única
  coisa que altera o saldo, junto com transferências.
* Estornos são lançamentos inversos que apontam para o lançamento original
  (reverses_id único), nunca exclusões. Assim o histórico é preservado e não
  há como estornar duas vezes.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Sequence,
    String,
    Text,
    Time,
    UniqueConstraint,
    case,
    func,
    select,
)
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .utils import ZERO, q

MONEY = Numeric(14, 2)
QTY = Numeric(12, 3)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Usuário e segurança
# --------------------------------------------------------------------------- #
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def label(self) -> str:
        return self.display_name or self.username

    @property
    def is_active(self) -> bool:  # Flask-Login: usuário desativado não autentica
        return bool(self.active)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="scrypt")
        self.password_changed_at = utcnow()
        # Invalida sessões abertas em outros dispositivos.
        self.session_version = (self.session_version or 0) + 1

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def get_id(self) -> str:
        return f"{self.id}:{self.session_version}"


class LoginAttempt(db.Model):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str | None] = mapped_column(String(80))
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Nome do autor gravado no próprio registro: o histórico continua legível
    # mesmo que o usuário seja desativado ou renomeado.
    username: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[str | None] = mapped_column(Text)  # JSON
    after: Mapped[str | None] = mapped_column(Text)  # JSON

    user = relationship("User")

    @property
    def author(self) -> str:
        return self.username or (self.user.label if self.user else "Sistema")

    def changes(self) -> list[tuple[str, str, str]]:
        import json
        try:
            b = json.loads(self.before) if self.before else {}
            a = json.loads(self.after) if self.after else {}
        except ValueError:
            return []
        keys = list(dict.fromkeys(list(b) + list(a)))
        return [(k, str(b.get(k, "—")), str(a.get(k, "—"))) for k in keys if b.get(k) != a.get(k)]


# --------------------------------------------------------------------------- #
# Configurações e cadastros
# --------------------------------------------------------------------------- #
class CompanySettings(db.Model):
    """Linha única (id=1) com identidade e preferências."""

    __tablename__ = "company_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(120), default="Doce Laços")
    tagline: Mapped[str | None] = mapped_column(String(160), default="Gestão financeira")
    color_primary: Mapped[str] = mapped_column(String(7), default="#8E4A67")
    color_accent: Mapped[str] = mapped_column(String(7), default="#B8915A")
    color_ink: Mapped[str] = mapped_column(String(7), default="#2E2433")
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    logo_mime: Mapped[str | None] = mapped_column(String(40))
    logo_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projection_days: Mapped[int] = mapped_column(Integer, default=30)
    upcoming_days: Mapped[int] = mapped_column(Integer, default=7)
    order_alert_days: Mapped[int] = mapped_column(Integer, default=2, server_default="2", nullable=False)
    default_period: Mapped[str] = mapped_column(String(20), default="mes_atual")
    last_backup_download_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


ACCOUNT_KINDS = {"banco": "Conta bancária", "caixa": "Dinheiro em caixa", "carteira": "Carteira digital", "outra": "Outra"}


class Account(db.Model):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="banco", nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(MONEY, default=ZERO, nullable=False)
    opening_date: Mapped[date] = mapped_column(Date, nullable=False)
    include_in_total: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def kind_label(self) -> str:
        return ACCOUNT_KINDS.get(self.kind, self.kind)


CATEGORY_GROUPS = {
    "venda": ("receita", "Vendas"),
    "outra_receita": ("receita", "Outras receitas"),
    "operacional": ("despesa", "Despesa operacional"),
    "mercadoria": ("despesa", "Compra de mercadorias"),
}


class Category(db.Model):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("name", "kind", name="uq_category_name_kind"),
        CheckConstraint("kind in ('receita','despesa')", name="ck_category_kind"),
        CheckConstraint("grp in ('venda','outra_receita','operacional','mercadoria')", name="ck_category_group"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    group: Mapped[str] = mapped_column("grp", String(20), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    @property
    def group_label(self) -> str:
        return CATEGORY_GROUPS.get(self.group, ("", self.group))[1]


class PaymentMethod(db.Model):
    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Product(db.Model):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str | None] = mapped_column(String(40), unique=True)
    category: Mapped[str | None] = mapped_column(String(80))
    supplier: Mapped[str | None] = mapped_column(String(120))
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, default=ZERO, nullable=False)
    sale_price: Mapped[Decimal | None] = mapped_column(MONEY)
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (CheckConstraint("unit_cost >= 0", name="ck_product_cost"),)


# --------------------------------------------------------------------------- #
# Documentos de origem: vendas e compras
# --------------------------------------------------------------------------- #
class Sale(db.Model):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("discount >= 0", name="ck_sale_discount"),
        CheckConstraint("net_amount > 0", name="ck_sale_net_positive"),
        CheckConstraint("net_amount = gross_amount - discount", name="ck_sale_net"),
        CheckConstraint("status in ('ativa','cancelada')", name="ck_sale_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True, default=new_uid)
    request_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(160), nullable=False)
    sale_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    customer: Mapped[str | None] = mapped_column(String(120))
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount: Mapped[Decimal] = mapped_column(MONEY, default=ZERO, nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    payment_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="ativa", nullable=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    canceled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    created_by = relationship("User", foreign_keys="Sale.created_by_id")
    canceled_by = relationship("User", foreign_keys="Sale.canceled_by_id")
    order = relationship("Order", back_populates="sale", uselist=False)
    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan", order_by="SaleItem.id")
    titles = relationship("Title", back_populates="sale", order_by="Title.installment_no")
    payment_method = relationship("PaymentMethod")

    @property
    def received(self) -> Decimal:
        return q(sum((t.settled_amount for t in self.titles), ZERO))

    @property
    def to_receive(self) -> Decimal:
        return q(sum((t.open_amount for t in self.titles), ZERO))

    @property
    def receipt_status(self) -> str:
        if self.status == "cancelada":
            return "cancelada"
        if self.to_receive == 0:
            return "recebida"
        if self.received > 0:
            return "parcial"
        return "a_receber"


class SaleItem(db.Model):
    __tablename__ = "sale_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_sale_item_qty"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    description: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    sale = relationship("Sale", back_populates="items")
    product = relationship("Product")


class Purchase(db.Model):
    """Compra de mercadorias para revenda. Gera títulos a pagar; não é saída de caixa por si só."""

    __tablename__ = "purchases"
    __table_args__ = (
        CheckConstraint("total > 0", name="ck_purchase_total"),
        CheckConstraint("status in ('ativa','cancelada')", name="ck_purchase_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True, default=new_uid)
    request_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    supplier: Mapped[str | None] = mapped_column(String(120))
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="ativa", nullable=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    canceled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    created_by = relationship("User", foreign_keys="Purchase.created_by_id")
    canceled_by = relationship("User", foreign_keys="Purchase.canceled_by_id")
    items = relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan", order_by="PurchaseItem.id")
    titles = relationship("Title", back_populates="purchase", order_by="Title.installment_no")

    @property
    def paid(self) -> Decimal:
        return q(sum((t.settled_amount for t in self.titles), ZERO))

    @property
    def to_pay(self) -> Decimal:
        return q(sum((t.open_amount for t in self.titles), ZERO))

    @property
    def payment_status(self) -> str:
        if self.status == "cancelada":
            return "cancelada"
        if self.to_pay == 0:
            return "paga"
        if self.paid > 0:
            return "parcial"
        return "a_pagar"


class PurchaseItem(db.Model):
    __tablename__ = "purchase_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_purchase_item_qty"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    description: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    purchase = relationship("Purchase", back_populates="items")
    product = relationship("Product")


# --------------------------------------------------------------------------- #
# Títulos e liquidações
# --------------------------------------------------------------------------- #
TITLE_ORIGINS = {"venda": "Venda", "receita": "Outra receita", "despesa": "Despesa", "compra": "Compra de mercadorias"}


class Settlement(db.Model):
    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_settlement_amount"),
        CheckConstraint("(is_reversal = false AND reverses_id IS NULL) OR (is_reversal = true AND reverses_id IS NOT NULL)",
                        name="ck_settlement_reversal_link"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True, default=new_uid)
    request_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    payment_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"))
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    is_reversal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # unique=True: um lançamento só pode ser estornado uma vez.
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("settlements.id"), unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    created_by = relationship("User", foreign_keys=[created_by_id])
    title = relationship("Title", back_populates="settlements")
    account = relationship("Account")
    payment_method = relationship("PaymentMethod")
    reverses = relationship("Settlement", remote_side="Settlement.id", foreign_keys=[reverses_id],
                            back_populates="reversal")
    reversal = relationship("Settlement", foreign_keys=[reverses_id], back_populates="reverses", uselist=False)

    @property
    def signed_amount(self) -> Decimal:
        """Efeito sobre o título: positivo liquida, estorno desfaz."""
        return -self.amount if self.is_reversal else self.amount

    @property
    def cash_effect(self) -> Decimal:
        """Efeito sobre o caixa: + entrada, − saída."""
        sign = 1 if self.title.kind == "receber" else -1
        return self.signed_amount * sign



class Title(db.Model):
    __tablename__ = "titles"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_title_amount"),
        CheckConstraint("kind in ('receber','pagar')", name="ck_title_kind"),
        CheckConstraint("origin in ('venda','receita','despesa','compra')", name="ck_title_origin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True, default=new_uid)
    request_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str] = mapped_column(String(160), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False, index=True)
    counterparty: Mapped[str | None] = mapped_column(String(120))
    competence_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    expected_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"))
    expected_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"), index=True)
    purchase_id: Mapped[int | None] = mapped_column(ForeignKey("purchases.id"), index=True)
    installment_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    installment_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    recurrence_group: Mapped[str | None] = mapped_column(String(36), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    canceled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    category = relationship("Category")
    created_by = relationship("User", foreign_keys="Title.created_by_id")
    canceled_by = relationship("User", foreign_keys="Title.canceled_by_id")
    expected_method = relationship("PaymentMethod")
    expected_account = relationship("Account")
    sale = relationship("Sale", back_populates="titles")
    purchase = relationship("Purchase", back_populates="titles")
    settlements = relationship("Settlement", back_populates="title", order_by="Settlement.id")

    @property
    def is_canceled(self) -> bool:
        return self.canceled_at is not None

    @property
    def open_amount(self) -> Decimal:
        if self.is_canceled:
            return ZERO
        return q(self.amount - self.settled_amount)

    def status_on(self, today: date) -> str:
        if self.is_canceled:
            return "cancelado"
        if self.open_amount <= 0:
            return "liquidado"
        if self.due_date < today:
            return "vencido"
        if self.settled_amount > 0:
            return "parcial"
        return "pendente"

    @property
    def origin_label(self) -> str:
        return TITLE_ORIGINS.get(self.origin, self.origin)

    @property
    def installment_label(self) -> str:
        return f"{self.installment_no}/{self.installment_count}" if self.installment_count > 1 else ""


# Valor líquido já liquidado do título (liquidações − estornos), calculado no banco.
Title.settled_amount = column_property(
    select(
        func.coalesce(
            func.sum(case((Settlement.is_reversal.is_(True), -Settlement.amount), else_=Settlement.amount)),
            0,
        )
    )
    .where(Settlement.title_id == Title.id)
    .correlate_except(Settlement)
    .scalar_subquery(),
    deferred=False,
)


class Transfer(db.Model):
    """Transferência entre contas próprias: não é receita nem despesa."""

    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transfer_amount"),
        CheckConstraint("from_account_id <> to_account_id", name="ck_transfer_accounts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True, default=new_uid)
    request_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    from_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    to_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("transfers.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    created_by = relationship("User", foreign_keys=[created_by_id])

    from_account = relationship("Account", foreign_keys=[from_account_id])
    to_account = relationship("Account", foreign_keys=[to_account_id])
    reverses = relationship("Transfer", remote_side="Transfer.id", foreign_keys=[reverses_id], back_populates="reversal")
    reversal = relationship("Transfer", foreign_keys=[reverses_id], back_populates="reverses", uselist=False)



# --------------------------------------------------------------------------- #
# Encomendas
# --------------------------------------------------------------------------- #
ORDER_STATUSES = [
    # (chave, rótulo, aberta?)  — para adaptar as etapas, edite esta lista e ORDER_TRANSITIONS.
    ("novo", "Novo pedido", True),
    ("aguardando", "Aguardando confirmação", True),
    ("confirmado", "Confirmado", True),
    ("producao", "Em produção", True),
    ("pronto", "Pronto para entrega", True),
    ("saiu", "Saiu para entrega", True),
    ("entregue", "Entregue", False),
    ("cancelado", "Cancelado", False),
]
ORDER_STATUS_LABELS = {k: label for k, label, _ in ORDER_STATUSES}
ORDER_OPEN_STATUSES = tuple(k for k, _, is_open in ORDER_STATUSES if is_open)
ORDER_TRANSITIONS = {
    "novo": ("aguardando", "confirmado", "cancelado"),
    "aguardando": ("novo", "confirmado", "cancelado"),
    "confirmado": ("aguardando", "producao", "pronto", "cancelado"),
    "producao": ("confirmado", "pronto", "cancelado"),
    "pronto": ("producao", "saiu", "entregue", "cancelado"),
    "saiu": ("pronto", "entregue", "cancelado"),
    "entregue": (),
    "cancelado": (),
}
ORDER_CHANNELS = {"whatsapp": "WhatsApp", "instagram": "Instagram", "loja": "Loja física", "indicacao": "Indicação",
                  "site": "Site", "outro": "Outro"}
ORDER_PRIORITIES = {"normal": "Normal", "alta": "Alta", "urgente": "Urgente"}
PRIORITY_RANK = {"urgente": 0, "alta": 1, "normal": 2}
DELIVERY_MODES = {"retirada": "Retirada", "entrega": "Entrega própria", "transportadora": "Transportadora"}

order_number_seq = Sequence("order_number_seq", start=1)


class Order(db.Model):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("discount >= 0", name="ck_order_discount"),
        CheckConstraint("priority in ('normal','alta','urgente')", name="ck_order_priority"),
        CheckConstraint("delivery_mode in ('retirada','entrega','transportadora')", name="ck_order_delivery_mode"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(Integer, order_number_seq, server_default=order_number_seq.next_value(),
                                        unique=True, nullable=False)
    request_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="novo", nullable=False, index=True)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    priority: Mapped[str] = mapped_column(String(10), default="normal", nullable=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(String(300))

    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String(40))
    contact_notes: Mapped[str | None] = mapped_column(Text)

    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    due_time: Mapped[time | None] = mapped_column(Time)
    delivery_mode: Mapped[str] = mapped_column(String(20), default="retirada", nullable=False)
    delivery_address: Mapped[str | None] = mapped_column(Text)
    pickup_location: Mapped[str | None] = mapped_column(String(200))
    delivery_person: Mapped[str | None] = mapped_column(String(120))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_notes: Mapped[str | None] = mapped_column(Text)

    discount: Mapped[Decimal] = mapped_column(MONEY, default=ZERO, nullable=False)
    payment_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"))
    balance_due_date: Mapped[date | None] = mapped_column(Date)

    production_notes: Mapped[str | None] = mapped_column(Text)
    customer_references: Mapped[str | None] = mapped_column(Text)
    internal_notes: Mapped[str | None] = mapped_column(Text)

    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"), unique=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    canceled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id")
    events = relationship("OrderEvent", back_populates="order", order_by="OrderEvent.id")
    sale = relationship("Sale", back_populates="order")
    payment_method = relationship("PaymentMethod")
    created_by = relationship("User", foreign_keys=[created_by_id])
    canceled_by = relationship("User", foreign_keys=[canceled_by_id])

    # ------------------------------------------------------------------ valores
    @property
    def code(self) -> str:
        return f"DL-{self.number:04d}"

    @property
    def gross(self) -> Decimal:
        return q(sum((i.gross for i in self.items), ZERO))

    @property
    def items_discount(self) -> Decimal:
        return q(sum((i.discount for i in self.items), ZERO))

    @property
    def total_discount(self) -> Decimal:
        return q(self.items_discount + (self.discount or ZERO))

    @property
    def total(self) -> Decimal:
        return q(self.gross - self.total_discount)

    @property
    def received(self) -> Decimal:
        """Recebido líquido (liquidações − estornos) da venda vinculada."""
        if not self.sale:
            return ZERO
        return q(sum((t.settled_amount for t in self.sale.titles), ZERO))

    @property
    def pending(self) -> Decimal:
        if self.status == "cancelado":
            return ZERO
        if self.sale and self.sale.status == "ativa":
            return q(sum((t.open_amount for t in self.sale.titles), ZERO))
        return q(self.total - self.received)

    @property
    def payment_status(self) -> str:
        if self.status == "cancelado":
            return "cancelado"
        if self.total > 0 and self.pending <= 0:
            return "pago"
        if self.received > 0:
            return "parcial"
        return "a_receber"

    # ------------------------------------------------------------------ prazos
    @property
    def is_open(self) -> bool:
        return self.status in ORDER_OPEN_STATUSES

    def due_at(self, tz) -> datetime:
        t = self.due_time or time(23, 59, 59)
        return datetime.combine(self.due_date, t, tzinfo=tz)

    def is_late(self, now: datetime) -> bool:
        return self.is_open and now > self.due_at(now.tzinfo)

    def days_left(self, today_: date) -> int:
        return (self.due_date - today_).days

    @property
    def status_label(self) -> str:
        return ORDER_STATUS_LABELS.get(self.status, self.status)

    @property
    def channel_label(self) -> str:
        return ORDER_CHANNELS.get(self.channel, self.channel)

    @property
    def priority_label(self) -> str:
        return ORDER_PRIORITIES.get(self.priority, self.priority)

    @property
    def delivery_label(self) -> str:
        return DELIVERY_MODES.get(self.delivery_mode, self.delivery_mode)

    @property
    def allowed_next(self) -> tuple[str, ...]:
        nxt = ORDER_TRANSITIONS.get(self.status, ())
        if self.delivery_mode == "retirada":
            nxt = tuple(s for s in nxt if s != "saiu")
        return nxt


class OrderItem(db.Model):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_item_qty"),
        CheckConstraint("unit_price >= 0", name="ck_order_item_price"),
        CheckConstraint("discount >= 0", name="ck_order_item_discount"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    description: Mapped[str] = mapped_column(String(160), nullable=False)
    customization: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount: Mapped[Decimal] = mapped_column(MONEY, default=ZERO, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    order = relationship("Order", back_populates="items")
    product = relationship("Product")

    @property
    def gross(self) -> Decimal:
        return q(self.quantity * self.unit_price)

    @property
    def subtotal(self) -> Decimal:
        return q(self.gross - (self.discount or ZERO))


class OrderEvent(db.Model):
    """Histórico da encomenda (somente inclusão)."""

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    username: Mapped[str | None] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    summary: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[str | None] = mapped_column(Text)
    after: Mapped[str | None] = mapped_column(Text)

    order = relationship("Order", back_populates="events")

    @property
    def author(self) -> str:
        return self.username or "Sistema"

    changes = AuditLog.changes
