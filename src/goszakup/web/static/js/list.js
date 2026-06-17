// Переключатель «в избранное» для строк/карточек. fetch на POST /lot/{id}/star,
// сервер возвращает JSON {starred: bool}. Меняем data-starred и цвет иконки
// без перезагрузки — иначе теряем выделение/скролл/пагинацию.
window.toggleStar = async function (ev, lotId) {
  ev.preventDefault();
  ev.stopPropagation();
  const btn = ev.currentTarget;
  if (btn.dataset.busy === "1") return;
  btn.dataset.busy = "1";
  try {
    const r = await fetch(`/lot/${lotId}/star`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const on = !!data.starred;
    btn.dataset.starred = on ? "1" : "0";
    btn.title = on ? "Убрать из избранного" : "В избранное";
    // SVG звезды — заполняем `currentColor`, цвет управляется через CSS-класс.
    btn.classList.toggle("starred", on);
  } catch (e) {
    console.warn("star toggle failed:", e);
  } finally {
    btn.dataset.busy = "0";
  }
};

// Обратный отсчёт до окончания приёма заявок. Дедлайн хранится в UTC и
// отдаётся в data-deadline как ISO-8601 (+00:00) — браузер сам переведёт в
// местное время пользователя. Тикаем раз в секунду; на коротких списках
// (≤50 строк) это дёшево. Классы urgency меняют цвет: <24ч — near, <6ч — soon,
// истёк — expired.
(function () {
  function fmt(ms) {
    if (ms <= 0) return { text: "истёк", cls: "is-expired" };
    const s = Math.floor(ms / 1000);
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    let text;
    if (d > 0) text = `${d}д ${h}ч`;
    else if (h > 0) text = `${h}ч ${m}м`;
    else text = `${m}м ${sec}с`;
    const cls = ms < 6 * 3600e3 ? "is-soon" : ms < 24 * 3600e3 ? "is-near" : "";
    return { text, cls };
  }

  function tick() {
    const now = Date.now();
    document.querySelectorAll(".gz-countdown[data-deadline]").forEach((el) => {
      const iso = el.dataset.deadline;
      if (!iso) return;
      const t = new Date(iso).getTime();
      if (Number.isNaN(t)) { el.textContent = "—"; return; }
      const { text, cls } = fmt(t - now);
      el.textContent = text;
      el.classList.remove("is-expired", "is-soon", "is-near");
      if (cls) el.classList.add(cls);
    });
  }

  tick();
  setInterval(tick, 1000);
})();

// Alpine.js компоненты для страницы списка лотов.
//
// Регистрируется на событие alpine:init, чтобы дождаться загрузки Alpine'а с CDN.
document.addEventListener("alpine:init", () => {
  // bulkSelect — выделение строк + переключатель compact/cards с localStorage.
  Alpine.data("lotList", (initialView) => ({
    view: initialView || localStorage.getItem("gz.list_view") || "compact",
    ids: new Set(),
    // tooltip для длинных полей таблицы (резюме ТЗ). Один на компонент,
    // содержимое подставляется через showTip.
    tip: { visible: false, text: "", x: 0, y: 0 },
    init() {
      this.$watch("view", (v) => localStorage.setItem("gz.list_view", v));
    },
    has(id) { return this.ids.has(id); },
    toggle(id) {
      if (this.ids.has(id)) this.ids.delete(id);
      else this.ids.add(id);
      // Set не реактивен из коробки — пересоздаём, чтобы Alpine обновил DOM.
      this.ids = new Set(this.ids);
    },
    selectAll(allIds) {
      if (this.ids.size === allIds.length) this.ids = new Set();
      else this.ids = new Set(allIds);
    },
    clear() { this.ids = new Set(); },
    get count() { return this.ids.size; },
    showTip(ev, text) {
      if (!text) return;
      // Показываем только когда контент реально обрезан (clamp-3) — иначе
      // лишний поп-ап на коротких резюме раздражает.
      const target = ev.currentTarget;
      if (target.scrollHeight <= target.clientHeight + 1) return;
      const rect = target.getBoundingClientRect();
      const TIP_MAX_W = 480;
      const TIP_MAX_H = 220;
      const placeAbove = rect.bottom + 8 + TIP_MAX_H > window.innerHeight;
      this.tip = {
        visible: true,
        text,
        x: Math.max(8, Math.min(rect.left, window.innerWidth - TIP_MAX_W - 8)),
        y: placeAbove ? Math.max(8, rect.top - TIP_MAX_H - 8) : rect.bottom + 8,
      };
    },
    hideTip() { this.tip.visible = false; },
  }));
});
