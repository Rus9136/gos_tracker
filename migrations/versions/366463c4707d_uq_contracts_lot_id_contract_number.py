"""uq contracts (lot_id, contract_number)

Идемпотентность двух писателей договоров (HTML detail-фаза и contracts-sync
из API OWS) на уровне БД. Дублей в prod на момент миграции — 0 (проверено).

trgm-индексы (ix_lots_*_trgm, ix_organizations_name_trgm), которые
autogenerate предлагал удалить, созданы вне моделей намеренно — не трогаем.

Revision ID: 366463c4707d
Revises: 635bb7f06534
Create Date: 2026-07-27 07:07:21.975910

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '366463c4707d'
down_revision: Union[str, Sequence[str], None] = '635bb7f06534'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('contracts', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_contract_lot_number', ['lot_id', 'contract_number']
        )


def downgrade() -> None:
    with op.batch_alter_table('contracts', schema=None) as batch_op:
        batch_op.drop_constraint('uq_contract_lot_number', type_='unique')
