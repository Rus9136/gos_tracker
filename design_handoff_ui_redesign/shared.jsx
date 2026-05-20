// Shared mock data + helpers + small components

const fmtAmount = (n) => {
  if (n == null) return '—';
  return new Intl.NumberFormat('ru-RU').format(n);
};
const fmtCompact = (n) => {
  if (n == null) return '—';
  if (n >= 1e9) return (n/1e9).toFixed(1).replace('.0','') + ' млрд';
  if (n >= 1e6) return (n/1e6).toFixed(1).replace('.0','') + ' млн';
  if (n >= 1e3) return (n/1e3).toFixed(0) + ' тыс';
  return String(n);
};
const fmtDate = (d) => d;

// Status taxonomy from README
const STATUS = {
  210: { label: 'Опубликован',                       tone: 'ok'     },
  220: { label: 'Опубликован (приём заявок)',        tone: 'ok'     },
  230: { label: 'Опубликован (доп. заявок)',         tone: 'ok'     },
  240: { label: 'Опубликован (приём цен. предл.)',   tone: 'ok'     },
  250: { label: 'Рассмотрение заявок',               tone: 'info'   },
  260: { label: 'Рассмотрение допол. заявок',        tone: 'info'   },
  270: { label: 'Заполнение условных скидок',        tone: 'info'   },
  280: { label: 'Ожидание аукциона',                 tone: 'info'   },
  310: { label: 'Формирование преддопуска',          tone: 'info'   },
  320: { label: 'Формирование допуска',              tone: 'info'   },
  325: { label: 'Промежуточные итоги',               tone: 'info'   },
  330: { label: 'Формирование итогов',               tone: 'info'   },
  360: { label: 'Закупка состоялась',                tone: 'accent' },
  370: { label: 'Не состоялась',                     tone: 'bad'    },
  410: { label: 'Отказ от лота',                     tone: 'warn'   },
  430: { label: 'Отменён',                           tone: 'bad'    },
  190: { label: 'Изменена документация',             tone: 'warn'   },
  420: { label: 'Приостановлен',                     tone: 'warn'   },
  440: { label: 'На обжаловании',                    tone: 'warn'   },
  444: { label: 'Ожидание камерального контроля',    tone: 'warn'   },
  445: { label: 'Ожидание контроля качества',        tone: 'warn'   },
  460: { label: 'Пройден контроль качества',         tone: 'info'   },
  510: { label: 'Пересмотр итогов',                  tone: 'info'   },
  540: { label: 'Принятие решения об уведомлении',   tone: 'info'   },
  550: { label: 'Пересмотр итогов (решение)',        tone: 'info'   },
};

const IT_CATS = {
  'Услуги ИТ':       { hue: 200, short: 'Услуги ИТ' },
  'Оборудование':    { hue: 80,  short: 'Оборуд.'   },
  'ПО и лицензии':   { hue: 280, short: 'ПО'        },
  'Связь и интернет':{ hue: 160, short: 'Связь'     },
};

const DEV_CATS = {
  '1С-разработка':    { hue: 220 },
  'веб-разработка':   { hue: 250 },
  'мобильное':        { hue: 290 },
  'интеграция':       { hue: 180 },
  'поддержка':        { hue: 140 },
  'инфраструктура':   { hue: 60  },
  'железо':           { hue: 30  },
  'не-разработка':    { hue: 0   },
};

