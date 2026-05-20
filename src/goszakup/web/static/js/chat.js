// Чат по ТЗ. История хранится в localStorage, отправляется на /lot/{id}/chat.
//
// Шаблон ожидает в DOM:
//   #chat-list, #chat-input, #chat-send, #chat-clear
// и data-lot-id на корневом контейнере #chat-root.

(() => {
  const root = document.getElementById("chat-root");
  if (!root) return;
  const LOT_ID = root.dataset.lotId;
  const STORAGE_KEY = "gz.chat." + LOT_ID;
  const listEl = document.getElementById("chat-list");
  const inputEl = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const clearBtn = document.getElementById("chat-clear");

  let messages = [];
  try { messages = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { messages = []; }

  function save() { localStorage.setItem(STORAGE_KEY, JSON.stringify(messages)); }

  function render() {
    listEl.innerHTML = "";
    for (const m of messages) {
      const bubble = document.createElement("div");
      bubble.className = "bubble " + (m.role === "user" ? "user" : m.role === "error" ? "ai" : "ai");
      if (m.role === "error") bubble.style.borderColor = "var(--bad)";
      const role = document.createElement("div");
      role.className = "role";
      role.textContent = m.role === "user" ? "вы" : m.role === "error" ? "ошибка" : "AI";
      bubble.appendChild(role);
      const txt = document.createElement("div");
      txt.textContent = m.content;
      bubble.appendChild(txt);
      listEl.appendChild(bubble);
    }
    listEl.scrollTop = listEl.scrollHeight;
  }

  async function send() {
    const text = inputEl.value.trim();
    if (!text) return;
    messages.push({ role: "user", content: text });
    save(); inputEl.value = ""; render();

    sendBtn.disabled = true;
    const originalLabel = sendBtn.innerHTML;
    sendBtn.textContent = "…";
    // Для отправки на сервер фильтруем служебный role='error' — он только для UI.
    const payload = messages.filter((m) => m.role === "user" || m.role === "assistant");
    try {
      const resp = await fetch(`/lot/${LOT_ID}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: payload }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || resp.statusText);
      messages.push({ role: "assistant", content: data.reply });
    } catch (e) {
      messages.push({ role: "error", content: "Ошибка: " + e.message });
    } finally {
      save(); render();
      sendBtn.disabled = false;
      sendBtn.innerHTML = originalLabel;
      inputEl.focus();
    }
  }

  sendBtn.addEventListener("click", send);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
  });
  clearBtn.addEventListener("click", () => {
    if (!messages.length || confirm("Очистить историю чата?")) {
      messages = []; save(); render();
    }
  });

  // Quick-prompt чипы: data-quick="текст" подставляется в textarea.
  document.querySelectorAll("[data-quick]").forEach((el) => {
    el.addEventListener("click", () => {
      inputEl.value = el.dataset.quick;
      inputEl.focus();
    });
  });

  render();
})();
