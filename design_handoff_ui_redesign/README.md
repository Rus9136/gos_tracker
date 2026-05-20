# Handoff: Goszakup Tracker — UI Redesign

## Overview
Редизайн UI внутреннего трекера государственных закупок Казахстана. Текущий стек:
FastAPI + Jinja2 templates + Vanilla JS (см. `goszakup/web/templates/`).
Задача: реализовать в существующем codebase новый визуальный язык и информационную
архитектуру (боковая навигация, плотная таблица, sticky-панель в карточке лота,
LLM-аналитика и чат, фильтры, preset-чипы).

## About the design files
Файлы в этом пакете — **дизайн-референсы, написанные в HTML/React (через CDN Babel)**.
Это прототипы, демонстрирующие желаемый внешний вид и поведение, **а не production-код для копирования напрямую**.

Задача — **воссоздать их в существующей среде проекта** (FastAPI + Jinja2 + Vanilla JS),
переиспользуя имеющиеся паттерны. Если для какого-то экрана JS-интерактив окажется
объёмным (например, чат по ТЗ или массовое выделение лотов) — рассмотреть точечное
внедрение Alpine.js / HTMX / небольшого встроенного React-острова. Глобально переезжать
на React **не нужно**, проект серверный.

## Fidelity
**High-fidelity.** Цвета, типографика, отступы, состояния, иконки и компоновка
определены окончательно. Реализовывать пиксельно близко.

## Screens / Views

### 1. Shell (общая оболочка)
- **Layout**: `display: grid; grid-template-columns: 232px 1fr;`
- **Sidebar** (232px, sticky, full-height):
  - Бренд: 30×30 округлый градиентный знак "GZ" + "Goszakup Tracker" + ver-чип `v0.2`
  - Группа "Тендеры": Дашборд / Актуальные (200) / Прошедшие (2582) / Заказчики (439)
  - Группа "Система": Preset'ы (20) / Прогоны / Догрузка по БИН
  - Каждый пункт: иконка 16px + лейбл + счётчик-pill в правом краю
  - Активный пункт: фон `var(--accent-tint)`, левый маркер 3px от `var(--accent)`
  - В подвале: компактная карточка пользователя (аватар + имя + роль)
  - На ширине <1100px сворачивается в 64px-полосу с одними иконками
- **Top action bar** (sticky, blur background):
  - Глобальный поиск (max-width 520px) с иконкой и `⌘K`-кнопкой
  - Справа: «Прогон» (sm-кнопка), колокольчик, шестерёнка

### 2. Дашборд (`/`)
- **KPI grid** (4 колонки): Всего лотов / Актуальных (акцент) / Заказчиков / Документов.
  В каждой плитке — лейбл (uppercase 11px), большое число (32px Manrope 700, tabular-nums)
  и спарклайн справа сверху, дельта снизу (стрелка + цвет ok/bad).
- **Preset-чипы** (быстрый старт): горизонтальная лента с квадратными свотчами по hue
  IT-категории + название + кол-во. Кнопка «+ создать».