// Mock lots (from screenshots + extended)
const LOTS = [
  {
    id: '84168811-OK1', anno: '17027284-1', lotPk: 41884942,
    name: 'по сопровождению и предоставлению доступа к деятельности психологической службы на электронной автоматизированной системе',
    customer: 'КГУ "Региональный центр психологической поддержки и помощи" управления образования Восточно-Казахстанской области',
    customerShort: 'РЦПП Восточно-Казахстанской области',
    bin: '240240023572',
    cat: 'Услуги ИТ', devCat: 'поддержка',
    enstru: 'Услуги по сопровождению и технической поддержке информационной системы',
    addChar: 'пользование программой АСППМ',
    amount: 17385058, unit: 17385058, qty: '1.0 Одна услуга',
    method: 'Открытый конкурс',
    region: '—', kato: null,
    status: 210, seen: '19.05.2026 11:08', updated: '19.05.2026 12:51',
    soloOk: true, vendorLock: 'medium', confidence: 'high',
    docs: 6, tzDl: true,
    contact: { name: 'КУЦЕНКО АЛЕКСАНДРА СЕРГЕЕВНА', role: 'Сотрудник', email: 'Alexprohodova@mail.ru' },
    history: [{ at: '19.05.2026 11:08', code: 210 }],
    starred: true, watched: true,
  },
  {
    id: '81086201-OK1', anno: '17027231-1', lotPk: 41884901,
    name: 'Услуги по проведению аудита информационных технологий',
    customer: 'АО "Астана-Энергия"',
    customerShort: 'АО "Астана-Энергия"',
    bin: '050240003789',
    cat: 'Услуги ИТ', devCat: 'не-разработка',
    enstru: 'Услуги по проведению аудита информационных технологий',
    addChar: '—',
    amount: 25294000, unit: 25294000, qty: '1.0',
    method: 'Открытый конкурс', region: 'Астана', kato: '710000000',
    status: 210, seen: '19.05.2026 11:08', updated: '19.05.2026 12:30',
    soloOk: false, vendorLock: 'low', confidence: 'high',
    docs: 5, tzDl: true,
    starred: false, watched: true,
  },
  {
    id: '86601472-OK2', anno: '17026980-1', lotPk: 41884781,
    name: 'Приобретения услуг по разработке и внедрению подсистемы учёта автотранспорта в 1С',
    customer: 'ГКП на праве хозяйственного ведения "Алматы тазалық" акимата города Алматы',
    customerShort: 'ГКП "Алматы тазалық"',
    bin: '030440002115',
    cat: 'Услуги ИТ', devCat: '1С-разработка',
    enstru: 'Услуги по сопровождению и технической поддержке информационной системы',
    addChar: 'разработка подсистемы 1С: УТ + интеграция с 1С: УПП',
    amount: 7500000, unit: 7500000, qty: '1.0',
    method: 'Открытый конкурс', region: 'Алматы', kato: '750000000',
    status: 220, seen: '19.05.2026 11:07', updated: '19.05.2026 12:51',
    soloOk: true, vendorLock: 'low', confidence: 'high',
    docs: 4, tzDl: true,
    starred: true, watched: false,
  },
  {
    id: '82959398-OK2', anno: '17026901-1', lotPk: 41884602,
    name: 'Услуги по техническому сопровождению АПК Open Almaty',
    customer: 'АО "Центр развития города Алматы"',
    customerShort: 'АО "Центр развития Алматы"',
    bin: '171140019022',
    cat: 'Услуги ИТ', devCat: 'поддержка',
    enstru: 'Услуги по сопровождению и технической поддержке информационной системы',
    addChar: '—',
    amount: 50893500, unit: 50893500, qty: '1.0',
    method: 'Открытый конкурс', region: 'Алматы', kato: '750000000',
    status: 220, seen: '19.05.2026 11:07', updated: '19.05.2026 11:15',
    soloOk: false, vendorLock: 'high', confidence: 'high',
    docs: 8, tzDl: true,
    starred: false, watched: false,
  },
  {
    id: '85412004-OK1', anno: '17026711-1', lotPk: 41884301,
    name: 'Закуп серверного оборудования для нужд министерства',
    customer: 'РГУ "Министерство искусственного интеллекта и цифрового развития Республики Казахстан"',
    customerShort: 'Министерство ИИ и цифрового развития РК',
    bin: '241040001829',
    cat: 'Оборудование', devCat: 'железо',
    enstru: 'Серверы стоечные',
    addChar: '2U, 2×CPU 32C, 512GB RAM, NVMe 8×3.84TB',
    amount: 187500000, unit: 18750000, qty: '10.0',
    method: 'Открытый конкурс', region: 'Астана', kato: '710000000',
    status: 240, seen: '18.05.2026 09:11', updated: '19.05.2026 10:02',
    soloOk: false, vendorLock: 'medium', confidence: 'high',
    docs: 12, tzDl: true,
    starred: false, watched: true,
  },
  {
    id: '85412009-OK4', anno: '17026511-1', lotPk: 41883994,
    name: 'Услуги по модернизации информационной системы ЦОН',
    customer: 'Некоммерческое акционерное общество «Государственная корпорация «Правительство для граждан»',
    customerShort: 'НАО "Правительство для граждан"',
    bin: '160440001242',
    cat: 'Услуги ИТ', devCat: 'веб-разработка',
    enstru: 'Услуги по разработке заказного программного обеспечения',
    addChar: 'микросервисы, React, PostgreSQL, OpenShift',
    amount: 432000000, unit: 432000000, qty: '1.0',
    method: 'Открытый конкурс', region: 'Астана', kato: '710000000',
    status: 250, seen: '17.05.2026 14:11', updated: '19.05.2026 09:00',
    soloOk: false, vendorLock: 'high', confidence: 'high',
    docs: 11, tzDl: true,
    starred: true, watched: true,
  },
  {
    id: '85002231-OK1', anno: '17026380-1', lotPk: 41883712,
    name: 'Закуп лицензий Microsoft 365 E5 на 2 года',
    customer: 'ГУ "Управление цифровых технологий области Ұлытау"',
    customerShort: 'УЦТ области Ұлытау',
    bin: '230440003117',
    cat: 'ПО и лицензии', devCat: 'не-разработка',
    enstru: 'Программное обеспечение лицензионное',
    addChar: 'M365 E5, 800 пользователей, 24 мес.',
    amount: 213000000, unit: 266250, qty: '800',
    method: 'Запрос ценовых предложений', region: 'Ұлытау', kato: '730000000',
    status: 230, seen: '17.05.2026 10:21', updated: '18.05.2026 17:30',
    soloOk: false, vendorLock: 'high', confidence: 'high',
    docs: 3, tzDl: true,
    starred: false, watched: false,
  },
  {
    id: '84922077-OK3', anno: '17025814-1', lotPk: 41883501,
    name: 'Услуги по разработке мобильного приложения для жителей города',
    customer: 'КГУ "Управление цифровизации города Алматы"',
    customerShort: 'УЦ города Алматы',
    bin: '230540014001',
    cat: 'Услуги ИТ', devCat: 'мобильное',
    enstru: 'Услуги по разработке заказного программного обеспечения',
    addChar: 'iOS + Android (native), бэкенд REST, 6 модулей',
    amount: 96120690, unit: 96120690, qty: '1.0',
    method: 'Открытый конкурс', region: 'Шымкент', kato: '790000000',
    status: 240, seen: '16.05.2026 18:00', updated: '19.05.2026 08:11',
    soloOk: false, vendorLock: 'low', confidence: 'high',
    docs: 7, tzDl: true,
    starred: true, watched: true,
  },
  {
    id: '84001188-OK1', anno: '17025701-1', lotPk: 41883399,
    name: 'Услуги по сопровождению системы электронного документооборота',
    customer: 'ГУ "Управление образования Мангистауской области"',
    customerShort: 'УО Мангистауской области',
    bin: '040340001882',
    cat: 'Услуги ИТ', devCat: 'поддержка',
    enstru: 'Услуги по сопровождению и технической поддержке информационной системы',
    addChar: 'СЭД "Документолог", 1200 рабочих мест',
    amount: 21697413, unit: 21697413, qty: '1.0',
    method: 'Открытый конкурс', region: 'Жетысуская', kato: '195000000',
    status: 210, seen: '16.05.2026 11:30', updated: '17.05.2026 09:15',
    soloOk: true, vendorLock: 'medium', confidence: 'high',
    docs: 5, tzDl: true,
    starred: false, watched: false,
  },
  {
    id: '83770121-OK2', anno: '17025212-1', lotPk: 41883100,
    name: 'Услуги по интеграции с системой межведомственного электронного взаимодействия',
    customer: 'ГУ "Департамент полиции области Абай Министерства внутренних дел Республики Казахстан"',
    customerShort: 'ДП области Абай МВД РК',
    bin: '220340001212',
    cat: 'Услуги ИТ', devCat: 'интеграция',
    enstru: 'Услуги по разработке заказного программного обеспечения',
    addChar: 'СМЭВ, шина IIB, 14 типов карточек',
    amount: 169253642, unit: 169253642, qty: '1.0',
    method: 'Открытый конкурс', region: 'Туркестанская', kato: '550000000',
    status: 210, seen: '15.05.2026 16:01', updated: '18.05.2026 11:32',
    soloOk: false, vendorLock: 'medium', confidence: 'high',
    docs: 9, tzDl: true,
    starred: false, watched: true,
  },
  {
    id: '83711902-OK1', anno: '17024991-1', lotPk: 41882801,
    name: 'Услуги связи и интернет-доступа для офисов филиала',
    customer: 'Коммунальное государственное предприятие "Центр ранней детской реабилитации" Управления общественного здравоохранения города Алматы',
    customerShort: 'КГП "Центр ранней детской реабилитации"',
    bin: '000140001764',
    cat: 'Связь и интернет', devCat: 'инфраструктура',
    enstru: 'Услуги доступа к сети интернет',
    addChar: 'оптика, 1 Гбит/с, 12 точек',
    amount: 12480000, unit: 1040000, qty: '12',
    method: 'Запрос ценовых предложений', region: 'Алматы', kato: '750000000',
    status: 210, seen: '15.05.2026 09:00', updated: '15.05.2026 09:00',
    soloOk: true, vendorLock: 'low', confidence: 'medium',
    docs: 2, tzDl: false,
    starred: false, watched: false,
  },
  {
    id: '83612001-OK5', anno: '17024801-1', lotPk: 41882500,
    name: 'Закуп ноутбуков для педагогического состава',
    customer: 'Государственное коммунальное предприятие "Городской научно-методический центр новых технологий в образовании" Управления образования города Алматы',
    customerShort: 'ГНМЦ НТО Алматы',
    bin: '990440004229',
    cat: 'Оборудование', devCat: 'железо',
    enstru: 'Компьютеры портативные',
    addChar: '15.6", i7, 16GB, 512SSD',
    amount: 78000000, unit: 520000, qty: '150',
    method: 'Открытый конкурс', region: 'Алматы', kato: '750000000',
    status: 220, seen: '14.05.2026 12:00', updated: '19.05.2026 11:00',
    soloOk: false, vendorLock: 'medium', confidence: 'high',
    docs: 6, tzDl: true,
    starred: false, watched: true,
  },
];

