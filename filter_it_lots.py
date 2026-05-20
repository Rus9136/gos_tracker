"""
Фильтрует IT-лоты из CSV скрапера и присваивает it_category.
Категории: 'Оборудование', 'Услуги ИТ', 'ПО и лицензии', 'Связь и интернет'.

Правила:
1) точное совпадение enstru с существующим набором (из shymkent_it_lots.csv);
2) если enstru новый — fallback по ключевым словам.
"""

import argparse
import csv
import re

# Маппинг enstru -> категория (собран из shymkent_it_lots.csv).
ENSTRU_TO_CATEGORY = {
    # Услуги ИТ
    "Услуги по сопровождению и технической поддержке информационной системы": "Услуги ИТ",
    "Комплексные работы в сфере информационных технологий «под ключ»": "Услуги ИТ",
    "Услуги по информационно-техническому обеспечению государственных органов": "Услуги ИТ",
    "Услуги по технической поддержке оборудования для хранения персональных данных (г. Шымкент)": "Услуги ИТ",
    # Оборудование
    "Компьютер": "Оборудование",
    "Комплекс программно-аппаратный": "Оборудование",
    "Услуги по администрированию и техническому обслуживанию программно-аппаратного комплекса": "Оборудование",
    "Сервер": "Оборудование",
    "Комплекс оборудования сетевой безопасности": "Оборудование",
    "Ноутбук": "Оборудование",
    "Система конференц связи": "Оборудование",
    "Планшет": "Оборудование",
    "Работы по установке (монтажу) программно-аппаратного комплекса": "Оборудование",
    "Монитор": "Оборудование",
    "Телефон сотовой связи": "Оборудование",
    "Коммутатор сетевой": "Оборудование",
    "Услуги по техническому обслуживанию серверного оборудования": "Оборудование",
    "Услуги по аренде офисной оргтехники": "Оборудование",
    "Термопринтер": "Оборудование",
    # ПО и лицензии
    "Программное обеспечение": "ПО и лицензии",
    "Услуги по пользованию программными продуктами": "ПО и лицензии",
    "Работы по модернизации программного обеспечения": "ПО и лицензии",
    # Связь и интернет
    "Услуги по доступу к Интернету": "Связь и интернет",
}

# Fallback по ключевым словам (lowercased substring match в enstru).
KEYWORD_RULES = [
    (r"\bинтернет", "Связь и интернет"),
    (r"передач(е|и) данных|телекоммуникацион|связи общего пользования", "Связь и интернет"),
    (r"программн(ое|ого|ому) обеспечен|программн(ый|ого|ому) продукт|лицензи(я|и|ям) на", "ПО и лицензии"),
    (r"информацион(ной|ная|ную) систем|информационно-техническ|сопровождени(е|ю) (информ|програм)", "Услуги ИТ"),
    (r"программно-аппаратн|информационных технологий", "Услуги ИТ"),
    (
        r"\bкомпьют|ноутбук|планшет|монитор|термопринтер|принтер|сканер|"
        r"мфу|сервер(а|ы|у|ом|е)?\b|коммутатор|маршрутизатор|оргтехник|"
        r"кассовый аппарат|видеокамер|видеонаблюден",
        "Оборудование",
    ),
]


def classify(enstru: str, lot_name: str) -> str | None:
    if enstru in ENSTRU_TO_CATEGORY:
        return ENSTRU_TO_CATEGORY[enstru]
    haystack = f"{enstru} || {lot_name}".lower()
    for pattern, category in KEYWORD_RULES:
        if re.search(pattern, haystack):
            return category
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument(
        "--status",
        action="append",
        default=[],
        help="фильтр по точному значению столбца status (можно повторять). "
        "Пример: --status Опубликован",
    )
    ap.add_argument(
        "--it-category",
        action="append",
        default=[],
        help="фильтр по it_category (можно повторять). "
        "Допустимые: 'Оборудование', 'Услуги ИТ', 'ПО и лицензии', 'Связь и интернет'",
    )
    args = ap.parse_args()

    with open(args.input, encoding="utf-8-sig", newline="") as fin:
        reader = csv.DictReader(fin)
        rows = list(reader)
        fields = reader.fieldnames + ["it_category"]

    status_filter = set(args.status)
    cat_filter = set(args.it_category)
    out_rows = []
    for r in rows:
        if status_filter and r.get("status", "") not in status_filter:
            continue
        cat = classify(r.get("enstru", ""), r.get("lot_name", ""))
        if not cat:
            continue
        if cat_filter and cat not in cat_filter:
            continue
        r["it_category"] = cat
        out_rows.append(r)

    with open(args.output, "w", encoding="utf-8-sig", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Всего на входе: {len(rows)}")
    print(f"IT-лотов на выходе: {len(out_rows)} → {args.output}")

    from collections import Counter
    cats = Counter(r["it_category"] for r in out_rows)
    for c, n in cats.most_common():
        print(f"  {n:>4}  {c}")


if __name__ == "__main__":
    main()
