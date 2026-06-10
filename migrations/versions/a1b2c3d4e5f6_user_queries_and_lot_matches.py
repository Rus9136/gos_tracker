"""user_queries + user_lot_matches

Семантическая память пользователя: UserQuery (NL-предпочтение «какие лоты хочу»)
и UserLotMatch (кеш матча query×lot, считается LLM один раз на пару).

Revision ID: a1b2c3d4e5f6
Revises: 18c81fc2f4ab
Create Date: 2026-06-10 05:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '18c81fc2f4ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSON, который на Postgres становится jsonb (как JSON_TYPE в models.py).
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("compiled_filters", _JSON, nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("user_queries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_queries_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_user_queries_active"), ["active"], unique=False
        )

    op.create_table(
        "user_lot_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_query_id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.BigInteger(), nullable=False),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("matched_at", _TS, nullable=False),
        sa.Column("matcher_version", sa.String(length=40), nullable=False),
        sa.Column("query_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_query_id"], ["user_queries.id"]),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_query_id", "lot_id", name="uq_match_query_lot"),
    )
    with op.batch_alter_table("user_lot_matches", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_lot_matches_user_query_id"),
            ["user_query_id"], unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_user_lot_matches_lot_id"), ["lot_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_user_lot_matches_matched"), ["matched"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_user_lot_matches_score"), ["score"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("user_lot_matches", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_lot_matches_score"))
        batch_op.drop_index(batch_op.f("ix_user_lot_matches_matched"))
        batch_op.drop_index(batch_op.f("ix_user_lot_matches_lot_id"))
        batch_op.drop_index(batch_op.f("ix_user_lot_matches_user_query_id"))
    op.drop_table("user_lot_matches")

    with op.batch_alter_table("user_queries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_queries_active"))
        batch_op.drop_index(batch_op.f("ix_user_queries_user_id"))
    op.drop_table("user_queries")