const ORGS = [
  { name: 'КГУ "Управление цифровизации города Алматы"', bin: null,            lots: 1,    actual: 1, total: 1514482800 },
  { name: 'ГУ "Департамент полиции области Абай Министерства внутренних дел Республики Казахстан"', bin: null, lots: 1, actual: 1, total: 1034000000 },
  { name: 'Государственное коммунальное предприятие на праве хозяйственного ведения "Городской научно-методический центр новых технологий в образовании" Управления образования города Алматы', bin: '990440004229', lots: 390, actual: 1, total: 954720250 },
  { name: 'Некоммерческое акционерное общество «Государственная корпорация «Правительство для граждан»', bin: null, lots: 4,    actual: 4, total: 867594391 },
  { name: 'ГУ "Управление образования Мангистауской области"', bin: null,     lots: 1,    actual: 1, total: 787500000 },
  { name: 'Коммунальное государственное предприятие на праве хозяйственного ведения "Центр ранней детской реабилитации" Управления общественного здравоохранения города Алматы', bin: '000140001764', lots: 2193, actual: 0, total: 606482087 },
  { name: 'ГУ "Управление цифровых технологий области Ұлытау"', bin: null,    lots: 2,    actual: 2, total: 402000000 },
  { name: 'Республиканское государственное учреждение "Министерство искусственного интеллекта и цифрового развития Республики Казахстан"', bin: null, lots: 1, actual: 1, total: 325862069 },
  { name: 'АО "Астана-Энергия"', bin: '050240003789', lots: 5, actual: 1, total: 89234100 },
  { name: 'АО "Центр развития города Алматы"', bin: '171140019022', lots: 12, actual: 3, total: 174320000 },
];

