"""Гейт 2: create_all-схема не расходится с Alembic.

init_db() на свежей БД строит таблицы через create_all И штампует Alembic head,
чтобы последующий `alembic upgrade head` не падал/не расходился молча.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from goszakup.db.engine import engine, init_db


def _alembic_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from goszakup.config import ROOT

    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def test_fresh_db_is_stamped_at_alembic_head():
    init_db()
    insp = inspect(engine)
    assert insp.has_table("alembic_version"), "alembic_version не создан — схема не заштампована"
    with engine.connect() as c:
        version = c.execute(text("select version_num from alembic_version")).scalar()
    assert version == _alembic_head()
