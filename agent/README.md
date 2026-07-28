# Submit-agent («робот» автоподачи)

Отдельный деплой на узле с реальным Tumar CSP + NCALayer. Получает от
Linux-планировщика задание, прогревается до открытия конкурса, исполняет визард
подачи с настоящим Tumar (запечатывание цены) и отчитывается обратно.

Зачем он нужен и как организовать сам узел — см.
`../TENDER_AUTOSUBMIT_PLAN.md` (приложение «организация узла golden-client») и
правило #19 в `../CLAUDE.md`. Здесь — только про сам агент.

> ⚠️ **Статус: каркас.** Вся «сантехника» рабочая (HTTP-сервер, авторизация,
> тайминг «выстрела», отчёт на Linux, управление потоком, обработка ошибок).
> Места с пометкой `TODO(recon)` — UI-селекторы визарда, окно Tumar и
> нюансы NCALayer-логина — заполняются по живому конкурсу (см. «Recon» ниже).

> 📌 **Платформа с 2026-07-28 — macOS** (гайд §6.3: у площадки есть актуальная
> macOS-сборка CryptoSocket `1.0.13.2287`, отдельный Windows-узел не нужен).
> Код агента платформенно-нейтрален, кроме `tumar.py` — он написан на
> `pywinauto` и под macOS нуждается в двойнике на Accessibility API
> (`pyobjc` / `AXUIElement`). Имена контролов окна цены известны из символов
> плагина `libEFCAPI`: `priceInput`, `okButton`, `cryptButtonClicked`.
> Команды ниже даны для Windows-варианта; на macOS это тот же `python -m agent`
> в venv, без `pywinauto` в зависимостях.

## Установка (на узле)

Предполагается, что NCALayer и Tumar CSP уже установлены и вход по ЭЦП проверен
вручную (шаги 1–3 приложения плана).

```powershell
# Python 3.12 + зависимости агента (только httpx/playwright/pywinauto)
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chromium

# Конфиг
copy .env.example .env    # и заполнить значения
```

## Запуск

```powershell
.venv\Scripts\python -m agent
```

Агент поднимет HTTP-сервер на `GZ_AGENT_HOST:GZ_AGENT_PORT`:
- `GET /health` → `{"ok": true}` (проверка живости);
- `POST /run` → принять задание (RunRequest), запустить подачу в фоне, вернуть `202`.

Боевой режим — **НЕ headless** (`GZ_AGENT_HEADLESS=0`): NCALayer и Tumar открывают
нативные окна, нужен живой разблокированный десктоп (autologon, без блокировки
экрана — см. приложение плана, шаг 4).

## Smoke-тест без Linux

```powershell
# health
curl http://127.0.0.1:8799/health
```

## Recon — что снять на живом конкурсе и куда вписать

На реальном (или тестовом) конкурсе пройти подачу руками до подачи, открыв
DevTools→Network с записью, и зафиксировать:

1. **Заголовок окна Tumar** (ввод цены) → `GZ_TUMAR_WINDOW_TITLE` в `.env`
   (посмотреть Spy++/Inspect.exe). Имена контролов поля/кнопки — в `tumar.py`
   (`_PRICE_EDIT_AUTOID`, `_OK_BUTTON_TITLE`).
2. **UI-селекторы визарда** (`wizard.py`, методы с `NotImplementedError`):
   кнопка «Подать заявку», выбор лотов, прикрепление документов, кнопка
   «подписать/зашифровать цену», preview. + получение `appId` из URL черновика.
3. **NCALayer-логин** (`wizard._login`): как всплывает окно выбора ключа/PIN —
   автоматизировать pywinauto.
4. **reCAPTCHA**: подтвердить, что финальный POST проходит без токена (по разбору
   HAR — не enforced); если enforced — добавить пред-solve в `wizard._preview`.

## Связь с Linux

- Linux шлёт `POST {GZ_AGENT_URL}/run` с заголовком `X-Agent-Token`
  (= `GZ_AGENT_TOKEN`).
- Агент по завершении шлёт `POST {GZ_LINUX_INGEST_URL}` (RunResult) с заголовком
  `X-Autosubmit-Token` (= `GZ_AUTOSUBMIT_INGEST_TOKEN`).
- Оба адреса — в приватной сети Tailscale/WireGuard, наружу не публиковать.

## Безопасность

Агент получает расшифрованные секреты клиента (`.p12`/пароль/PIN) в `RunRequest`.
Слушать только на tailnet-интерфейсе; токен `GZ_AGENT_TOKEN` обязателен на проде;
секреты не логировать; диск шифровать (BitLocker). См. гайд §8.
