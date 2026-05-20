// Actual tenders list — primary working screen
const { useState, useMemo } = React;

const Listing = ({ goto, tweaks }) => {
  const [q, setQ] = useState('');
  const [region, setRegion] = useState('все');
  const [cat, setCat] = useState('все');
  const [dev, setDev] = useState('все');
  const [risk, setRisk] = useState('все');
  const [amountFrom, setAmountFrom] = useState('');
  const [amountTo, setAmountTo] = useState('');
  const [sort, setSort] = useState('seen-desc');
  const [view, setView] = useState(tweaks?.list_layout || 'compact'); // compact|cards
  const [activePreset, setActivePreset] = useState(null);
  const [selected, setSelected] = useState(new Set());

  const filtered = useMemo(() => {
    let l = LOTS.filter(l => {
      if (q && !(l.name.toLowerCase().includes(q.toLowerCase()) || l.customer.toLowerCase().includes(q.toLowerCase()) || l.id.includes(q))) return false;
      if (region !== 'все' && l.region !== region) return false;
      if (cat !== 'все' && l.cat !== cat) return false;
      if (dev !== 'все' && l.devCat !== dev) return false;
      if (risk !== 'все' && l.vendorLock !== risk) return false;
      if (amountFrom && l.amount < +amountFrom) return false;
      if (amountTo && l.amount > +amountTo) return false;
      return true;
    });
    const sorters = {
      'seen-desc': (a,b) => b.lotPk - a.lotPk,
      'seen-asc':  (a,b) => a.lotPk - b.lotPk,
      'amt-desc':  (a,b) => b.amount - a.amount,
      'amt-asc':   (a,b) => a.amount - b.amount,
    };
    l.sort(sorters[sort]);
    return l;
  }, [q, region, cat, dev, risk, amountFrom, amountTo, sort]);

  const totalSum = filtered.reduce((s, l) => s + l.amount, 0);
  const activeFilters = [
    region !== 'все' && { k: 'region', v: region, label: `Регион: ${region}` },
    cat !== 'все' && { k: 'cat', v: cat, label: `Категория: ${cat}` },
    dev !== 'все' && { k: 'dev', v: dev, label: `Разработка: ${dev}` },
    risk !== 'все' && { k: 'risk', v: risk, label: `Риск: ${risk}` },
    amountFrom && { k: 'af', v: '', label: `От ${fmtCompact(+amountFrom)}` },
    amountTo && { k: 'at', v: '', label: `До ${fmtCompact(+amountTo)}` },
  ].filter(Boolean);

  const reset = () => {
    setQ(''); setRegion('все'); setCat('все'); setDev('все'); setRisk('все'); setAmountFrom(''); setAmountTo(''); setActivePreset(null);
  };

  const toggleSelect = (id) => {
    setSelected(s => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  return (
    <div data-screen-label="02 Актуальные тендеры">
      <div className="page-head">
        <div>
          <div className="crumb"><a>Тендеры</a><span className="sep">/</span><span className="strong">Актуальные</span></div>
          <h1 className="page-title">Актуальные тендеры</h1>
        </div>
        <div className="flex gap-2 items-center">
          <span className="hint mono">найдено {filtered.length} • ∑ {fmtAmount(totalSum)} ₸</span>
          <div className="seg">
            <button className={view==='compact'?'active':''} onClick={() => setView('compact')} title="Таблица"><Icon name="list" size={13} /></button>
            <button className={view==='cards'?'active':''} onClick={() => setView('cards')} title="Карточки"><Icon name="grid" size={13} /></button>
          </div>
          <button className="btn"><Icon name="download" /> Экспорт</button>
        </div>
      </div>

      {/* Preset chips */}
      <div className="flex items-center gap-2 mb-3" style={{ flexWrap: 'wrap' }}>
        <span className="uc">Preset'ы</span>
        {PRESETS.map(p => (
          <button key={p.name} className={`chip ${activePreset === p.name ? 'active' : ''}`}
            onClick={() => { setActivePreset(activePreset === p.name ? null : p.name); }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: `oklch(0.72 0.14 ${p.hue})` }}></span>
            {p.name}
            <span className="dim mono">{p.n}</span>
          </button>
        ))}
        <button className="chip"><Icon name="plus" size={12} /> сохранить текущий</button>
      </div>

      {/* Filters */}
      <div className="card mb-3">
        <div className="card-body" style={{ padding: 14 }}>
          <div className="filterbar">
            <div className="field">
              <label>Поиск</label>
              <div style={{ position: 'relative' }}>
                <input className="input" placeholder="название, заказчик, лот, БИН" value={q} onChange={e => setQ(e.target.value)} style={{ paddingLeft: 32, width: '100%' }} />
                <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--fg-3)' }}><Icon name="search" size={14} /></span>
              </div>
            </div>
            <div className="field">
              <label>Регион</label>
              <select className="select" value={region} onChange={e => setRegion(e.target.value)}>{REGIONS.map(r => <option key={r}>{r}</option>)}</select>
            </div>
            <div className="field">
              <label>IT-категория</label>
              <select className="select" value={cat} onChange={e => setCat(e.target.value)}>{ALL_IT_CATS.map(r => <option key={r}>{r}</option>)}</select>
            </div>
            <div className="field">
              <label>Тип разработки</label>
              <select className="select" value={dev} onChange={e => setDev(e.target.value)}>{ALL_DEV_CATS.map(r => <option key={r}>{r}</option>)}</select>
            </div>
            <div className="field">
              <label>Риск заточки</label>
              <select className="select" value={risk} onChange={e => setRisk(e.target.value)}>{RISK.map(r => <option key={r}>{r}</option>)}</select>
            </div>
            <button className="btn primary"><Icon name="filter" /> Применить</button>
          </div>
          <div className="filterbar mt-2" style={{ gridTemplateColumns: 'repeat(4, 1fr) 1fr auto' }}>
            <div className="field">
              <label>Сумма от, ₸</label>
              <input className="input" placeholder="0" value={amountFrom} onChange={e => setAmountFrom(e.target.value)} />
            </div>
            <div className="field">
              <label>Сумма до, ₸</label>
              <input className="input" placeholder="—" value={amountTo} onChange={e => setAmountTo(e.target.value)} />
            </div>
            <div className="field">
              <label>Сортировка</label>
              <select className="select" value={sort} onChange={e => setSort(e.target.value)}>
                <option value="seen-desc">Новые первыми</option>
                <option value="seen-asc">Старые первыми</option>
                <option value="amt-desc">Сумма ↓</option>
                <option value="amt-asc">Сумма ↑</option>
              </select>
            </div>
            <div className="field">
              <label>Статус</label>
              <div className="flex gap-1 items-center" style={{ height: 36 }}>
                <span className="badge ok"><span className="dot"></span>опубликованные</span>
                <span className="chip"><Icon name="plus" size={11} /></span>
              </div>
            </div>
            <div></div>
            <div className="flex items-end">
              <button className="btn ghost sm" onClick={reset}>Сбросить</button>
            </div>
          </div>
          {activeFilters.length > 0 && (
            <div className="flex items-center gap-2 mt-2" style={{ flexWrap: 'wrap' }}>
              <span className="uc">Активные</span>
              {activeFilters.map(f => (
                <button key={f.k+f.v} className="chip active" onClick={() => {
                  if (f.k === 'region') setRegion('все');
                  if (f.k === 'cat') setCat('все');
                  if (f.k === 'dev') setDev('все');
                  if (f.k === 'risk') setRisk('все');
                  if (f.k === 'af') setAmountFrom('');
                  if (f.k === 'at') setAmountTo('');
                }}>{f.label}<span className="x">✕</span></button>
              ))}
              <button className="btn sm ghost" onClick={reset}>очистить все</button>
            </div>
          )}
        </div>
      </div>

      {/* Bulk action bar */}
      {selected.size > 0 && (
        <div className="card mb-3" style={{ background: 'var(--accent-tint)', borderColor: 'color-mix(in oklch, var(--accent) 35%, transparent)' }}>
          <div className="card-body flex items-center justify-between" style={{ padding: '10px 16px' }}>
            <div className="flex items-center gap-3">
              <span className="strong">Выбрано: {selected.size}</span>
              <button className="btn sm"><Icon name="star" size={12} /> В избранное</button>
              <button className="btn sm"><Icon name="sparkle" size={12} /> Запустить LLM-анализ</button>
              <button className="btn sm"><Icon name="download" size={12} /> Скачать ТЗ ({selected.size})</button>
              <button className="btn sm"><Icon name="external" size={12} /> Сравнить</button>
            </div>
            <button className="btn sm ghost" onClick={() => setSelected(new Set())}>Снять выделение</button>
          </div>
        </div>
      )}

      {/* List */}
      {view === 'compact' ? (
        <div className="card">
          <table className="tbl">
            <thead>
              <tr>
                <th style={{ width: 32 }}>
                  <input type="checkbox" checked={selected.size === filtered.length && filtered.length > 0}
                    onChange={() => setSelected(selected.size === filtered.length ? new Set() : new Set(filtered.map(l => l.id)))} />
                </th>
                <th style={{ width: 28 }}></th>
                <th style={{ width: 130 }}>Лот</th>
                <th>Наименование / Заказчик</th>
                <th style={{ width: 130 }}>Категория</th>
                <th style={{ width: 170 }}>Статус</th>
                <th className="num" style={{ width: 140 }}>Сумма ₸</th>
                <th style={{ width: 110 }}>Регион</th>
                <th style={{ width: 140 }}>Замечен</th>
                <th style={{ width: 120 }}></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(l => (
                <tr key={l.id} className={selected.has(l.id) ? 'is-selected' : ''}>
                  <td onClick={(e)=>{e.stopPropagation(); toggleSelect(l.id);}}>
                    <input type="checkbox" checked={selected.has(l.id)} readOnly />
                  </td>
                  <td>
                    <button className="icon-btn" title={l.starred ? 'В избранном' : 'В избранное'} onClick={(e) => e.stopPropagation()}>
                      <Icon name="star" size={14} color={l.starred ? 'var(--warn)' : 'var(--fg-3)'} />
                    </button>
                  </td>
                  <td className="mono dimer" style={{ fontSize: 11.5 }} onClick={() => goto('lot', l)}>
                    <div className="strong" style={{ color: 'var(--fg-1)' }}>#{l.id}</div>
                    <div style={{ fontSize: 10.5 }}>anno {l.anno}</div>
                  </td>
                  <td onClick={() => goto('lot', l)} style={{ minWidth: 0 }}>
                    <div className="strong clamp-2" style={{ fontSize: 13.5, maxWidth: 520 }}>{l.name}</div>
                    <div className="dim truncate" style={{ fontSize: 12, maxWidth: 520 }}>{l.customerShort}</div>
                    {l.devCat && l.devCat !== 'не-разработка' && (
                      <div className="mt-1 flex gap-1 items-center">
                        <DevTag cat={l.devCat} />
                        {l.vendorLock !== 'low' && <RiskBadge level={l.vendorLock} />}
                        {l.soloOk && <span className="badge accent">соло-ok</span>}
                      </div>
                    )}
                  </td>
                  <td onClick={() => goto('lot', l)}><CatTag cat={l.cat} /></td>
                  <td onClick={() => goto('lot', l)}><StatusPill code={l.status} mini /></td>
                  <td className="num strong mono" onClick={() => goto('lot', l)} style={{ fontSize: 13.5 }}>
                    {fmtAmount(l.amount)}
                  </td>
                  <td onClick={() => goto('lot', l)} className="dim">{l.region === '—' ? <span className="dimer">—</span> : l.region}</td>
                  <td onClick={() => goto('lot', l)} className="mono dim" style={{ fontSize: 12 }}>{l.seen}</td>
                  <td>
                    <div className="row-actions">
                      <button className="icon-btn" title="ТЗ" onClick={(e) => e.stopPropagation()}><Icon name="file" size={13} /></button>
                      <button className="icon-btn" title="LLM-анализ" onClick={(e) => e.stopPropagation()}><Icon name="sparkle" size={13} /></button>
                      <button className="icon-btn" title="goszakup.gov.kz" onClick={(e) => e.stopPropagation()}><Icon name="external" size={13} /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="card-foot flex items-center justify-between">
            <span>Показано <span className="strong">{filtered.length}</span> из 200 актуальных</span>
            <div className="flex items-center gap-2">
              <button className="btn sm"><Icon name="chevronLeft" size={12} /> Назад</button>
              <span className="mono">1 / 4</span>
              <button className="btn sm">Вперёд <Icon name="chevronRight" size={12} /></button>
            </div>
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 12 }}>
          {filtered.map(l => (
            <div key={l.id} className="card" onClick={() => goto('lot', l)} style={{ cursor: 'pointer' }}>
              <div className="card-body" style={{ padding: 16 }}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <StatusPill code={l.status} mini />
                    <CatTag cat={l.cat} />
                  </div>
                  <button className="icon-btn" onClick={(e) => e.stopPropagation()}>
                    <Icon name="star" size={14} color={l.starred ? 'var(--warn)' : 'var(--fg-3)'} />
                  </button>
                </div>
                <div className="mono dimer mb-1" style={{ fontSize: 11 }}>#{l.id}</div>
                <div className="strong clamp-2 mb-1" style={{ fontSize: 14 }}>{l.name}</div>
                <div className="dim truncate mb-2" style={{ fontSize: 12 }}>{l.customerShort}</div>
                <div className="flex gap-1 items-center mb-3" style={{ flexWrap: 'wrap' }}>
                  <DevTag cat={l.devCat} />
                  {l.vendorLock !== 'low' && <RiskBadge level={l.vendorLock} />}
                  {l.soloOk && <span className="badge accent">соло-ok</span>}
                </div>
                <div className="flex items-end justify-between">
                  <div className="num mono strong" style={{ fontSize: 18 }}>{fmtAmount(l.amount)} ₸</div>
                  <div className="dim mono" style={{ fontSize: 11 }}>{l.region === '—' ? '—' : l.region} • {l.seen.split(' ')[0]}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

window.Listing = Listing;
