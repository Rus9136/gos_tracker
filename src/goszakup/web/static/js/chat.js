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
  // Совпадает с _ChatRequest.messages (max_length=40) в web/app.py: сервер
  // отвергает историю длиннее, поэтому режем сами — иначе на 41-м сообщении
  // чат просто начал бы отвечать «Unprocessable Entity».
  const MAX_TURNS = 40;
  const listEl = document.getElementById("chat-list");
  const inputEl = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const clearBtn = document.getElementById("chat-clear");

  let messages = [];
  let pending = false;
  try { messages = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { messages = []; }

  function save() { localStorage.setItem(STORAGE_KEY, JSON.stringify(messages)); }

  function bubble(role, text, extraClass) {
    const el = document.createElement("div");
    el.className = "bubble " + (role === "user" ? "user" : "ai") + (extraClass ? " " + extraClass : "");
    if (role === "error") el.style.borderColor = "var(--bad)";
    const label = document.createElement("div");
    label.className = "role";
    label.textContent = role === "user" ? "вы" : role === "error" ? "ошибка" : "AI";
    el.appendChild(label);
    const txt = document.createElement("div");
    txt.textContent = text;
    el.appendChild(txt);
    return el;
  }

  function render() {
    listEl.innerHTML = "";
    if (!messages.length && !pending) {
      const empty = document.createElement("div");
      empty.className = "chat-empty";
      empty.textContent = "Вопросы по ТЗ — сроки, требования, обеспечение. Ответ считает LLM по тексту документа.";
      listEl.appendChild(empty);
      return;
    }
    for (const m of messages) listEl.appendChild(bubble(m.role, m.content));
    if (pending) listEl.appendChild(bubble("assistant", "думаю…", "typing"));
    listEl.scrollTop = listEl.scrollHeight;
  }

  async function send() {
    if (pending) return;
    const text = inputEl.value.trim();
    if (!text) return;
    messages.push({ role: "user", content: text });
    save();
    inputEl.value = "";
    pending = true;
    render();

    sendBtn.disabled = true;
    // Служебный role='error' на сервер не уходит — он только для UI.
    const payload = messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .slice(-MAX_TURNS);
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
      pending = false;
      save();
      render();
      sendBtn.disabled = false;
      // preventScroll обязателен: панель со своим скролом, и обычный focus()
      // подтягивал бы её к полю ввода — страница дёргалась вниз после
      // каждого ответа.
      inputEl.focus({ preventScroll: true });
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
  root.querySelectorAll("[data-quick]").forEach((el) => {
    el.addEventListener("click", () => {
      inputEl.value = el.dataset.quick;
      inputEl.focus({ preventScroll: true });
    });
  });

  render();
})();
