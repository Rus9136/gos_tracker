// Main app — router, top nav, tweaks panel

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "dark",
  "accent_hue": 155,
  "density": "cozy",
  "list_layout": "compact"
}/*EDITMODE-END*/;

const HUE_OPTIONS = [
  { hue: 255, name: 'Индиго'  },
  { hue: 215, name: 'Сапфир'  },
  { hue: 200, name: 'Циан'    },
  { hue: 155, name: 'Изумруд' },
  { hue: 75,  name: 'Янтарь'  },
  { hue: 25,  name: 'Кармин'  },
  { hue: 310, name: 'Пурпур'  },
];

function App() {
  const [route, setRoute] = useState('dashboard');
  const [lot, setLot] = useState(LOTS[0]);

  const goto = (r, payload) => {
    if (r === 'lot' && payload) setLot(payload);
    setRoute(r);
    window.scrollTo({ top: 0, behavior: 'auto' });
  };

  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  React.useEffect(() => {
    document.documentElement.dataset.theme = t.theme;
    document.documentElement.dataset.density = t.density;
    document.documentElement.style.setProperty('--accent-h', String(t.accent_hue));
  }, [t]);

  const NAV_MAIN = [
    { id: 'dashboard', label: 'Дашборд',          ic: 'grid',    count: null },
    { id: 'actual',    label: 'Актуальные',       ic: 'flame',   count: 200  },
    { id: 'past',      label: 'Прошедшие',        ic: 'clock',   count: 2582 },
    { id: 'customers', label: 'Заказчики',        ic: 'list',    count: 439  },
  ];
  const NAV_SYS = [
    { id: 'presets',   label: "Preset'ы",         ic: 'star',    count: 20   },
    { id: 'runs',      label: 'Прогоны',          ic: 'refresh', count: null },
    { id: 'ingest',    label: 'Догрузка по БИН',  ic: 'download',count: null },
  ];

  const isActive = (id) =>
    route === id ||
    (route === 'lot' && id === 'actual') ||
    (route === 'customer' && id === 'customers');

  const NavLink = ({ n }) => (
    <a className={isActive(n.id) ? 'active' : ''} onClick={() => goto(n.id)}>
      <span className="nav-ic"><Icon name={n.ic} size={15} /></span>
      <span className="nav-label">{n.label}</span>
      {n.count != null && <span className="nav-count">{n.count}</span>}
    </a>
  );

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">GZ</div>
          <div className="brand-name">Goszakup<br/>Tracker</div>
          <div className="brand-ver mono">v0.2</div>
        </div>

        <nav className="nav">
          <div className="nav-group-label">Тендеры</div>
          {NAV_MAIN.map(n => <NavLink key={n.id} n={n} />)}
          <div className="nav-group-label">Система</div>
          {NAV_SYS.map(n => <NavLink key={n.id} n={n} />)}
        </nav>

        <div className="sidebar-spacer" />

        <div className="sidebar-user">
          <div className="avatar" style={{ background: 'var(--accent-tint)', color: 'var(--accent-hi)' }}>Р</div>
          <div className="user-meta">
            <div className="user-name">Руслан</div>
            <div className="user-role">admin · GZ_NO_AUTH</div>
          </div>
        </div>
      </aside>

      <div className="col" style={{ minWidth: 0 }}>
        <header className="topbar">
          <div className="topbar-inner">
            <div className="global-search">
              <span className="ic"><Icon name="search" size={14} /></span>
              <input placeholder="Поиск по лотам, БИН, заказчикам, документам…" />
              <span className="kbd">⌘K</span>
            </div>
            <div className="topbar-right">
              <button className="btn sm"><Icon name="refresh" size={12} /> Прогон</button>
              <button className="icon-btn" title="Уведомления"><Icon name="bell" size={15} /></button>
              <button className="icon-btn" title="Настройки"><Icon name="settings" size={15} /></button>
            </div>
          </div>
        </header>

        <main className="main">
          {route === 'dashboard' && <Dashboard goto={goto} />}
          {route === 'actual'    && <Listing  goto={goto} tweaks={t} />}
          {route === 'past'      && <Listing  goto={goto} tweaks={t} />}
          {route === 'lot'       && <LotPage  lot={lot} goto={goto} />}
          {route === 'customers' && <Customers goto={goto} />}
          {(route === 'presets' || route === 'runs' || route === 'ingest') && (
            <Stub title={[...NAV_MAIN, ...NAV_SYS].find(n => n.id === route).label} goto={goto} />
          )}
        </main>
      </div>

      <TweaksPanel title="Tweaks">
        <TweakSection label="Внешний вид">
          <TweakRadio label="Тема" value={t.theme} onChange={v => setTweak('theme', v)}
            options={[{value:'dark',label:'Тёмная'},{value:'light',label:'Светлая'}]} />
          <TweakRadio label="Плотность" value={t.density} onChange={v => setTweak('density', v)}
            options={[
              {value:'compact', label:'⊟'},
              {value:'cozy',    label:'≣'},
              {value:'spacious',label:'☰'},
            ]} />
          <AccentPicker value={t.accent_hue} onChange={v => setTweak('accent_hue', v)} />
        </TweakSection>
        <TweakSection label="Список лотов">
          <TweakRadio label="Вид" value={t.list_layout} onChange={v => setTweak('list_layout', v)}
            options={[{value:'compact',label:'Таблица'},{value:'cards',label:'Карточки'}]} />
        </TweakSection>
      </TweaksPanel>
    </div>
  );
}

function AccentPicker({ value, onChange }) {
  return (
    <div className="twk-row" style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'stretch' }}>
      <div className="twk-lbl" style={{ width: 'auto' }}><span>Акцент</span></div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 6 }}>
        {HUE_OPTIONS.map(o => (
          <button key={o.hue} title={o.name}
            onClick={() => onChange(o.hue)}
            style={{
              width: '100%', aspectRatio: '1 / 1',
              border: value === o.hue ? '2px solid #fff' : '1px solid rgba(255,255,255,0.15)',
              background: `oklch(0.68 0.16 ${o.hue})`,
              borderRadius: 8, cursor: 'pointer', padding: 0,
              boxShadow: value === o.hue ? '0 0 0 2px oklch(0.68 0.16 ' + o.hue + ' / 0.4)' : 'none',
            }} />
        ))}
      </div>
    </div>
  );
}

function Stub({ title, goto }) {
  return (
    <div>
      <div className="page-head">
        <h1 className="page-title">{title}</h1>
      </div>
      <div className="card" style={{ padding: 40, textAlign: 'center' }}>
        <div style={{ width: 64, height: 64, margin: '0 auto 12px', borderRadius: 16, background: 'var(--bg-2)', display: 'grid', placeItems: 'center' }}>
          <Icon name="sparkle" size={28} color="var(--accent-hi)" />
        </div>
        <div className="strong" style={{ fontSize: 16 }}>Экран «{title}» — в дизайн-итерации</div>
        <div className="dim mt-1">В этом редизайне сосредоточился на 4 главных экранах. Если нужно — добавлю.</div>
        <div className="mt-3 flex gap-2" style={{ justifyContent: 'center' }}>
          <button className="btn" onClick={() => goto('dashboard')}>На дашборд</button>
          <button className="btn primary" onClick={() => goto('actual')}>Актуальные тендеры</button>
        </div>
      </div>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
