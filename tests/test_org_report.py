"""Отчёт по закупкам организации (jobs/org_report.py)."""

from datetime import UTC, datetime

from goszakup.db.models import Announcement, Contract, Lot, Organization
from goszakup.jobs.org_report import build_org_report, related_org_ids, render_markdown


def _seed(s):
    # Дубль организации: customer без БИН (из листинга) + organizer с БИН.
    cust = Organization(name='КГП "Больница"', bin=None)
    orgz = Organization(name='Коммунальное ГП "Больница"', bin="990240004300")
    other = Organization(name="ТОО Победитель", bin="111111111111")
    s.add_all([cust, orgz, other])
    s.flush()

    a1 = Announcement(
        id=101, url="u1", organizer_id=orgz.id,
        publish_date=datetime(2024, 3, 1, tzinfo=UTC),
    )
    a2 = Announcement(
        id=102, url="u2", organizer_id=orgz.id,
        publish_date=datetime(2025, 5, 1, tzinfo=UTC),
    )
    s.add_all([a1, a2])
    s.flush()

    # Состоялся, с победителем и договором; заказчик — запись без БИН.
    l1 = Lot(
        id=1, announcement_id=101, customer_id=cust.id, url="l1",
        number="1-ОИ1", enstru="Услуги ИТ-сопровождения", name="Сопровождение 1С",
        status_code=360, status_name="Закупка состоялась",
        plan_amount=1000, it_category="Услуги ИТ",
        winner_bin="111111111111", winner_name="ТОО Победитель",
    )
    # Не состоялся.
    l2 = Lot(
        id=2, announcement_id=101, customer_id=cust.id, url="l2",
        number="1-ОИ2", enstru="Услуги ИТ-сопровождения", name="Сопровождение 1С",
        status_code=370, status_name="Закупка не состоялась", plan_amount=1000,
    )
    # Состоялся, лот связан только через organizer (customer_id пуст).
    l3 = Lot(
        id=3, announcement_id=102, customer_id=None, url="l3",
        number="2-ЗЦП1", enstru="Услуги прачечные", name="Стирка",
        status_code=360, status_name="Закупка состоялась",
        plan_amount=500, winner_bin="111111111111", winner_name="ТОО Победитель",
    )
    # Актуальный (открытый).
    l4 = Lot(
        id=4, announcement_id=102, customer_id=cust.id, url="l4",
        number="2-ЗЦП2", enstru="Услуги ИТ-сопровождения", name="Модернизация ИС",
        status_code=240, status_name="Опубликован (прием ценовых предложений)",
        plan_amount=9200, is_actual=True, it_category="Услуги ИТ",
    )
    s.add_all([l1, l2, l3, l4])
    s.flush()
    s.add(Contract(lot_id=1, contract_number="c1", contract_amount=900))
    s.commit()
    return cust, orgz


def test_related_org_ids_merges_duplicates(db_session):
    cust, orgz = _seed(db_session)
    assert set(related_org_ids(db_session, orgz)) >= {orgz.id}
    # запись с тем же именем, но без БИН (customer из листинга) — склеивается
    dup = Organization(name=orgz.name, bin=None)
    db_session.add(dup)
    db_session.commit()
    assert dup.id in related_org_ids(db_session, orgz)


def test_build_org_report_counts(db_session):
    cust, orgz = _seed(db_session)
    r = build_org_report(db_session, orgz)
    k = r["kpis"]
    assert k.n_lots == 4            # l1, l2 (через customer тоже organizer), l3, l4
    assert k.n_ok == 2              # l1 + l3
    assert k.n_winner == 2
    assert float(k.contract_total) == 900   # только договор l1; у l3 договора нет
    assert k.it_n == 2 and k.it_ok == 1

    years = {int(y.yr): y for y in r["years"]}
    assert years[2024].n_ok == 1 and float(years[2024].contract_ok) == 900
    assert years[2025].n_ok == 1 and float(years[2025].contract_ok) == 0

    winners = r["winners"]
    assert len(winners) == 1
    # договор (900) + план l3 без договора (500)
    assert float(winners[0].total) == 1400

    assert len(r["it_lots"]) == 1 and r["it_lots"][0].id == 1
    assert len(r["actual_lots"]) == 1 and r["actual_lots"][0].id == 4


def test_report_from_customer_row_covers_same_lots(db_session):
    cust, orgz = _seed(db_session)
    # У записи-заказчика нет БИН и другое имя — но её лоты видны, а отчёт
    # по ней охватывает как минимум её собственные лоты.
    r = build_org_report(db_session, cust)
    assert r["kpis"].n_lots >= 3


def test_render_markdown(db_session):
    cust, orgz = _seed(db_session)
    md = render_markdown(build_org_report(db_session, orgz), base_url="https://x")
    assert "# Отчёт по закупкам" in md
    assert "990240004300" in md
    assert "ТОО Победитель" in md
    assert "https://x/lot/1" in md
    assert "| 2024 |" in md
