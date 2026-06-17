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
