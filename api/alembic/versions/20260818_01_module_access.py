"""add functional module access flags to users

Revision ID: 20260818_01
Revises:
Create Date: 2026-08-18
"""
from typing import Optional

from alembic import op
import sqlalchemy as sa

revision: str = "20260818_01"
down_revision: Optional[str] = None
branch_labels = None
depends_on = None


def _column_map():
    return {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }


def upgrade() -> None:
    existing_columns = _column_map()
    if "can_access_control" not in existing_columns:
        op.add_column(
            "users",
            sa.Column(
                "can_access_control",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "can_access_monitoring" not in existing_columns:
        op.add_column(
            "users",
            sa.Column(
                "can_access_monitoring",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )
    op.execute(
        sa.text(
            "UPDATE users "
            "SET can_access_control = TRUE, can_access_monitoring = TRUE "
            "WHERE role IN ('admin', 'superadmin')"
        )
    )


def downgrade() -> None:
    columns = _column_map()
    for column_name in ("can_access_monitoring", "can_access_control"):
        if column_name in columns:
            op.drop_column("users", column_name)
