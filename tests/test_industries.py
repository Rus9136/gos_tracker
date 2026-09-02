"""Отрасль организации: классификатор по названию и фильтр «Отрасль» на /organizations."""

from __future__ import annotations

from fastapi.testclient import TestClient

from goszakup.db.models import Lot, Organization
from goszakup.industries import backfill_industries, classify_industry
from goszakup.jobs.run_preset import _get_or_create_org
from goszakup.web import app as app_mod


def test_classify_medical_by_keywords():
    assert classify_industry('КГП на ПХВ "Городская больница № 1"') == "med"
    assert classify_industry('ГКП "Емхана № 5"') == "med"
    assert classify_industry("ГУ Управление здравоохранения области") == "med"
    assert classify_industry('КГУ "Школа-гимназия № 2"') is None
    # Исключения: не медицина, хотя слова похожи.
    assert classify_industry("РГУ Центр фитосанитарной диагностики") is None
    assert classify_industry("Ветеринарная станция") is None
    assert classify_industry("") is None


def test_org_creation_sets_industry(db_session):
    org = _get_or_create_org(db_session, bin_="100000000009", name='ГКП "Районная больница"')
    assert org.industry == "med"
    school = _get_or_create_org(db_session, bin_="100000000010", name='КГУ "Школа № 1"')
    assert school.industry is None


def test_backfill_only_fills_empty(db_session):
    a = Organization(bin="100000000011", name='КГП "Городская поликлиника № 3"')
    b = Organization(bin="100000000012", name='КГП "Областной госпиталь"', industry="other")
    db_session.add_all([a, b])
    db_session.commit()
    assert backfill_industries(db_session) == 1
    assert a.industry == "med"
    assert b.industry == "other"  # присвоенное не трогаем без force
    assert backfill_industries(db_session, force=True) == 1
    assert b.industry == "med"


def test_organizations_page_filters_by_industry(db_session):
    hospital = Organization(bin="100000000013", name='КГП "Больница Тестовая"', industry="med")
    school = Organization(bin="100000000014", name='КГУ "Школа Тестовая"')
    db_session.add_all([hospital, school])
    db_session.flush()
    for i, org in enumerate((hospital, school), start=1):
        db_session.add(
            Lot(
                id=778000 + i,
                number=f"778000-{i}",
                announcement_id=None,
                name="Лот",
                url="https://x/778000",
                customer_id=org.id,
                plan_amount=1_000,
            )
        )
    db_session.commit()

    app_mod._nav_cache.clear()
    with TestClient(app_mod.app) as client:
        html = client.get("/organizations").text
        assert "Больница Тестовая" in html and "Школа Тестовая" in html
        med = client.get("/organizations?industry=med").text
        assert "Больница Тестовая" in med
        assert "Школа Тестовая" not in med
        assert "из 1 организаций" in med
        # Неизвестный слаг — как без фильтра, а не пустая страница.
        unknown = client.get("/organizations?industry=zzz").text
        assert "Школа Тестовая" in unknown


def test_oked_maps_to_industry_when_name_is_silent():
    from goszakup.industries import industry_from_oked

    assert industry_from_oked(86101) == "med"
    assert industry_from_oked("85310") == "edu"
    assert industry_from_oked("84111") == "gov"
    assert industry_from_oked("99999") is None
    assert industry_from_oked(None) is None
    # Название первично: у больницы в реестре может стоять аптечная розница.
    assert classify_industry('ГКП "Районная больница"', "47731") == "med"
    assert classify_industry('КГУ "Школа № 2"', "85310") == "edu"


def test_industry_sync_applies_oked_and_marks_polled(db_session):
    from goszakup.jobs import industry_sync

    school = Organization(bin="100000000015", name='КГУ "Школа Синк"')
    unknown = Organization(bin="100000000016", name="ТОО Без реестра")
    nobin = Organization(name="Безбиновый заказчик")
    db_session.add_all([school, unknown, nobin])
    db_session.flush()
    for i, org in enumerate((school, unknown, nobin), start=1):
        db_session.add(
            Lot(
                id=779000 + i,
                number=f"779000-{i}",
                announcement_id=None,
                name="Лот",
                url="https://x/779000",
                customer_id=org.id,
                plan_amount=1_000,
            )
        )
    db_session.commit()

    registry = {
        "100000000015": {"okedList": 85310, "email": "school@example.kz", "Address": []},
    }

    class FakeClient:
        def graphql(self, query, variables):
            f = variables["f"]
            id_ = f.get("bin") or f.get("iin")
            hit = registry.get(id_)
            return ({"Subjects": [hit] if hit else []}, None)

    # Безбиновый в выборку не попадает — реестр ищет по БИН/ИИН.
    assert {o.bin for o in industry_sync.orgs_to_sync(db_session)} == {
        "100000000015",
        "100000000016",
    }
    stats = industry_sync.sync_industries(db_session, FakeClient())
    assert (stats.processed, stats.found, stats.not_found) == (2, 1, 1)
    assert school.oked == "85310" and school.industry == "edu"
    assert school.email == "school@example.kz"
    assert school.contacts_synced_at is not None
    # Не найденный тоже помечен — иначе он вечно был бы первым в выборке.
    assert unknown.oked is None and unknown.oked_synced_at is not None
    assert industry_sync.orgs_to_sync(db_session) == []
