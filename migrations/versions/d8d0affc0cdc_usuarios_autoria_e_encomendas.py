"""Usuários, autoria e encomendas

Migração aditiva: só cria tabelas, colunas, índices, uma sequência e gatilhos.
Nenhum dado existente é apagado ou alterado, exceto o preenchimento de autoria
dos registros antigos quando a base tem exatamente um usuário (era o único autor
possível na versão anterior).

Revision ID: d8d0affc0cdc
Revises: 981c9cfaa373
"""
import sqlalchemy as sa
from alembic import op

revision = "d8d0affc0cdc"
down_revision = "981c9cfaa373"
branch_labels = None
depends_on = None

AUTHORED = {  # tabela: (tem created_by, tem canceled_by)
    "sales": (True, True),
    "purchases": (True, True),
    "titles": (True, True),
    "settlements": (True, False),
    "transfers": (True, False),
}


def upgrade():
    # ------------------------------------------------------------ usuários
    op.add_column("users", sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("users", sa.Column("created_by_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_users_created_by", "users", "users", ["created_by_id"], ["id"])

    # ------------------------------------------------------------ auditoria
    op.add_column("audit_log", sa.Column("username", sa.String(length=120), nullable=True))
    op.add_column("audit_log", sa.Column("before", sa.Text(), nullable=True))
    op.add_column("audit_log", sa.Column("after", sa.Text(), nullable=True))
    op.create_index("ix_audit_log_entity", "audit_log", ["entity"])
    op.create_index("ix_audit_log_entity_id", "audit_log", ["entity_id"])

    # ------------------------------------------------------------ autoria nos registros financeiros
    for table, (created, canceled) in AUTHORED.items():
        if created:
            op.add_column(table, sa.Column("created_by_id", sa.Integer(), nullable=True))
            op.create_foreign_key(f"fk_{table}_created_by", table, "users", ["created_by_id"], ["id"])
        if canceled:
            op.add_column(table, sa.Column("canceled_by_id", sa.Integer(), nullable=True))
            op.create_foreign_key(f"fk_{table}_canceled_by", table, "users", ["canceled_by_id"], ["id"])

    op.add_column("company_settings", sa.Column("order_alert_days", sa.Integer(), server_default="2", nullable=False))

    # ------------------------------------------------------------ encomendas
    op.execute("CREATE SEQUENCE IF NOT EXISTS order_number_seq START 1")
    op.create_table('orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('number', sa.Integer(), server_default=sa.text("nextval('order_number_seq')"), nullable=False),
    sa.Column('request_key', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('priority', sa.String(length=10), nullable=False),
    sa.Column('blocked', sa.Boolean(), nullable=False),
    sa.Column('blocked_reason', sa.String(length=300), nullable=True),
    sa.Column('customer_name', sa.String(length=120), nullable=False),
    sa.Column('customer_phone', sa.String(length=40), nullable=True),
    sa.Column('contact_notes', sa.Text(), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('due_time', sa.Time(), nullable=True),
    sa.Column('delivery_mode', sa.String(length=20), nullable=False),
    sa.Column('delivery_address', sa.Text(), nullable=True),
    sa.Column('pickup_location', sa.String(length=200), nullable=True),
    sa.Column('delivery_person', sa.String(length=120), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivery_notes', sa.Text(), nullable=True),
    sa.Column('discount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('payment_method_id', sa.Integer(), nullable=True),
    sa.Column('balance_due_date', sa.Date(), nullable=True),
    sa.Column('production_notes', sa.Text(), nullable=True),
    sa.Column('customer_references', sa.Text(), nullable=True),
    sa.Column('internal_notes', sa.Text(), nullable=True),
    sa.Column('sale_id', sa.Integer(), nullable=True),
    sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.Column('canceled_by_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("delivery_mode in ('retirada','entrega','transportadora')", name='ck_order_delivery_mode'),
    sa.CheckConstraint("priority in ('normal','alta','urgente')", name='ck_order_priority'),
    sa.CheckConstraint('discount >= 0', name='ck_order_discount'),
    sa.ForeignKeyConstraint(['canceled_by_id'], ['users.id'], name='fk_orders_canceled_by'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_orders_created_by'),
    sa.ForeignKeyConstraint(['payment_method_id'], ['payment_methods.id'], ),
    sa.ForeignKeyConstraint(['sale_id'], ['sales.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number'),
    sa.UniqueConstraint('request_key'),
    sa.UniqueConstraint('sale_id')
    )
    op.create_index('ix_orders_created_at', 'orders', ['created_at'])
    op.create_index('ix_orders_due_date', 'orders', ['due_date'])
    op.create_index('ix_orders_status', 'orders', ['status'])

    op.create_table('order_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('username', sa.String(length=120), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('from_status', sa.String(length=20), nullable=True),
    sa.Column('to_status', sa.String(length=20), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('before', sa.Text(), nullable=True),
    sa.Column('after', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_order_events_order_id', 'order_events', ['order_id'])

    op.create_table('order_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=True),
    sa.Column('description', sa.String(length=160), nullable=False),
    sa.Column('customization', sa.Text(), nullable=True),
    sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('discount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.CheckConstraint('discount >= 0', name='ck_order_item_discount'),
    sa.CheckConstraint('quantity > 0', name='ck_order_item_qty'),
    sa.CheckConstraint('unit_price >= 0', name='ck_order_item_price'),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_order_items_order_id', 'order_items', ['order_id'])

    # ------------------------------------------------------------ dados antigos
    # Antes desta versão só existia um usuário; ele é o autor de tudo o que já foi lançado.
    single = "(SELECT CASE WHEN count(*) = 1 THEN min(id) END FROM users)"
    for table, (created, canceled) in AUTHORED.items():
        op.execute(f"UPDATE {table} SET created_by_id = {single} WHERE created_by_id IS NULL")
        if canceled:
            op.execute(f"UPDATE {table} SET canceled_by_id = {single} WHERE canceled_by_id IS NULL AND canceled_at IS NOT NULL")
    op.execute(f"UPDATE audit_log a SET user_id = {single} WHERE a.user_id IS NULL")
    op.execute("UPDATE audit_log a SET username = COALESCE(u.display_name, u.username) FROM users u "
               "WHERE a.user_id = u.id AND a.username IS NULL")

    # ------------------------------------------------------------ histórico protegido
    from app.services.audit import GUARD_SQL
    for stmt in GUARD_SQL:
        op.execute(stmt)


def downgrade():
    from app.services.audit import DROP_GUARD_SQL
    for stmt in DROP_GUARD_SQL:
        op.execute(stmt)
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")
    op.drop_index("ix_order_events_order_id", table_name="order_events")
    op.drop_table("order_events")
    for ix in ("ix_orders_status", "ix_orders_due_date", "ix_orders_created_at"):
        op.drop_index(ix, table_name="orders")
    op.drop_table("orders")
    op.execute("DROP SEQUENCE IF EXISTS order_number_seq")
    op.drop_column("company_settings", "order_alert_days")
    for table, (created, canceled) in AUTHORED.items():
        if canceled:
            op.drop_constraint(f"fk_{table}_canceled_by", table, type_="foreignkey")
            op.drop_column(table, "canceled_by_id")
        if created:
            op.drop_constraint(f"fk_{table}_created_by", table, type_="foreignkey")
            op.drop_column(table, "created_by_id")
    op.drop_index("ix_audit_log_entity_id", table_name="audit_log")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    for col in ("after", "before", "username"):
        op.drop_column("audit_log", col)
    op.drop_constraint("fk_users_created_by", "users", type_="foreignkey")
    for col in ("deactivated_at", "created_by_id", "active"):
        op.drop_column("users", col)