const REGIONS = ['все', 'Астана', 'Алматы', 'Шымкент', 'Абайская', 'Жетысуская', 'Туркестанская', 'Ұлытау', 'Мангистауская', 'ВКО', '—'];
const ALL_IT_CATS = ['все', ...Object.keys(IT_CATS)];
const ALL_DEV_CATS = ['все', ...Object.keys(DEV_CATS)];
const RISK = ['все', 'low', 'medium', 'high'];

const PRESETS = [
  { name: 'Астана • IT • >5 млн',        n: 16, hue: 200 },
  { name: 'Алматы • 1С',                 n: 8,  hue: 220 },
  { name: 'РК • Заказная разработка',    n: 24, hue: 250 },
  { name: 'РК • Веб',                    n: 11, hue: 270 },
  { name: 'РК • Мобильное',              n: 4,  hue: 290 },
  { name: 'Регионы • Серверы и СХД',     n: 27, hue: 60  },
];

const RUNS = [
  { at: '19.05.2026 13:09', preset: '(adhoc)', listing: 0, fresh: 0, updated: 0, docs: 0, err: 1 },
  { at: '19.05.2026 06:00', preset: 'daily',     listing: 312, fresh: 8, updated: 41, docs: 22, err: 0 },
  { at: '18.05.2026 06:00', preset: 'daily',     listing: 289, fresh: 11, updated: 38, docs: 19, err: 0 },
  { at: '17.05.2026 06:00', preset: 'daily',     listing: 256, fresh: 6, updated: 33, docs: 15, err: 1 },
  { at: '16.05.2026 06:00', preset: 'daily',     listing: 244, fresh: 4, updated: 29, docs: 14, err: 0 },
];

