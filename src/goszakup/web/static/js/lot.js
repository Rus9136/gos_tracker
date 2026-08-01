// Мелкая механика карточки лота.
//
//   form[data-busy="текст"] — гасит кнопку на submit (POST'ы «Загрузить
//     документы» / «Переанализировать» идут десятками секунд, без этого
//     жмут повторно и запускают второй заход на goszakup);
//   button[data-copy="значение"] — копирует в буфер и возвращает исходную
//     подпись через пару секунд.

(() => {
  document.querySelectorAll("form[data-busy]").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("button[type=submit], button:not([type])");
      if (!btn) return;
      btn.disabled = true;
      btn.textContent = form.dataset.busy;
    });
  });

  document.querySelectorAll("button[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const original = btn.innerHTML;
      // У иконочной кнопки нет текста — длинная подпись её разорвёт.
      const iconOnly = btn.textContent.trim() === "";
      try {
        await navigator.clipboard.writeText(btn.dataset.copy);
        btn.textContent = iconOnly ? "✓" : "✓ скопировано";
      } catch {
        btn.textContent = iconOnly ? "✕" : "не вышло";
      }
      setTimeout(() => { btn.innerHTML = original; }, 1800);
    });
  });
})();
