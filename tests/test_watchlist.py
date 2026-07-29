"""Watchlist фазы C: вертикали подписчиков ∪ пре-фильтры их запросов."""

from __future__ import annotations

from sqlalchemy import select

from goszakup.classify import llm as llm_mod
from goszakup.db.models import Announcement, Lot, User, UserQuery
from goszakup.watchlist import (
    invalidate_watchlist_cache,
    should_analyze,
    watchlist_conditions,
    watchlist_rules,
)


def _mk_lot(db_session, lot_id, category, **kw):
    ann = Announcement(id=lot_id * 10, url="https://example/a")
    db_session.add(ann)
    db_session.flush()
    lot = Lot(
        id=lot_id,
        announcement_id=ann.id,
        url="https://example/lot",
        name=kw.pop("name", "Лот"),
        category=category,
        **kw,
    )
    db_session.add(lot)
    db_session.flush()
    return lot


def _mk_user(db_session, **kw):
    base = dict(
        username=f"u{kw.get('id', 1)}",
        password_hash="",
        is_admin=False,
        is_active=True,
    )
    base.update(kw)
    user = User(**base)
    db_session.add(user)
    db_session.flush()
    invalidate_watchlist_cache()
    return user


def _mk_query(db_session, user, prefilter, active=True):
    q = UserQuery(
        user_id=user.id if user else 0,
        name="q",
        text="хочу",
        compiled_filters=prefilter,
        active=active,
    )
    db_session.add(q)
    db_session.flush()
    invalidate_watchlist_cache()
    return q


def _ids_by_sql(db_session):
    return set(
        db_session.scalars(select(Lot.id).where(watchlist_conditions(db_session))).all()
    )


def test_empty_watchlist_analyzes_nothing(db_session):
    """Ни подписчиков, ни пре-фильтров → всё False, а SQL — false(), не None
    (`where(None)` означал бы «весь рынок» — ровно наоборот)."""
    lot = _mk_lot(db_session, 1, "it")
    invalidate_watchlist_cache()
    assert watchlist_rules(db_session) == []
    assert not should_analyze(db_session, lot)
    assert _ids_by_sql(db_session) == set()


def test_subscriber_vertical_opens_watchlist(db_session):
    it_lot = _mk_lot(db_session, 1, "it")
    med_lot = _mk_lot(db_session, 2, "medicine")
    _mk_user(db_session, id=1, categories=["it"])

    assert should_analyze(db_session, it_lot)
    assert not should_analyze(db_session, med_lot)
    assert _ids_by_sql(db_session) == {1}


def test_empty_categories_does_not_widen_watchlist(db_session):
    """Инверсия относительно scope: там пустой categories = «вижу всё»,
    здесь — «не расширяю» (иначе админ с NULL-scope тянет весь рынок)."""
    lot = _mk_lot(db_session, 1, "it")
    _mk_user(db_session, id=1, is_admin=True, categories=None)
    _mk_user(db_session, id=2, categories=[])

    assert not should_analyze(db_session, lot)


def test_inactive_user_is_ignored(db_session):
    lot = _mk_lot(db_session, 1, "it")
    _mk_user(db_session, id=1, categories=["it"], is_active=False)

    assert not should_analyze(db_session, lot)


def test_query_prefilter_opens_watchlist(db_session):
    """Пользователь без вертикального ограничения получает узкий срез по
    пре-фильтру вместо всего рынка."""
    med = _mk_lot(db_session, 1, "medicine", name="Поставка томографа")
    furniture = _mk_lot(db_session, 2, "furniture", name="Стулья")
    user = _mk_user(db_session, id=1)
    _mk_query(db_session, user, {"categories": ["medicine"]})

    assert should_analyze(db_session, med)
    assert not should_analyze(db_session, furniture)
    assert _ids_by_sql(db_session) == {1}


def test_query_without_prefilter_does_not_open_watchlist(db_session):
    lot = _mk_lot(db_session, 1, "medicine")
    user = _mk_user(db_session, id=1)
    _mk_query(db_session, user, None)

    assert not should_analyze(db_session, lot)


def test_inactive_query_is_ignored(db_session):
    lot = _mk_lot(db_session, 1, "medicine")
    user = _mk_user(db_session, id=1)
    _mk_query(db_session, user, {"categories": ["medicine"]}, active=False)

    assert not should_analyze(db_session, lot)


def test_prefilter_is_intersected_with_owner_scope(db_session):
    """Клиент с categories=['it'] не может пре-фильтром заказать анализ
    медицины, которую всё равно не увидит."""
    med = _mk_lot(db_session, 1, "medicine")
    user = _mk_user(db_session, id=1, categories=["it"])
    _mk_query(db_session, user, {"categories": ["medicine"]})

    assert not should_analyze(db_session, med)


def test_dev_query_without_user_row_is_kept(db_session):
    """GZ_NO_AUTH-админ имеет id=0 и строки в users не имеет — его запрос
    не должен теряться (outerjoin), scope при этом «видит всё»."""
    lot = _mk_lot(db_session, 1, "medicine")
    _mk_query(db_session, None, {"categories": ["medicine"]})

    assert should_analyze(db_session, lot)


def test_sql_mirror_is_superset_of_predicate(db_session):
    """keywords в SQL не выражаются — зеркало обязано быть надмножеством."""
    hit = _mk_lot(db_session, 1, "it", name="Поставка серверов")
    miss = _mk_lot(db_session, 2, "it", name="Поставка мебели")
    user = _mk_user(db_session, id=1)
    _mk_query(db_session, user, {"categories": ["it"], "keywords": ["сервер"]})

    assert should_analyze(db_session, hit)
    assert not should_analyze(db_session, miss)
    assert _ids_by_sql(db_session) == {hit.id, miss.id}


def test_analyze_inner_refuses_non_watchlist(db_session, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("LLM не должен вызываться вне watchlist")

    monkeypatch.setattr(llm_mod, "_call_llm", _boom)
    _mk_user(db_session, id=1, categories=["it"])
    lot = _mk_lot(db_session, 4, "medicine")
    assert llm_mod.analyze_and_save(db_session, lot) is False
