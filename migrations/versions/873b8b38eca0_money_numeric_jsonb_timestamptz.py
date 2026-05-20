"""money_numeric_jsonb_timestamptz

Revision ID: 873b8b38eca0
Revises: 792771485417
Create Date: 2026-05-20 07:12:37.653875

Меняем типы под Postgres-семантику (тип-сохраняется и на SQLite):
- DOUBLE PRECISION → NUMERIC(18, 2) для денежных колонок.
- JSON → JSONB на Postgres (JSON остаётся text-сериализованным на SQLite).
- TIMESTAMP → TIMESTAMPTZ на Postgres (хранение в UTC + offset).

Для Postgres-ALTER нужны USING-клаузы, иначе с непустыми колонками будет
"column cannot be cast automatically to type ...":
- TIMESTAMP→TIMESTAMPTZ: интерпретируем существующие naive-значения как UTC.
- JSON→JSONB: явный cast `::jsonb` на тексте.
- DOUBLE PRECISION→NUMERIC: PG умеет неявно, USING не нужен.

`render_as_batch=True` (env.py) для SQLite превращает alter_column в
recreate-and-copy, который копирует значения как есть — `postgresql_using`
там игнорируется. На Postgres это атомарный ALTER COLUMN TYPE.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '873b8b38eca0'
down_revision: Union[str, Sequence[str], None] = '792771485417'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Переиспользуемые типы — чтобы не плодить копии в каждом alter_column.
_MONEY = sa.Numeric(precision=18, scale=2)
_JSON_VARIANT = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), 'postgresql'
)
_TZ = sa.DateTime(timezone=True)


def _ts_using(col: str) -> str:
    # Интерпретируем naive-значения в столбце как UTC. Если на проде сервер
    # систем-tz отличался от UTC и в БД лежат «локальные» naive-метки, эту
    # строку нужно поправить под этот tz. У нас все datetime.utcnow() →
    # naive-UTC, поэтому UTC — правильно.
    return f"{col} AT TIME ZONE 'UTC'"


def _json_using(col: str) -> str:
    return f"{col}::jsonb"


def upgrade() -> None:
    with op.batch_alter_table('announcements') as batch_op:
        batch_op.alter_column('total_amount', existing_type=sa.Float(), type_=_MONEY, existing_nullable=True)
        batch_op.alter_column('publish_date', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=True, postgresql_using=_ts_using('publish_date'))
        batch_op.alter_column('raw', existing_type=sa.JSON(), type_=_JSON_VARIANT, existing_nullable=True, postgresql_using=_json_using('raw'))
        batch_op.alter_column('first_seen', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('first_seen'))
        batch_op.alter_column('last_synced', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('last_synced'))

    with op.batch_alter_table('contracts') as batch_op:
        batch_op.alter_column('plan_amount', existing_type=sa.Float(), type_=_MONEY, existing_nullable=True)
        batch_op.alter_column('contract_amount', existing_type=sa.Float(), type_=_MONEY, existing_nullable=True)
        batch_op.alter_column('fact_amount', existing_type=sa.Float(), type_=_MONEY, existing_nullable=True)
        batch_op.alter_column('last_synced', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('last_synced'))

    with op.batch_alter_table('documents') as batch_op:
        batch_op.alter_column('downloaded_at', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=True, postgresql_using=_ts_using('downloaded_at'))

    with op.batch_alter_table('lot_analyses') as batch_op:
        batch_op.alter_column('tech_stack', existing_type=sa.JSON(), type_=_JSON_VARIANT, existing_nullable=True, postgresql_using=_json_using('tech_stack'))
        batch_op.alter_column('analyzed_at', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('analyzed_at'))

    with op.batch_alter_table('lot_status_history') as batch_op:
        batch_op.alter_column('observed_at', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('observed_at'))

    with op.batch_alter_table('lots') as batch_op:
        batch_op.alter_column('price_per_unit', existing_type=sa.Float(), type_=_MONEY, existing_nullable=True)
        batch_op.alter_column('plan_amount', existing_type=sa.Float(), type_=_MONEY, existing_nullable=True)
        batch_op.alter_column('amount_y1', existing_type=sa.Float(), type_=_MONEY, existing_nullable=True)
        batch_op.alter_column('amount_y2', existing_type=sa.Float(), type_=_MONEY, existing_nullable=True)
        batch_op.alter_column('amount_y3', existing_type=sa.Float(), type_=_MONEY, existing_nullable=True)
        batch_op.alter_column('first_seen', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('first_seen'))
        batch_op.alter_column('last_synced', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('last_synced'))

    with op.batch_alter_table('organizations') as batch_op:
        batch_op.alter_column('first_seen', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('first_seen'))
        batch_op.alter_column('last_seen', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('last_seen'))

    with op.batch_alter_table('presets') as batch_op:
        batch_op.alter_column('status_codes', existing_type=sa.JSON(), type_=_JSON_VARIANT, existing_nullable=True, postgresql_using=_json_using('status_codes'))
        batch_op.alter_column('it_categories', existing_type=sa.JSON(), type_=_JSON_VARIANT, existing_nullable=True, postgresql_using=_json_using('it_categories'))
        batch_op.alter_column('created_at', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('created_at'))
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('updated_at'))

    with op.batch_alter_table('scrape_runs') as batch_op:
        batch_op.alter_column('started_at', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=False, postgresql_using=_ts_using('started_at'))
        batch_op.alter_column('finished_at', existing_type=sa.DateTime(), type_=_TZ, existing_nullable=True, postgresql_using=_ts_using('finished_at'))


def downgrade() -> None:
    # Downgrade — на всякий случай, но смысла особого нет: NUMERIC обратно в
    # FLOAT теряет точность, JSONB → JSON теряет индексы, TIMESTAMPTZ → TIMESTAMP
    # теряет tz. Оставлено как обратное симметричное преобразование.
    with op.batch_alter_table('scrape_runs') as batch_op:
        batch_op.alter_column('finished_at', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('started_at', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('presets') as batch_op:
        batch_op.alter_column('updated_at', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('created_at', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('it_categories', existing_type=_JSON_VARIANT, type_=sa.JSON(), existing_nullable=True)
        batch_op.alter_column('status_codes', existing_type=_JSON_VARIANT, type_=sa.JSON(), existing_nullable=True)

    with op.batch_alter_table('organizations') as batch_op:
        batch_op.alter_column('last_seen', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('first_seen', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('lots') as batch_op:
        batch_op.alter_column('last_synced', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('first_seen', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('amount_y3', existing_type=_MONEY, type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('amount_y2', existing_type=_MONEY, type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('amount_y1', existing_type=_MONEY, type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('plan_amount', existing_type=_MONEY, type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('price_per_unit', existing_type=_MONEY, type_=sa.Float(), existing_nullable=True)

    with op.batch_alter_table('lot_status_history') as batch_op:
        batch_op.alter_column('observed_at', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)

    with op.batch_alter_table('lot_analyses') as batch_op:
        batch_op.alter_column('analyzed_at', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('tech_stack', existing_type=_JSON_VARIANT, type_=sa.JSON(), existing_nullable=True)

    with op.batch_alter_table('documents') as batch_op:
        batch_op.alter_column('downloaded_at', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=True)

    with op.batch_alter_table('contracts') as batch_op:
        batch_op.alter_column('last_synced', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('fact_amount', existing_type=_MONEY, type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('contract_amount', existing_type=_MONEY, type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('plan_amount', existing_type=_MONEY, type_=sa.Float(), existing_nullable=True)

    with op.batch_alter_table('announcements') as batch_op:
        batch_op.alter_column('last_synced', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('first_seen', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=False)
        batch_op.alter_column('raw', existing_type=_JSON_VARIANT, type_=sa.JSON(), existing_nullable=True)
        batch_op.alter_column('publish_date', existing_type=_TZ, type_=sa.DateTime(), existing_nullable=True)
        batch_op.alter_column('total_amount', existing_type=_MONEY, type_=sa.Float(), existing_nullable=True)
