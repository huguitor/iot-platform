"""Add nullable device firmware metadata, accepting compatible existing columns.

Revision ID: 20261104_01
Revises: 20260818_01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261104_01"
down_revision = "20260818_01"
branch_labels = None
depends_on = None


def _columns():
    return (
        sa.Column("fw_version", sa.String(50), nullable=True),
        sa.Column("gateway_fw_version", sa.String(50), nullable=True),
        sa.Column("card_capacity", sa.Integer(), nullable=True),
        sa.Column("info", postgresql.JSONB(), nullable=True),
        sa.Column("info_at", sa.DateTime(timezone=True), nullable=True),
    )


def _compatible(actual, expected):
    typ = actual["type"]
    wanted = expected.type
    if isinstance(wanted, sa.String):
        same_type = isinstance(typ, sa.VARCHAR) and typ.length == 50
    elif isinstance(wanted, sa.Integer):
        same_type = isinstance(typ, postgresql.INTEGER)
    elif isinstance(wanted, postgresql.JSONB):
        same_type = isinstance(typ, postgresql.JSONB)
    else:
        same_type = isinstance(typ, postgresql.TIMESTAMP) and typ.timezone
    return same_type and actual["nullable"] and actual["default"] is None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        raise RuntimeError("Firmware migration requires an online PostgreSQL connection")
    # Bound lock waits and execution time; settings expire with this transaction.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    existing = {
        column["name"]: column
        for column in sa.inspect(connection).get_columns("devices", schema="public")
    }
    columns = _columns()
    # Validate everything before issuing any ALTER TABLE.
    for column in columns:
        if column.name in existing and not _compatible(existing[column.name], column):
            raise RuntimeError(
                f"Incompatible public.devices.{column.name}; expected the firmware "
                "type, nullable and without DEFAULT. No automatic conversion performed."
            )
    for column in columns:
        if column.name not in existing:
            op.add_column("devices", column, schema="public")


def downgrade():
    raise RuntimeError(
        "Automatic firmware downgrade is disabled: columns may predate this "
        "revision or contain data. Use a separately reviewed recovery procedure."
    )
