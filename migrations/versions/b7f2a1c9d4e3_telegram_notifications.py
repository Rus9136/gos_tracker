"""telegram notifications: user chat_id + match notified_at

Revision ID: b7f2a1c9d4e3
Revises: 67d99253ab8f
Create Date: 2026-06-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f2a1c9d4e3'
down_revision: Union[str, Sequence[str], None] = '67d99253ab8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('telegram_chat_id', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column(
            'notify_telegram', sa.Boolean(), nullable=False, server_default='0'
        ))

    with op.batch_alter_table('user_lot_matches', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('user_lot_matches', schema=None) as batch_op:
        batch_op.drop_column('notified_at')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('notify_telegram')
        batch_op.drop_column('telegram_chat_id')
