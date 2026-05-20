// Customers / Organizations page

const Customers = ({ goto }) => {
  const [q, setQ] = useState('');
  const [sort, setSort] = useState('sum-desc');
  const filtered = useMemo(() => {
    let l = ORGS.filter(o => {
      if (!q) return true;
      return o.name.toLowerCase().includes(q.toLowerCase()) || (o.bin || '').includes(q);
    });
    const sorters = {
      'sum-desc': (a,b) => b.total - a.total,
      'sum-asc':  (a,b) => a.total - b.total,
      'lots-desc':(a,b) => b.lots - a.lots,
      'act-desc': (a,b) => b.actual - a.actual,
    };
    l.sort(sorters[sort]);
    return l;
  }, [q, sort]);

  const max = Math.max(...filtered.map(o => o.total), 1);

  return (
    <div data-screen-label="04 Заказчики">
      <div className="page-head">
        <div>
          <div className="crumb"><span className="strong">Заказчики и организаторы</span></div>
          <h1 className="page-title">Заказчики и организаторы</h1>
          <div className="page-sub">439 организаций • топ-10 держат 62% бюджета актуальных лотов</div>
        </div>
        <div className="flex gap-2 items-center">
          <button className="btn"><Icon name="download" /> Экспорт CSV</button>
          <button className="btn primary"><Icon name="plus" /> Догрузка по БИН</button>
        </div>
      </div>

      {/* Filters */}
      <div className="card mb-3">
        <div className="card-body" style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr auto', gap: 10, alignItems: 'end', padding: 14 }}>
          <div className="field">
            <label>Поиск (название / БИН)</label>
            <div style={{ position: 'relative' }}>
              <input className="input w-full" placeholder="название организации или 12-значный БИН" value={q} onChange={e => setQ(e.target.value)} style={{ paddingLeft: 32 }} />
              <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--fg-3)' }}><Icon name="search" size={14} /></span>
            </div>
          </div>
          <div className="field">
            <label>Сортировка</label>
            <select className="select" value={sort} onChange={e => setSort(e.target.value)}>
              <option value="sum-desc">По сумме ↓</option>
              <option value="sum-asc">По сумме ↑</option>
              <option value="lots-desc">По числу лотов ↓</option>
              <option value="act-desc">По актуальным ↓</option>
            </select>
          </div>
          <div className="field">
            <label>Только с актуальными</label>
            <div className="seg" style={{ height: 36 }}>
              <button className="active">Все</button>
              <button>Да</button>
              <button>Нет</button>
            </div>
          </div>
          <button className="btn primary">Применить</button>
        </div>
      </div>

      {/* Table */}
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ width: 36 }}>#</th>
              <th>Организация</th>
              <th style={{ width: 140 }}>БИН</th>
              <th className="num" style={{ width: 90 }}>Лотов</th>
              <th className="num" style={{ width: 100 }}>Актуальных</th>
              <th className="num" style={{ width: 320 }}>Сумма всего ₸</th>
              <th style={{ width: 60 }}></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((o, i) => (
              <tr key={o.name} onClick={() => goto('customer', o)}>
                <td className="dim mono" style={{ fontSize: 12 }}>{String(i + 1).padStart(2, '0')}</td>
                <td>
                  <div className="flex items-start gap-2">
                    <div className="avatar" style={{ background: `oklch(0.30 0.06 ${(o.bin||o.name).charCodeAt(o.name.length-1)})` }}>
                      {o.name.slice(0, 1)}
                    </div>
                    <div style={{ minWidth: 0 }}>
                      <div className="strong clamp-2" style={{ fontSize: 13.5, maxWidth: 560 }}>{o.name}</div>
                      {o.actual > 0 && <div className="flex items-center gap-1 mt-1"><span className="pulse"></span><span className="dim" style={{ fontSize: 11.5 }}>{o.actual} актуальн{o.actual === 1 ? 'ый' : o.actual < 5 ? 'ых' : 'ых'} {o.actual === 1 ? 'лот' : 'лотов'}</span></div>}
                    </div>
                  </div>
                </td>
                <td>{o.bin ? <span className="mono" style={{ color: 'var(--accent-hi)', fontSize: 12 }}>{o.bin}</span> : <span className="dimer">—</span>}</td>
                <td className="num mono">{fmtAmount(o.lots)}</td>
                <td className="num">
                  {o.actual > 0
                    ? <span className="badge ok"><span className="dot"></span>{o.actual}</span>
                    : <span className="dimer mono">0</span>}
                </td>
                <td>
                  <div className="flex items-center gap-3 justify-end">
                    <div className="bar" style={{ width: 200, flex: '0 0 200px' }}>
                      <i style={{ width: `${(o.total / max) * 100}%` }}></i>
                    </div>
                    <div className="mono strong" style={{ minWidth: 130, textAlign: 'right' }}>{fmtAmount(o.total)}</div>
                  </div>
                </td>
                <td>
                  <div className="row-actions">
                    <button className="icon-btn" onClick={(e) => e.stopPropagation()} title="Скопировать БИН"><Icon name="copy" size={13} /></button>
                    <button className="icon-btn" onClick={(e) => e.stopPropagation()} title="Открыть"><Icon name="chevronRight" size={13} /></button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="card-foot flex items-center justify-between">
          <span>Показано <span className="strong">{filtered.length}</span> из 439 организаций</span>
          <div className="flex items-center gap-2">
            <button className="btn sm"><Icon name="chevronLeft" size={12} /> Назад</button>
            <span className="mono">1 / 44</span>
            <button className="btn sm">Вперёд <Icon name="chevronRight" size={12} /></button>
          </div>
        </div>
      </div>
    </div>
  );
};

window.Customers = Customers;
