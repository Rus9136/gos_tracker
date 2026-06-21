"""autosubmit: client_credentials + submissions

Revision ID: c3a7e1f09b21
Revises: b7f2a1c9d4e3
Create Date: 2026-06-21

Таблицы автоподачи заявок (TENDER_AUTOSUBMIT_PLAN.md): зашифрованные учётки
клиентов (KeyVault, §8) и запланированные/исполненные подачи со статус-машиной.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c3a7e1f09b21'
down_revision: Union[str, Sequence[str], None] = 'b7f2a1c9d4e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSON с jsonb-вариантом на Postgres — как JSON_TYPE в db/models.py.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'client_credentials',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('iin_bin', sa.String(length=12), nullable=True),
        sa.Column('p12_enc', sa.Text(), nullable=False),
        sa.Column('p12_nonce', sa.String(length=32), nullable=False),
        sa.Column('portal_password_enc', sa.Text(), nullable=False),
        sa.Column('portal_password_nonce', sa.String(length=32), nullable=False),
        sa.Column('key_pin_enc', sa.Text(), nullable=True),
        sa.Column('key_pin_nonce', sa.String(length=32), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', _TS, nullable=True),
        sa.Column('updated_at', _TS, nullable=True),
    )
    op.create_index('ix_client_credentials_iin_bin', 'client_credentials', ['iin_bin'])
    op.create_index('ix_client_credentials_active', 'client_credentials', ['active'])

    op.create_table(
        'submissions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('credential_id', sa.Integer(), nullable=False),
        sa.Column('anno_id', sa.BigInteger(), nullable=False),
        sa.Column('anno_number', sa.String(length=40), nullable=True),
        sa.Column('lot_ids', _JSON, nullable=True),
        sa.Column('bid_enc', sa.Text(), nullable=False),
        sa.Column('bid_nonce', sa.String(length=32), nullable=False),
        sa.Column('open_at', _TS, nullable=False),
        sa.Column('close_at', _TS, nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PLANNED'),
        sa.Column('agent_node', sa.String(length=80), nullable=True),
        sa.Column('app_id', sa.BigInteger(), nullable=True),
        sa.Column('armed_at', _TS, nullable=True),
        sa.Column('fired_at', _TS, nullable=True),
        sa.Column('submitted_at', _TS, nullable=True),
        sa.Column('confirmed_at', _TS, nullable=True),
        sa.Column('fire_latency_ms', sa.Integer(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('result', _JSON, nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', _TS, nullable=True),
        sa.Column('updated_at', _TS, nullable=True),
        sa.ForeignKeyConstraint(['credential_id'], ['client_credentials.id']),
    )
    op.create_index('ix_submissions_credential_id', 'submissions', ['credential_id'])
    op.create_index('ix_submissions_anno_id', 'submissions', ['anno_id'])
    op.create_index('ix_submissions_open_at', 'submissions', ['open_at'])
    op.create_index('ix_submissions_status', 'submissions', ['status'])


def downgrade() -> None:
    op.drop_table('submissions')
    op.drop_table('client_credentials')
