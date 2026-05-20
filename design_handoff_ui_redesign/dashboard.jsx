// Dashboard page

const Dashboard = ({ goto }) => {
  const totals = {
    all: 2782,
    actual: 200,
    customers: 439,
    docs: 7377,
  };
  // Spark data
  const spark1 = [12,10,15,18,14,19,22,20,24,21,26,30,28,32];
  const spark2 = [7,5,9,8,11,10,8,12,14,12,13,17,15,16];
  const spark3 = [400,420,415,440,430,438,445,439];
  const spark4 = [320,330,340,335,360,370,365,380,392];

  // Regions
  const regions = [
    { name: '—',          lots: 168, sum: 3633323472 },
    { name: 'Астана',     lots: 16,  sum: 610095524  },
    { name: 'Алматы',     lots: 8,   sum: 1712282880 },
    { name: 'Ұлытау',     lots: 3,   sum: 464311062  },
    { name: 'Туркестанская', lots: 2, sum: 169253642 },
    { name: 'Шымкент',    lots: 1,   sum: 96120690   },
    { name: 'Жетысуская', lots: 1,   sum: 21697413   },
    { name: 'Абайская',   lots: 1,   sum: 1034000000 },
  ];
  const maxR = Math.max(...regions.map(r => r.sum));

  const cats = [
    { name: 'Оборудование',     n: 79, sum: 1786359401, hue: 80  },
    { name: 'Услуги ИТ',        n: 61, sum: 4596554043, hue: 200 },
    { name: 'Связь и интернет', n: 43, sum: 173945270,  hue: 160 },
    { name: 'ПО и лицензии',    n: 17, sum: 1184225968, hue: 280 },
  ];
  const maxC = Math.max(...cats.map(c => c.sum));

  // attention list: lots that need triage
  const attention = LOTS.filter(l => l.watched || l.starred).slice(0, 5);

  return (
    <div data-screen-label="01 Дашборд">
      <div className="page-head">
        <div>
          <h1 className="page-title">Дашборд</h1>
          <div className="page-sub">База: 2 782 лота • последний прогон сегодня 06:00 • ежедневный launchd-агент активен</div>
        </div>
        <div className="flex gap-2">
          <button className="btn"><Icon name="refresh" /> Прогнать сейчас</button>
          <button className="btn primary" onClick={() => goto('actual')}><Icon name="play" /> Открыть актуальные</button>
        </div>
      </div>

      {/* KPI */}
      <div className="kpi-grid">
        <div className="kpi">
          <div className="flex justify-between items-center">
            <div className="k-label">Всего лотов</div>
            <Sparkline data={spark1} w={80} h={24} color="oklch(0.75 0.08 255)" />
          </div>
          <div className="k-value num">{fmtAmount(totals.all)}</div>
          <div className="k-delta up"><Icon name="arrowUp" size={12} /> +49 за сутки</div>
        </div>
        <div className="kpi accent">
          <div className="flex justify-between items-center">
            <div className="k-label" style={{ color: 'var(--accent-hi)' }}>Актуальных</div>
            <Sparkline data={spark2} w={80} h={24} />
          </div>
          <div className="k-value num">{fmtAmount(totals.actual)}</div>
          <div className="k-delta up"><Icon name="arrowUp" size={12} /> +8 новых • 41 обновлено</div>
        </div>
        <div className="kpi">
          <div className="flex justify-between items-center">
            <div className="k-label">Заказчиков</div>
            <Sparkline data={spark3} w={80} h={24} color="oklch(0.75 0.12 220)" />
          </div>
          <div className="k-value num">{fmtAmount(totals.customers)}</div>
          <div className="k-delta">439 уникальных по БИН/имени</div>
        </div>
        <div className="kpi">
          <div className="flex justify-between items-center">
            <div className="k-label">Документов</div>
            <Sparkline data={spark4} w={80} h={24} color="oklch(0.75 0.12 155)" />
          </div>
          <div className="k-value num">{fmtAmount(totals.docs)}</div>
          <div className="k-delta">+22 скачано сегодня</div>
        </div>
      </div>

      {/* Quick-access presets */}
      <div className="card mt-4">
        <div className="card-head">
          <div className="flex items-center gap-3">
            <div className="card-title">Быстрый старт по preset'ам</div>
            <span className="dim">— сохранённые фильтры</span>
          </div>
          <button className="btn sm ghost">Управлять preset'ами</button>
        </div>
        <div className="card-body" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {PRESETS.map(p => (
            <button key={p.name} className="chip" onClick={() => goto('actual')}>
              <span className="swatch" style={{ width: 8, height: 8, borderRadius: 2, background: `oklch(0.7 0.14 ${p.hue})` }}></span>
              {p.name}
              <span className="dim mono">{p.n}</span>
            </button>
          ))}
          <button className="chip"><Icon name="plus" size={12} /> создать</button>
        </div>
      </div>

      {/* Panels grid */}
      <div className="panels">
        {/* By region */}
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">По регионам</div>
              <div className="card-sub">Актуальные лоты — сумма ₸</div>
            </div>
            <div className="seg">
              <button className="active">Сумма</button>
              <button>Кол-во</button>
            </div>
          </div>
          <div className="card-body">
            <div className="barlist">
              {regions.map(r => (
                <div className="row" key={r.name}>
                  <div className="rail">
                    <div className="fill" style={{ width: `${(r.sum / maxR) * 100}%` }}></div>
                    <span className="lbl">{r.name}</span>
                  </div>
                  <div className="val">
                    <span className="dim">{r.lots} лот.</span>
                    <span style={{ marginLeft: 12 }} className="strong">{fmtAmount(r.sum)} ₸</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* IT cats */}
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">По IT-категориям</div>
              <div className="card-sub">Актуальные лоты</div>
            </div>
          </div>
          <div className="card-body">
            <div style={{ display: 'grid', gap: 14 }}>
              {cats.map(c => (
                <div key={c.name}>
                  <div className="flex justify-between items-center mb-1">
                    <div className="flex items-center gap-2">
                      <span style={{ width: 8, height: 8, borderRadius: 2, background: `oklch(0.72 0.14 ${c.hue})` }}></span>
                      <span className="strong">{c.name}</span>
                      <span className="dim num">— {c.n} лот.</span>
                    </div>
                    <div className="mono dim">{fmtAmount(c.sum)} ₸</div>
                  </div>
                  <div className="bar">
                    <i style={{ width: `${(c.sum / maxC) * 100}%`, background: `oklch(0.72 0.14 ${c.hue})` }}></i>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Attention + Runs */}
      <div className="panels">
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">Требуют внимания</div>
              <div className="card-sub">Отслеживаемые лоты со сменой статуса или новыми документами</div>
            </div>
            <button className="btn sm ghost" onClick={() => goto('actual')}>Все актуальные <Icon name="arrowRight" size={12} /></button>
          </div>
          <div>
            {attention.map((l, i) => (
              <a key={l.id} className="att-row" onClick={() => goto('lot', l)} style={{
                display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 12, alignItems: 'center',
                padding: '14px 20px', borderBottom: i === attention.length - 1 ? 0 : '1px solid var(--bd-1)', cursor: 'pointer',
              }}>
                <div style={{ minWidth: 0 }}>
                  <div className="flex items-center gap-2 mb-1">
                    {l.starred && <Icon name="star" size={13} color="var(--warn)" />}
                    <span className="mono dimer" style={{ fontSize: 11 }}>#{l.id}</span>
                    <CatTag cat={l.cat} />
                  </div>
                  <div className="truncate strong" style={{ fontSize: 13.5 }}>{l.name}</div>
                  <div className="truncate dim" style={{ fontSize: 12 }}>{l.customerShort}</div>
                </div>
                <StatusPill code={l.status} mini />
                <div className="mono strong" style={{ fontSize: 13 }}>{fmtAmount(l.amount)} ₸</div>
              </a>
            ))}
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">Последние прогоны</div>
              <div className="card-sub">Журнал scrape-runs</div>
            </div>
          </div>
          <div style={{ overflow: 'auto' }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Когда</th>
                  <th>Preset</th>
                  <th className="num">listing</th>
                  <th className="num">new</th>
                  <th className="num">upd</th>
                  <th className="num">docs</th>
                  <th className="num">err</th>
                </tr>
              </thead>
              <tbody>
                {RUNS.map((r, i) => (
                  <tr key={i}>
                    <td className="mono" style={{ fontSize: 12.5 }}>{r.at}</td>
                    <td><span className="badge muted">{r.preset}</span></td>
                    <td className="num mono">{r.listing}</td>
                    <td className="num mono"><span className="strong">{r.fresh || '—'}</span></td>
                    <td className="num mono">{r.updated || '—'}</td>
                    <td className="num mono">{r.docs || '—'}</td>
                    <td className="num">{r.err > 0 ? <span className="badge bad"><span className="dot"></span>{r.err}</span> : <span className="dim">0</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};

window.Dashboard = Dashboard;