// LLM analysis sample
const LLM_ANALYSIS = {
  dev_category: 'поддержка',
  tech_stack: ['АСППМ', 'PHP', 'MySQL', 'Web'],
  tz_summary: 'Сопровождение и поддержка автоматизированной системы психолого-педагогического мониторинга. Включает консультацию пользователей, исправление ошибок, обновление справочников, доработка 3 форм отчётности по запросу заказчика. Опыт работы с АСППМ не требуется в явном виде, но в ТЗ зашита формулировка «программа уже эксплуатируется заказчиком», что косвенно указывает на необходимость унаследовать сопровождение.',
  solo_feasible: true,
  vendor_lock_risk: 'medium',
  vendor_lock_reasons: [
    'Конкретное название продукта в названии лота (АСППМ)',
    'Привязка к существующей инфраструктуре заказчика',
  ],
  confidence: 'high',
  version: 'v0.4 • gpt-oss-120b',
  analyzed_at: '19.05.2026 12:55',
};

// Icons (heroicons mini — inline SVG strings)
const Icon = ({ name, size = 16, color }) => {
  const paths = {
    search: 'M21 21l-4.35-4.35M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16z',
    chevronDown: 'M6 9l6 6 6-6',
    chevronRight: 'M9 6l6 6-6 6',
    chevronLeft: 'M15 6l-6 6 6 6',
    arrowRight: 'M5 12h14M13 5l7 7-7 7',
    arrowUp: 'M12 19V5M5 12l7-7 7 7',
    arrowDown: 'M12 5v14M5 12l7 7 7-7',
    plus: 'M12 5v14M5 12h14',
    minus: 'M5 12h14',
    x: 'M6 6l12 12M6 18L18 6',
    star: 'M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z',
    bell: 'M18 16v-5a6 6 0 1 0-12 0v5l-2 2v1h16v-1l-2-2zM10 21h4',
    file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6',
    download: 'M12 3v12m0 0l-4-4m4 4l4-4M5 21h14',
    external: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6 M15 3h6v6 M10 14L21 3',
    refresh: 'M3 12a9 9 0 0 1 15.5-6.4L21 8 M21 3v5h-5 M21 12a9 9 0 0 1-15.5 6.4L3 16 M3 21v-5h5',
    filter: 'M4 6h16 M7 12h10 M10 18h4',
    sparkle: 'M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5L12 2z M19 14l.7 2.1L22 17l-2.3.9L19 20l-.7-2.1L16 17l2.3-.9z',
    pin: 'M12 17v5 M5 11l7-7 7 7-2 2-2-2-5 5-5-5z',
    eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
    grid: 'M3 3h8v8H3z M13 3h8v8h-8z M3 13h8v8H3z M13 13h8v8h-8z',
    list: 'M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01',
    settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
    check: 'M5 13l4 4L19 7',
    play: 'M5 3l14 9-14 9V3z',
    send: 'M22 2L11 13 M22 2l-7 20-4-9-9-4z',
    clock: 'M12 8v4l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
    arrowLeft: 'M19 12H5M12 19l-7-7 7-7',
    copy: 'M9 9h10v10H9z M5 5h10v4 M5 5v10h4',
    flame: 'M12 2c2 4 6 6 6 11a6 6 0 1 1-12 0c0-2 1-3 2-4 0 2 1 3 3 3-1-3-1-7 1-10z',
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color||'currentColor'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {paths[name].split(' M').map((d, i) => (
        <path key={i} d={(i===0?'':'M')+d} />
      ))}
    </svg>
  );
};