- **Два панели** (1.4fr / 1fr): «По регионам» (barlist с заливкой) и «По IT-категориям»
  (тонкие bar'ы цветом по hue категории).
- **Требуют внимания**: список из 5 starred/watched лотов — id, название (clamp-2),
  заказчик, статус-pill, сумма.
- **Последние прогоны**: компактная таблица (`Когда / Preset / listing / new / upd / docs / err`).

### 3. Актуальные тендеры (`/actual` и `/past`)
- Хлебные крошки + заголовок «Актуальные тендеры», справа: счётчик найдено + сумма ₸,
  переключатель view (таблица/карточки), кнопка «Экспорт».
- **Preset-чипы** под заголовком (тапаются, активный — акцентный).
- **Filter card**: 2 строки grid:
  - Поиск / Регион / IT-категория / Тип разработки / Риск заточки / [Применить]
  - Сумма от / Сумма до / Сортировка / Статус-чипы / [Сбросить]
  - Под фильтрами — лента «Активные фильтры» в виде закрываемых чипов
- **Bulk action bar** (появляется, если selected.size > 0): акцентный фон,
  «Выбрано: N» + кнопки «В избранное / LLM-анализ / Скачать ТЗ / Сравнить».
- **Compact table**:
  - Колонки: ☐ / ★ / Лот (id+anno моно, 130px) / Наименование+Заказчик / Категория / Статус / Сумма ₸ (моно, num) / Регион / Замечен / row-actions (видны на hover)
  - Высота строки: `--row-h` (44px cozy / 38px compact / 56px spacious)
  - Под наименованием — мини-теги: DevTag, RiskBadge (если не low), «соло-ok»
  - Hover: фон `bg-2/60`, появляются row-actions (file/sparkle/external)
  - Selected: фон `var(--accent-tint)`
- **Card view** (toggle): auto-fill minmax(360px, 1fr), та же информация компактнее.
- Пагинация в card-foot.

### 4. Карточка лота (`/lot/{id}`)
- Хлебные крошки `← Актуальные / Прошедшие / #ID`.
- Заголовок (24px, textWrap pretty) + meta-строка (моно id, anno, ссылка на goszakup, замечен/обновлён).
- Справа от заголовка: «В избранное / Следить / Скопировать БИН / На goszakup» (primary).
- **Horizontal status flow** (отдельная card-плашка):
  - 7 шагов: 210 → 220 → 250 → 320 → 325 → 330 → 360
  - Кружок 22px: пройденные — акцентный фон + check; текущий — pulsing ring; будущие — серый круг с номером
  - Под кружком моно-код 10px + лейбл 12px
  - Линии между шагами заливаются акцентным по достижении
- **Split layout 1fr / 380px**:
  - **Левая колонка**:
    - Сведения по лоту: dl (160px / 1fr) — IT-категория, Тип разработки (LLM), ENSTRU,
      Доп. характеристика, Плановая сумма (моно 16px strong), Цена за ед., Количество,
      Способ закупки, Регион (КАТО)
    - Документация: таблица с колонками Документ (иконка файла + название, primary с badge «основное ТЗ»),
      ТЗ (Да/Нет с галочкой), Размер, Скачано, ссылка «скачать»
    - Хронология статусов: вертикальный timeline (точки на полосе)
  - **Правая колонка** (sticky top:80px):
    - **Заказчик**: аватар (буква, hue по BIN) + полное название (clamp-2) + БИН + 2 кнопки
    - **LLM-анализ ТЗ** (акцентная рамка):
      - Header с иконкой sparkle, бейдж confidence, кнопка «Перезапустить»
      - Резюме (textWrap pretty), Стек (моно-теги), Соло-разработчик / Риск заточки (grid 1/1),
        «Почему» — bulleted reasons
      - Footer моно: версия анализатора + дата
    - **Чат по ТЗ**:
      - Список bubbles (user — акцентный фон справа, ai — нейтральный слева, role-метка сверху)
      - Textarea + кнопка «Отправить» (Cmd/Ctrl+Enter)
      - Под полем — quick-prompt чипы: «Требования к опыту / Срок / Обеспечение / Заточка?»
    - **Контактное лицо**

### 5. Заказчики (`/organizations`)
- Page-head с подзаголовком «439 организаций • топ-10 держат 62% бюджета».
- Кнопки: Экспорт CSV / **Догрузка по БИН** (primary).
- Filter card: Поиск (название/БИН) / Сортировка (по сумме/лотам/актуальным) / Сегментный «Только с актуальными» / Применить.
- **Таблица**:
  - Колонки: # / Организация (аватар + название clamp-2 + индикатор пульсации актуальных) /
    БИН (моно accent) / Лотов (num моно) / Актуальных (badge ok с числом) /
    Сумма всего (inline-bar 200px шириной + число справа выровненное)
  - Hover-actions: скопировать БИН / открыть
- Пагинация снизу.

## Interactions & Behavior

### Sidebar nav
- Клик по пункту — переход. Состояние active вычисляется по текущему route.
- При route=`lot` активным остаётся «Актуальные» (lot — это drill-down).
- При route=`customer/{id}` — активным остаётся «Заказчики».

### Top search
- ⌘K (Cmd+K / Ctrl+K) — фокус в поле поиска (требуется хоткей).
- Поиск глобальный: по лотам / БИН / заказчикам.

### Listing
- Чипы preset'ов: тап включает/выключает (взаимоисключающее).
- Фильтры: применяются по кнопке «Применить» **и** реактивно при выборе селекта (как реализовано в прототипе).
- Сортировка: `seen-desc | seen-asc | amt-desc | amt-asc`.
- View toggle: `compact | cards`. Сохранять в localStorage.
- Hover row: показать row-actions с задержкой 80ms.
- Чекбокс-выделение → появление bulk action bar (slide-down плашка над списком, акцентный фон).
- Клик по строке (кроме чекбокса и иконок) → переход на карточку лота.

### Lot card
- Sticky правая колонка: `position: sticky; top: 80px;` (учитывает sticky-topbar 52px + запас).
- Чат: Cmd/Ctrl+Enter — отправить. История в localStorage по `lot_id`.
- LLM-анализ: кнопка «Перезапустить» дергает существующий эндпоинт `POST /lot/{id}/analyze`.
- Quick-prompts заполняют textarea.
- Кнопка «Скопировать БИН»: `navigator.clipboard.writeText(lot.bin)` + тост-уведомление.

### Statuses → tone mapping (точное соответствие)
```
ok     → 210, 220, 230, 240        (приём заявок, опубликован)
info   → 250, 260, 270, 280, 310, 320, 325, 330, 460, 510, 540, 550
accent → 360                       (закупка состоялась)
bad    → 370, 430                  (не состоялась / отменён)
warn   → 410, 190, 420, 440, 444, 445
```

### Animations
- Pulse (актуальные индикаторы): 1.6s ease-out infinite, scale 0.6 → 2.2, opacity 0.5 → 0.
- Skeleton: shimmer 1.6s linear infinite (для загрузки данных).
- Hover transitions: `background 100ms, border-color 100ms`.
- Sidebar active marker: без анимации, появляется мгновенно при смене route.

## State Management
Существующие FastAPI-роуты остаются. Для интерактивных частей:

- **Selected lots** (массовое выделение): локальный state в `/actual` шаблоне.
  Достаточно AlpineJS `x-data` или мини-React-острова.
- **View toggle**: localStorage (`gz.list_view`).
- **Chat history**: localStorage (`gz.chat.{lot_id}`), как сейчас.
- **Tweaks** (тема/плотность/акцент/вид списка):
  - localStorage (`gz.tweaks.*`)
  - применяются как `data-theme`, `data-density` атрибуты на `<html>` и CSS custom property `--accent-h`

## Design Tokens

### Colors (oklch — все в `styles.css` как CSS custom properties)

**Dark (default):**
```
--bg-0:   oklch(0.165 0.018 255)   /* page */
--bg-1:   oklch(0.205 0.020 255)   /* surface */
--bg-2:   oklch(0.245 0.022 255)   /* raised */
--bg-3:   oklch(0.290 0.025 255)   /* hover */
--bd-1:   oklch(0.305 0.022 255)   /* hairline */
--bd-2:   oklch(0.360 0.025 255)   /* stronger */

--fg-0:   oklch(0.97 0.005 255)    /* primary */
--fg-1:   oklch(0.82 0.008 255)    /* secondary */
--fg-2:   oklch(0.65 0.012 255)    /* tertiary / labels */
--fg-3:   oklch(0.50 0.012 255)    /* faint */
```

**Accent** (изумрудный по умолчанию, hue 155):
```
--accent-h:    155
--accent:      oklch(0.68 0.16 var(--accent-h))
--accent-hi:   oklch(0.78 0.14 var(--accent-h))
--accent-lo:   oklch(0.45 0.16 var(--accent-h))
--accent-tint: oklch(0.30 0.08 var(--accent-h) / 0.35)
```

**Semantic** (tone-окраска статусов и бейджей):
```
--ok:    oklch(0.74 0.15 155)   --ok-bg:   oklch(0.42 0.10 155 / 0.25)
--warn:  oklch(0.80 0.14 80)    --warn-bg: oklch(0.50 0.10 80  / 0.25)
--bad:   oklch(0.70 0.18 25)    --bad-bg:  oklch(0.45 0.13 25  / 0.28)
--info:  oklch(0.78 0.12 220)   --info-bg: oklch(0.42 0.10 220 / 0.25)
```

**Light theme**: см. `styles.css` блок `:root[data-theme="light"]`.

### Typography
- UI: `Manrope` (Google Fonts) — 400/500/600/700/800
- Mono (БИН, id, суммы, временные метки): `JetBrains Mono` — 400/500/600
- Базовый: 14px / line-height 1.45
- Page title: 22px / 700 / -0.02em
- Card title: 14px / 600 / -0.005em
- KPI value: 32px / 700 / -0.02em / tabular-nums
- Table header: 11.5px / 500 / uppercase / letter-spacing 0.06em
- Table cell: 13px
- Mono-числа: `font-variant-numeric: tabular-nums` + `font-feature-settings: "tnum"`

### Spacing scale
```
--gap-1: 8px;
--gap-2: 12px;
--gap-3: 16px;
--gap-4: 24px;
--gap-5: 32px;
--pad-card: 20px (cozy) / 16px (compact) / 28px (spacious);
--pad-cell-y: 12px / 8px / 16px;
--pad-cell-x: 16px / 12px / 20px;
--row-h: 44px / 38px / 56px;
```

### Radii / shadows
```
--r-1: 6px;  /* chips, tags */
--r-2: 10px; /* inputs, segmented control */
--r-3: 14px; /* cards */
--shadow-1: 0 1px 0 oklch(1 0 0 / 0.04) inset, 0 1px 2px oklch(0 0 0 / 0.3);
--shadow-2: 0 8px 24px oklch(0 0 0 / 0.35), 0 1px 0 oklch(1 0 0 / 0.04) inset;
```

## Assets
- **Шрифты**: подключаются с Google Fonts:
  `https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap`
- **Иконки**: inline SVG, набор из ~22 пиктограмм Heroicons-mini, см. компонент `Icon` в `shared.jsx`. Перенести в `templates/_macros/icons.html` (jinja-макрос на каждую иконку) или подключить heroicons npm/CDN.
- Брендовый знак «GZ» — CSS-градиент `oklch(0.68 0.16 var(--accent-h)) → oklch(0.45 0.16 var(--accent-h))` + моно-текст. Не SVG.

## Files (включены в этот пакет)
- `Goszakup Tracker.html` — корневой HTML (роутинг через React-state)
- `styles.css` — все токены и компоненты
- `shared.jsx` — мок-данные, словари статусов/категорий, общие компоненты (`Icon`, `StatusPill`, `CatTag`, `DevTag`, `RiskBadge`, `Sparkline`)
- `app.jsx` — App-компонент (sidebar + topbar + роутер)
- `dashboard.jsx` — Дашборд
- `listing.jsx` — Актуальные тендеры
- `lot.jsx` — Карточка лота
- `customers.jsx` — Заказчики
- `tweaks-panel.jsx` — панель тонкой настройки (можно не переносить — это только для дизайн-итераций)

## Maps to existing Jinja templates

| Прототип-файл       | Целевой Jinja-шаблон в `goszakup/web/templates/` |
| ------------------- | ------------------------------------------------ |
| `app.jsx` shell     | `_layout.html` (sidebar + topbar)                |
| `dashboard.jsx`     | `index.html`                                     |
| `listing.jsx`       | `lots.html` (используется для /actual и /past)   |
| `lot.jsx`           | `lot.html`                                       |
| `customers.jsx`     | `organizations.html`                             |
| `shared.jsx` Icon   | `_macros/icons.html`                             |
| `shared.jsx` пиллы  | `_macros/pills.html` (StatusPill, CatTag, ...)   |

## Recommendations for implementation order

1. **CSS-токены** — перенести `styles.css` в `static/css/app.css` (можно как есть).
2. **_layout.html** — sidebar + topbar, заменить старый header.
3. **Иконки** — макрос `{% from "_macros/icons.html" import icon %}` → `{{ icon("search", 14) }}`.
4. **Pill-макросы** — status_pill, cat_tag, dev_tag, risk_badge.
5. **lots.html** — таблица + фильтры. Bulk-actions можно делать на AlpineJS.
6. **lot.html** — split-layout, sticky правая колонка, чат и LLM-анализ переиспользуют существующие эндпоинты.
7. **organizations.html** — таблица с inline-bar.
8. **index.html** — KPI и панели в последнюю очередь.

## Tweaks panel
Файл `tweaks-panel.jsx` использован **только в прототипе** для демонстрации
альтернативных оттенков акцента, тёмной/светлой темы и плотности. В целевой
реализации Tweaks не нужны: значения по умолчанию зафиксированы (изумруд / тёмная / cozy /
таблица). Если хочется — можно вынести в `settings`-страницу как «персональные предпочтения»
с сохранением в localStorage.
