// Обратный отсчёт до окончания приёма заявок. Дедлайн отдаётся в data-deadline
// как ISO-8601 со смещением — браузер сам переводит в местное время. Тикает раз
// в секунду; элементов на странице немного (≤50 строк), это дёшево. Классы
// urgency меняют цвет: <24ч — near, <6ч — soon, истёк — expired. Подключается
// глобально в _layout.html, поэтому работает на всех страницах со спанами
// .gz-countdown (списки лотов, /matched).
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