const StatusPill = ({ code, withCode = true, mini = false }) => {
  const s = STATUS[code] || { label: String(code), tone: 'muted' };
  return (
    <span className={`spill ${s.tone}`} title={s.label}>
      {mini ? null : <span className="dot" style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }}></span>}
      <span className={mini ? 'truncate' : 'truncate'} style={{ maxWidth: mini ? 130 : 220 }}>{s.label}</span>
      {withCode && <span className="code">{code}</span>}
    </span>
  );
};

const CatTag = ({ cat }) => {
  const c = IT_CATS[cat];
  if (!c) return <span className="tag">{cat}</span>;
  return (
    <span className="tag">
      <span className="swatch" style={{ background: `oklch(0.7 0.14 ${c.hue})` }}></span>
      {c.short}
    </span>
  );
};

const DevTag = ({ cat }) => {
  const c = DEV_CATS[cat] || { hue: 0 };
  return (
    <span className="tag">
      <span className="swatch" style={{ background: `oklch(0.72 0.16 ${c.hue})`, borderRadius: '50%' }}></span>
      {cat}
    </span>
  );
};

const RiskBadge = ({ level }) => {
  const map = { low: ['ok','низкий'], medium: ['warn','средний'], high: ['bad','высокий'] };
  const [tone, label] = map[level] || ['muted','—'];
  return <span className={`badge ${tone}`}><span className="dot"></span>{label}</span>;
};

// tiny sparkline
const Sparkline = ({ data, w = 90, h = 28, color }) => {
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const span = Math.max(max - min, 1);
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / span) * (h - 4) - 2;
    return [x, y];
  });
  const d = pts.map((p, i) => (i === 0 ? `M${p[0]},${p[1]}` : `L${p[0]},${p[1]}`)).join(' ');
  const last = pts[pts.length - 1];
  const stroke = color || 'var(--accent)';
  const fill = `color-mix(in oklch, ${stroke} 18%, transparent)`;
  return (
    <svg width={w} height={h} className="spark-svg" style={{ overflow: 'visible' }}>
      <path d={`${d} L${w},${h} L0,${h} Z`} fill={fill} />
      <path d={d} stroke={stroke} strokeWidth="1.5" fill="none" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r="2.4" fill={stroke} stroke="var(--bg-1)" strokeWidth="1" />
    </svg>
  );
};

Object.assign(window, {
  fmtAmount, fmtCompact, fmtDate,
  STATUS, IT_CATS, DEV_CATS,
  LOTS, ORGS, PRESETS, RUNS, LLM_ANALYSIS,
  REGIONS, ALL_IT_CATS, ALL_DEV_CATS, RISK,
  Icon, StatusPill, CatTag, DevTag, RiskBadge, Sparkline,
});
