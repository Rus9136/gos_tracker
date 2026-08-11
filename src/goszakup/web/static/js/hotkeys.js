// Global keyboard shortcuts.
// Cmd/Ctrl+K → фокус в глобальный поиск.
// Esc → закрыть открытый редактор строки (details.row-edit): панель
// перекрыта затемнением, повторно кликнуть по summary нельзя.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document
      .querySelectorAll("details.row-edit[open]")
      .forEach((d) => d.removeAttribute("open"));
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    const input = document.querySelector(".global-search input");
    if (input) {
      e.preventDefault();
      input.focus();
      input.select();
    }
  }
});
