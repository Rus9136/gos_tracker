// Индикатор идущей дозагрузки на странице отчёта организации. Опрашивает
// /runs/<id>/progress раз в 4с и перезагружает страницу, когда прогон
// закончился, — отчёт считается по БД и сам себя не обновит.
//
// Прогон двухфазный. Пока идёт обход выдачи, знаменателя нет вовсе (goszakup
// отдаёт страницы до первой пустой), поэтому полоска остаётся мерцающей
// заглушкой и показывает только счётчик просмотренных позиций. Как только
// начинается фаза деталей, приезжают done/total — полоска становится
// определённой, рядом появляется оценка остатка.
(function () {
  const box = document.getElementById("run-progress");
  if (!box) return;
  const runId = box.dataset.run;
  const bar = document.getElementById("run-progress-bar");
  const fill = bar.querySelector("i");
  const text = document.getElementById("run-progress-text");
  const POLL_MS = 4000;

  function human(sec) {
    if (sec === null || sec === undefined) return null;
    if (sec < 60) return "меньше минуты";
    const m = Math.round(sec / 60);
    if (m < 60) return `≈${m} мин`;
    const h = Math.floor(m / 60);
    return `≈${h} ч ${m % 60} мин`;
  }

  function render(p) {
    if (p.phase === "details") {
      bar.classList.remove("skel-bar");
      fill.style.width = `${p.percent}%`;
      const eta = human(p.eta_seconds);
      text.textContent =
        `обработано ${p.done} из ${p.total} объявлений (${p.percent}%)` +
        (eta ? ` • осталось ${eta}` : "");
    } else {
      // Фаза listing либо потерянные счётчики: честно не притворяемся, что
      // знаем процент.
      bar.classList.add("skel-bar");
      fill.style.width = "0";
      text.textContent = p.listing_count
        ? `собираем список: просмотрено ${p.listing_count} позиций`
        : "собираем список объявлений…";
    }
  }

  function poll() {
    fetch(`/runs/${runId}/progress`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((p) => {
        if (p.finished) {
          text.textContent = "готово, обновляем отчёт…";
          window.location.reload();
          return;
        }
        render(p);
        setTimeout(poll, POLL_MS);
      })
      .catch(() => {
        // Сеть моргнула или сессия истекла — молча пробуем дальше, реже.
        text.textContent = "статус недоступен, пробуем ещё…";
        setTimeout(poll, POLL_MS * 3);
      });
  }

  poll();
})();
