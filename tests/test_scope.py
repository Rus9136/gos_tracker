"""Scope-логика (goszakup.scope) — единый источник правды для web и matcher'а."""

from __future__ import annotations

from goszakup.db.models import Lot, User
from goszakup.scope import lot_in_scope, scope_conditions


def _user(**kw) -> User:
    base = dict(username="u", password_hash="", is_admin=False, is_active=True)
    base.update(kw)
    return User(**base)


def _lot(**kw) -> Lot:
    base = dict(id=1, url="u/1", announcement_id=1)
    base.update(kw)
    return Lot(**base)


def test_admin_and_anon_see_everything():
    assert scope_conditions(None) == []
    assert scope_conditions(_user(is_admin=True)) == []
    assert lot_in_scope(_lot(kato="000000000"), None)
    assert lot_in_scope(_lot(kato="000000000"), _user(is_admin=True))


def test_empty_scope_means_no_restriction():
    u = _user(regions=None, categories=[], min_amount=None)
    assert scope_conditions(u) == []
    assert lot_in_scope(_lot(kato="751000000", plan_amount=None), u)


def test_regions_filter():
    u = _user(regions=["751000000"])
    assert lot_in_scope(_lot(kato="751000000"), u)
    assert not lot_in_scope(_lot(kato="431000000"), u)
    assert len(scope_conditions(u)) == 1


def test_categories_filter():
    u = _user(categories=["it"])
    assert lot_in_scope(_lot(category="it"), u)
    assert not lot_in_scope(_lot(category="medicine"), u)
    assert not lot_in_scope(_lot(category=None), u)


def test_min_amount_filter():
    u = _user(min_amount=1_000_000)
    assert lot_in_scope(_lot(plan_amount=2_000_000), u)
    assert not lot_in_scope(_lot(plan_amount=500_000), u)
    # Без суммы — вне scope: пользователь явно поднял порог.
    assert not lot_in_scope(_lot(plan_amount=None), u)


def test_combined_scope_is_conjunction():
    u = _user(regions=["751000000"], categories=["it"], min_amount=1_000_000)
    ok = _lot(kato="751000000", category="it", plan_amount=5_000_000)
    assert lot_in_scope(ok, u)
    assert not lot_in_scope(
        _lot(kato="431000000", category="it", plan_amount=5_000_000), u
    )
    assert len(scope_conditions(u)) == 3
