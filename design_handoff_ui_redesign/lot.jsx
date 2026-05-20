// Lot card — detail view with two columns (data + chat/LLM)

const LotPage = ({ lot, goto }) => {
  const [chatInput, setChatInput] = useState('');
  const [chat, setChat] = useState([
    { role: 'user', text: 'Что именно нужно поддерживать?' },
    { role: 'ai',   text: 'По ТЗ — это сопровождение АСППМ (автоматизированной системы психолого-педагогического мониторинга). Заказчик ожидает: консультации пользователей в часы работы 9:00–18:00, ведение списка ошибок, исправление багов в течение 48 часов, обновление справочников и доработка 3 форм отчётности.' },
  ]);
  const [llmRunning, setLlmRunning] = useState(false);
  const [analysis, setAnalysis] = useState(LLM_ANALYSIS);

  const docs = [
    { name: 'Конкурсная документация', tz: 'Нет', size: '125.1 KB', when: '19.05.2026 11:32' },
    { name: 'Приложение 16 (Техническая спецификация закупаемых услуг)', tz: 'Да', size: '81.4 KB', when: '19.05.2026 11:32', primary: true },
    { name: 'Приложение 2 (Соглашение об участии в конкурсе)',           tz: 'Да',  size: '18.4 KB', when: '19.05.2026 11:32' },
    { name: 'Приложение 12 (Сведения о квалификации и критериях)',       tz: 'Да',  size: '24.0 KB', when: '19.05.2026 11:32' },
    { name: 'Приложение 19 (Обеспечение заявки)',                        tz: 'Да',  size: '17.3 KB', when: '19.05.2026 11:32' },
    { name: 'Приложение 5 (Сведения о субподрядчиках)',                  tz: 'Нет', size: '9.2 KB',  when: '19.05.2026 11:32' },
  ];

  const sendChat = () => {
    if (!chatInput.trim()) return;
    setChat(c => [...c, { role: 'user', text: chatInput }]);
    setChatInput('');
    setTimeout(() => {
      setChat(c => [...c, { role: 'ai', text: '…' }]);
    }, 150);
  };

  return (
    <div data-screen-label="03 Карточка лота">
      {/* Header */}
      <div className="crumb">
        <a onClick={() => goto('actual')} style={{ cursor:'pointer' }}><Icon name="arrowLeft" size={12} /> Актуальные</a>
        <span className="sep">/</span><span>Прошедшие</span>
        <span className="sep">/</span><span className="strong mono">#{lot.id}</span>
      </div>
      <div className="flex items-start justify-between gap-4 mb-4">
        <div style={{ minWidth: 0 }}>
          <h1 className="page-title" style={{ fontSize: 24, marginBottom: 6, textWrap: 'pretty' }}>{lot.name}</h1>
          <div className="meta">
            <span className="mono">Лот #{lot.id}</span>
            <span className="sep"></span>
            <span className="mono">Объявление #{lot.anno}</span>
            <span className="sep"></span>
            <a className="flex items-center gap-1" style={{ color: 'var(--accent-hi)' }}>открыть на goszakup.gov.kz <Icon name="external" size={11} /></a>
            <span className="sep"></span>
            <span>замечен {lot.seen} • обновлён {lot.updated}</span>
          </div>
        </div>
        <div className="flex gap-2 items-center">
          <button className="btn"><Icon name="star" size={13} color={lot.starred?'var(--warn)':'currentColor'} /> {lot.starred ? 'В избранном' : 'В избранное'}</button>
          <button className="btn"><Icon name="eye" size={13} /> Следить</button>
          <button className="btn"><Icon name="copy" size={13} /> Скопировать БИН</button>
          <button className="btn primary"><Icon name="external" size={13} /> На goszakup</button>
        </div>
      </div>

      {/* Status timeline (horizontal) */}
      <div className="card mb-4">
        <div className="card-body" style={{ padding: 16 }}>
          <HorizontalStatus current={lot.status} history={lot.history} />
        </div>
      </div>

      <div className="split">
        {/* LEFT */}
        <div className="col gap-3">
          {/* Key facts */}
          <div className="card">
            <div className="card-head">
              <div className="card-title">Сведения по лоту</div>
              <StatusPill code={lot.status} />
            </div>
            <div className="card-body" style={{ paddingTop: 0, paddingBottom: 0 }}>
              <dl className="dl">
                <dt>IT-категория</dt><dd><CatTag cat={lot.cat} /></dd>
                <dt>Тип разработки (LLM)</dt><dd><DevTag cat={lot.devCat} /></dd>
                <dt>ENSTRU</dt><dd className="dim">{lot.enstru}</dd>
                <dt>Доп. характеристика</dt><dd>{lot.addChar}</dd>
                <dt>Плановая сумма</dt><dd className="mono strong" style={{ fontSize: 16 }}>{fmtAmount(lot.amount)} ₸</dd>
                <dt>Цена за ед.</dt><dd className="mono">{fmtAmount(lot.unit)} ₸</dd>
                <dt>Количество</dt><dd className="mono">{lot.qty}</dd>
                <dt>Способ закупки</dt><dd>{lot.method}</dd>
                <dt>Регион (КАТО)</dt><dd>{lot.region === '—' ? <span className="dimer">— (None)</span> : <span>{lot.region} <span className="mono dimer">{lot.kato}</span></span>}</dd>
              </dl>
            </div>
          </div>

          {/* Documents */}
          <div className="card">
            <div className="card-head">
              <div>
                <div className="card-title">Документация <span className="dim">({docs.length})</span></div>
                <div className="card-sub">Серым — не отнесены к ТЗ • голубым — выгружены и проиндексированы</div>
              </div>
              <div className="flex gap-2">
                <button className="btn sm"><Icon name="refresh" size={12} /> Обновить</button>
                <button className="btn sm"><Icon name="download" size={12} /> Скачать всё</button>
              </div>
            </div>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Документ</th>
                  <th style={{ width: 70 }}>ТЗ</th>
                  <th className="num" style={{ width: 80 }}>Размер</th>
                  <th style={{ width: 140 }}>Скачано</th>
                  <th style={{ width: 90 }}></th>
                </tr>
              </thead>
              <tbody>
                {docs.map(d => (
                  <tr key={d.name}>
                    <td>
                      <div className="flex items-center gap-2">
                        <Icon name="file" size={14} color={d.tz==='Да' ? 'var(--accent-hi)' : 'var(--fg-3)'} />
                        <span className={d.primary ? 'strong' : ''}>{d.name}</span>
                        {d.primary && <span className="badge accent">основное ТЗ</span>}
                      </div>
                    </td>
                    <td>{d.tz === 'Да' ? <span className="badge ok"><Icon name="check" size={10} /> Да</span> : <span className="badge muted">Нет</span>}</td>
                    <td className="num mono dim">{d.size}</td>
                    <td className="mono dim">{d.when}</td>
                    <td>
                      <button className="btn sm">скачать <Icon name="external" size={11} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Status history */}
          <div className="card">
            <div className="card-head">
              <div className="card-title">Хронология статусов</div>
            </div>
            <div className="card-body">
              <div className="timeline">
                {lot.history.map((h, i) => (
                  <div key={i} className={`ev ${i === lot.history.length - 1 ? 'current' : ''}`}>
                    <div className="t mono">{h.at}</div>
                    <div className="label">{STATUS[h.code]?.label || h.code} <span className="dim mono">· {h.code}</span></div>
                  </div>
                ))}
                <div className="ev" style={{ opacity: 0.55 }}>
                  <div className="t mono">— ожидание —</div>
                  <div className="label dim">Опубликован (приём заявок) <span className="dim mono">· 220</span></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* RIGHT — sticky panel */}
        <div className="col gap-3" style={{ position: 'sticky', top: 80 }}>
          {/* Customer card */}
          <div className="card">
            <div className="card-head"><div className="card-title">Заказчик</div></div>
            <div className="card-body">
              <div className="flex items-start gap-2 mb-2">
                <div className="avatar" style={{ background: `oklch(0.32 0.08 ${(lot.bin||'0').slice(-3)})`, color: 'var(--fg-0)' }}>
                  {(lot.customerShort || '').slice(0,1)}
                </div>
                <div style={{ minWidth: 0 }}>
                  <div className="strong" style={{ fontSize: 13.5 }}>{lot.customer}</div>
                  <div className="mt-1 mono dim" style={{ fontSize: 11.5 }}>БИН: {lot.bin || '—'}</div>
                </div>
              </div>
              <div className="flex gap-1 mt-2">
                <button className="btn sm">Все лоты заказчика</button>
                <button className="btn sm ghost"><Icon name="copy" size={11} /></button>
              </div>
            </div>
          </div>

          {/* LLM analysis */}
          <div className="card" style={{ borderColor: 'color-mix(in oklch, var(--accent) 30%, var(--bd-1))' }}>
            <div className="card-head" style={{ background: 'color-mix(in oklch, var(--accent) 10%, transparent)' }}>
              <div className="flex items-center gap-2">
                <Icon name="sparkle" size={14} color="var(--accent-hi)" />
                <div className="card-title" style={{ color: 'var(--accent-hi)' }}>LLM-анализ ТЗ</div>
                <span className="badge accent">{analysis.confidence}</span>
              </div>
              <button className="btn sm" onClick={() => setLlmRunning(true)}>
                {llmRunning ? '…идёт' : 'Перезапустить'}
              </button>
            </div>
            <div className="card-body">
              <div className="uc mb-1">Резюме ТЗ</div>
              <div className="mb-3" style={{ fontSize: 13, color: 'var(--fg-1)', textWrap: 'pretty' }}>{analysis.tz_summary}</div>

              <div className="uc mb-1">Стек</div>
              <div className="flex gap-1 mb-3" style={{ flexWrap: 'wrap' }}>
                {analysis.tech_stack.map(t => <span key={t} className="tag mono" style={{ fontSize: 11 }}>{t}</span>)}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <div className="uc mb-1">Соло-разработчик</div>
                  {analysis.solo_feasible
                    ? <span className="badge ok"><Icon name="check" size={10} /> справится</span>
                    : <span className="badge warn"><span className="dot"></span>команда</span>}
                </div>
                <div>
                  <div className="uc mb-1">Риск заточки</div>
                  <RiskBadge level={analysis.vendor_lock_risk} />
                </div>
              </div>

              {analysis.vendor_lock_reasons?.length > 0 && (
                <div className="mt-3">
                  <div className="uc mb-1">Почему</div>
                  <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12.5, color: 'var(--fg-1)' }}>
                    {analysis.vendor_lock_reasons.map(r => <li key={r} style={{ marginBottom: 4 }}>{r}</li>)}
                  </ul>
                </div>
              )}
            </div>
            <div className="card-foot mono" style={{ fontSize: 11 }}>
              {analysis.version} • проанализирован {analysis.analyzed_at}
            </div>
          </div>

          {/* Chat */}
          <div className="card">
            <div className="card-head">
              <div>
                <div className="card-title flex items-center gap-2"><Icon name="sparkle" size={13} /> Чат по ТЗ</div>
                <div className="card-sub">В контекст уходит спецификация и поля объявления. История — в браузере.</div>
              </div>
              <button className="btn sm ghost">Очистить</button>
            </div>
            <div className="card-body">
              <div className="chat" style={{ maxHeight: 220, overflowY: 'auto', paddingRight: 4 }}>
                {chat.map((m, i) => (
                  <div key={i} className={`bubble ${m.role}`}>
                    <div className="role">{m.role === 'user' ? 'вы' : 'AI'}</div>
                    {m.text}
                  </div>
                ))}
              </div>
              <div className="mt-3" style={{ position: 'relative' }}>
                <textarea className="input w-full" rows={2} placeholder="Спроси что-нибудь по ТЗ (Cmd/Ctrl+Enter — отправить)"
                  value={chatInput} onChange={e => setChatInput(e.target.value)}
                  onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') sendChat(); }} />
                <button className="btn primary" style={{ position: 'absolute', right: 6, bottom: 6 }} onClick={sendChat}>
                  <Icon name="send" size={13} /> Отправить
                </button>
              </div>
              <div className="flex gap-1 mt-2" style={{ flexWrap: 'wrap' }}>
                <button className="chip" onClick={() => setChatInput('Какие требования к опыту участника?')}>Требования к опыту</button>
                <button className="chip" onClick={() => setChatInput('Какой срок выполнения?')}>Срок</button>
                <button className="chip" onClick={() => setChatInput('Что в обеспечении заявки?')}>Обеспечение</button>
                <button className="chip" onClick={() => setChatInput('Есть ли скрытая заточка?')}>Заточка?</button>
              </div>
            </div>
          </div>

          {/* Contact */}
          {lot.contact && (
            <div className="card">
              <div className="card-head"><div className="card-title">Контактное лицо</div></div>
              <div className="card-body">
                <div className="strong">{lot.contact.name}</div>
                <div className="dim mb-1" style={{ fontSize: 12 }}>{lot.contact.role}</div>
                <a className="mono" style={{ color: 'var(--accent-hi)', fontSize: 12 }}>{lot.contact.email}</a>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// Compact horizontal status flow
const HorizontalStatus = ({ current, history }) => {
  const flow = [210, 220, 250, 320, 325, 330, 360];
  const ix = flow.indexOf(current);
  const reached = (i) => i <= ix;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0, position: 'relative', overflowX: 'auto' }}>
      {flow.map((code, i) => {
        const s = STATUS[code];
        return (
          <React.Fragment key={code}>
            <div className="flex items-center gap-2" style={{ flex: '0 0 auto', padding: '0 4px' }}>
              <div style={{
                width: 22, height: 22, borderRadius: '50%',
                display: 'grid', placeItems: 'center',
                background: reached(i) ? 'var(--accent)' : 'var(--bg-2)',
                color: reached(i) ? 'var(--accent-fg)' : 'var(--fg-3)',
                border: i === ix ? '0' : '1px solid var(--bd-1)',
                boxShadow: i === ix ? '0 0 0 4px color-mix(in oklch, var(--accent) 25%, transparent)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700,
              }}>{reached(i) ? <Icon name="check" size={11} /> : i + 1}</div>
              <div className="col" style={{ minWidth: 0 }}>
                <div className="mono dimer" style={{ fontSize: 10 }}>{code}</div>
                <div style={{ fontSize: 12, fontWeight: i === ix ? 600 : 500, color: reached(i) ? 'var(--fg-0)' : 'var(--fg-3)', whiteSpace: 'nowrap' }}>{s.label}</div>
              </div>
            </div>
            {i < flow.length - 1 && (
              <div style={{ flex: 1, minWidth: 24, height: 2, margin: '0 8px',
                background: reached(i + 1) ? 'var(--accent)' : 'var(--bd-1)', borderRadius: 1 }}></div>
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
};

window.LotPage = LotPage;
