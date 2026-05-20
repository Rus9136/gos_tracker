// Global keyboard shortcuts.
// Cmd/Ctrl+K → фокус в глобальный поиск.
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    const input = document.querySelector(".global-search input");
    if (input) {
      e.preventDefault();
      input.focus();
      input.select();
    }
  }
});
