# Программное подписание тендерных заявок через ЭЦП (НУЦ РК)

> Технический документ для проекта `goszakup` (gos_tracker): автоматическая подача
> тендерных заявок с подписью ЭЦП-ключом клиента в момент открытия приёма заявок.
>
> **Стек проекта:** Python 3.11+, FastAPI, SQLAlchemy 2.0, **Dramatiq + Redis**, httpx, typer.
> **Статус:** проектный гайд (в `goszakup` пока нет кода ЭЦП).
> **Источник паттернов подписи:** рабочая реализация GOST/CMS в проекте `kz-docs-saas`
> (`src/modules/signing/`), проверенная в проде с сертификатами НУЦ РК.

---

## 1. Постановка задачи

**Что хочет бизнес:**
1. Есть база тендеров: у каждого известны `time_open` (начало приёма заявок) и `time_close`.
2. Клиент заранее загружает свой **ЭЦП-ключ (`.p12` / `.pfx`)** и **пароль** к нему.
3. В момент `time_open` система автоматически:
   - формирует заявку (payload по формату площадки goszakup.kz),
   - **подписывает её ЭЦП-ключом клиента на сервере** (без участия человека),
   - отправляет в тендерную площадку.

**Ключевое отличие от `kz-docs-saas`:** там ключ всегда остаётся у пользователя (eGov Mobile /
NCALayer на его машине), а сервер принимает уже готовую подпись. Здесь наоборот — **клиент
осознанно передаёт нам ключ и пароль**, и подпись формируется на сервере. Это меняет и
архитектуру, и требования к безопасности (раздел 8).

---

## 2. Что такое ЭЦП НУЦ РК (необходимый минимум)

### 2.1. Формат ключа
НУЦ РК (Национальный удостоверяющий центр РК, `pki.gov.kz`) выдаёт пользователю **контейнер
PKCS#12** — файл `.p12` (иногда `.pfx`), защищённый паролем. Внутри:
- **закрытый ключ** (private key),
- **сертификат** владельца (X.509) с цепочкой до корня НУЦ РК.

У одного пользователя обычно **два ключа** (по имени файла видно назначение):

| Префикс файла | Назначение | Использовать для подписи заявки? |
|---|---|---|
| `AUTH_RSA...` / `AUTH_GOST...` | аутентификация (логин на портал) | ❌ нет |
| `RSA...` / `GOST...` / `GOST512_...` | **подпись (digital signature)** | ✅ **да** |

> ⚠️ Для подписи документов нужен именно **signing-ключ** (`RSA*`/`GOST*`), а не `AUTH_*`.
> Если подписать `AUTH`-ключом, площадка отклонит заявку (неверный keyUsage).

### 2.2. Алгоритмы
НУЦ РК сейчас выдаёт **два семейства**:
- **RSA** — старые/совместимые ключи (`RSA256...`). Хэш SHA-256, подпись RSA.
- **ГОСТ** — новые ключи (`GOST512_...`): подпись **ГОСТ Р 34.10-2015**, хэш **ГОСТ Р 34.11-2012
  (Streebog-256/512)**.

> С 2024 НУЦ РК активно переводит всех на ГОСТ-ключи. Поэтому **обязательна поддержка ГОСТ** —
> обычный OpenSSL «из коробки» ГОСТ не умеет, нужен GOST engine/provider (раздел 4).

### 2.3. Формат подписи
Площадки в РК принимают подпись в одном из видов (**уточнять по API конкретной площадки!**):
- **CMS / PKCS#7** (`.p7s`) — `SignedData`, чаще всего в base64. Бывает *attached* (данные внутри)
  и *detached* (подпись отдельно).
- **XMLDSig** — XML-подпись.

Этот гайд показывает генерацию **CMS** (основной случай) и упоминает XMLDSig.

> ✅ **Для входа на `v3bl.goszakup.gov.kz` формат уже выяснен разведкой —
> это XMLDSig (ГОСТ), не CMS.** См. раздел 2.4. Для самой *подачи заявки*
> формат пока не подтверждён (раздел 6, нужен отдельный HAR).

### 2.4. Проверенный протокол входа goszakup (разведка по HAR, 2026-06-19)

Снят HAR реального входа по ЭЦП (`v3bl.goszakup.gov.kz.har`) и разобран.
Это **эмпирика с боевого портала**, а не предположение — она уточняет раздел 2.3.

**Последовательность запросов входа:**

```
1. GET  /ru/user/                → страница логина + серверный одноразовый
                                   nonce  key=14c0912b146b2d9369fb37742bf0c0cf
2. POST /ru/user/sendkey/kz      → подготовительный шаг (тело пустое)
3. WS   wss://127.0.0.1:13579/   → NCALayer, module="NURSign":
        → {module:"NURSign", type:"version"}
        → {module:"NURSign", type:"xml",
           data:"<?xml version=\"1.0\"?><root><key>NONCE</key></root>",
           source:"local"}
        ← подписанный XML  (здесь NCALayer показывает выбор ключа + ввод PIN)
4. POST /user/sendsign/kz        → sign=<подписанный XML, urlencoded>
                                   сервер проверяет подпись и nonce, тянет
                                   личность (ИИН/БИН) из сертификата
5. POST /ru/user/auth_confirm    → password=<пароль учётки портала>&agreed_check=on
                                   → 302 → /ru/cabinet/profile  (выдана cookie-сессия)
```

**Два факта, которые уточняют общий гайд:**

1. **Подпись входа — XMLDSig (enveloped), НЕ CMS/PKCS#7.** Алгоритм из тела
   `sendsign`: `urn:ietf:params:xml:ns:pkigovkz:xmlsec:algorithms:gostr34102015-gostr34112015-512`
   — то есть **ГОСТ Р 34.10-2015 / Streebog-512**. Подписывается крошечный
   документ `<root><key>NONCE</key></root>`, где `NONCE` — серверный
   одноразовый токен со страницы входа. NCALayer вызывается в режиме
   **`NURSign` → `type:"xml"`** (enveloped XMLDSig), а не CMS.

2. **Вход двухфакторный:** ЭЦП-подпись nonce (шаг 4) **плюс пароль учётной
   записи портала** (шаг 5, `auth_confirm`). Это **не** PIN ключа (PIN остаётся
   внутри NCALayer и по сети не уходит), а отдельный пароль аккаунта goszakup.
   Значит, в KeyVault для полной автоматизации входа надо хранить **и `.p12`+PIN,
   и пароль портала** клиента.

**Что это значит для серверной реализации:**
- Серверный аналог NCALayer обязан уметь **XMLDSig с ГОСТ-ключом** (а не только
  CMS). Голый openssl/`cryptography` XMLDSig-ГОСТ «из коробки» не делают —
  поэтому **NCANode (вариант A) становится основным путём**: у него есть
  XML-подпись ГОСТ-ключом из `.p12` (эндпоинт `/xml/sign`). Скармливаем `.p12` +
  `<root><key>NONCE</key></root>` → получаем подписанный XML → POST в
  `/user/sendsign/kz`.
- Вход воспроизводим как обычный HTTP-флоу на `httpx` с сохранением cookie-jar
  (получить nonce на `/ru/user/` → подписать → `sendsign` → `auth_confirm`).

**Чего ещё НЕ хватает (критично для цели):** этот HAR покрывает только **вход**.
Сама **подача заявки** почти наверняка требует **отдельной ЭЦП-подписи тела
заявки** на другом эндпоинте (формат — CMS или ещё один XMLDSig, пока не
подтверждён). Нужен второй HAR — реальной подачи заявки на лот (раздел 6).

> 🔒 **Безопасность HAR-файла:** трейс входа содержит пароль учётки портала в
> открытом виде (шаг 5, `password=...`). HAR с боевым входом нельзя коммитить
> в git и надо удалять после анализа; пароль, попавший в файл, — сменить.

---

## 3. Архитектура решения (в терминах `goszakup`)

```
┌────────────────────────────────────────────────────────────────────────┐
│  goszakup backend                                                       │
│                                                                         │
│  systemd-cron / typer CLI        Dramatiq + Redis                       │
│  ┌────────────────────┐  раз в N  ┌──────────────────────────────────┐  │
│  │ schedule_submissions│ ───мин──▶│ submit_actor(tender_id, client_id)│ │
│  │ (наполнитель)       │          │  delay = time_open - now          │  │
│  └─────────┬──────────┘          │  1. собрать payload               │  │
│            │                     │  2. достать ключ+пароль из Vault  │  │
│            ▼                     │  3. SIGN (CMS)                    │  │
│  ┌────────────────────┐          │  4. POST в площадку               │  │
│  │ Tenders (SQLAlchemy)│         └──────────────┬───────────────────┘  │
│  │ time_open/time_close│                        │                      │
│  └────────────────────┘                         ▼                      │
│  ┌──────────────────────────┐        ┌──────────────────────────────┐  │
│  │ KeyVault (зашифр. .p12    │ ◀──────│ Signing (subprocess openssl  │  │
│  │ + пароль клиента)         │  decrypt│  / httpx → NCANode)          │  │
│  └──────────────────────────┘  в момент└──────────────────────────────┘ │
│                                 подписи                                  │
└────────────────────────────────────────────────────────────────────────┘
```

**Компоненты:**
1. **Tenders (БД)** — модели SQLAlchemy с `time_open`/`time_close`, связка «клиент → тендер → ключ».
2. **KeyVault** — зашифрованное хранилище `.p12` + пароль клиента (раздел 8).
3. **schedule_submissions** — typer-CLI, запускается системным cron'ом; ставит отложенные
   Dramatiq-таски с `delay` до `time_open` (раздел 7).
4. **submit_actor** — Dramatiq-актёр: payload → подпись → отправка.
5. **Signing** — две альтернативы (раздел 5):
   - **Вариант A — NCANode** (рекомендуется): REST-сервис для server-side подписи `.p12` через httpx.
   - **Вариант B — OpenSSL + GOST engine** через `subprocess` (паттерн из `kz-docs-saas`).

---

## 4. Зависимости

### 4.1. Системные (обязательно для ГОСТ-ключей)
OpenSSL 3.x **с GOST-движком**. В `kz-docs-saas` это уже стоит и проверено:

```bash
$ openssl version
OpenSSL 3.0.13 30 Jan 2024

$ openssl engine -t gost
(gost) Reference implementation of GOST engine
     [ available ]

$ openssl list -providers | grep -A2 gost
  gost
    name: OpenSSL GOST Provider
    status: active
```

Сборка GOST-движка (Ubuntu/Debian, OpenSSL 3.x):
```bash
git clone https://github.com/gost-engine/engine /opt/src/gost-engine
cd /opt/src/gost-engine && mkdir build && cd build
cmake -DOPENSSL_ROOT_DIR=/usr/local -DOPENSSL_LIBRARIES=/usr/local/lib ..
make && sudo make install
```
Подключить провайдер в `openssl.cnf`:
```ini
[openssl_init]
providers = provider_sect
[provider_sect]
default = default_sect
gost    = gost_sect
[default_sect]
activate = 1
[gost_sect]
activate = 1
```

> 📌 В `goszakup` это надо добавить **в `Dockerfile`** (проект уже контейнеризован). Можно
> переиспользовать слой сборки gost-engine из окружения `kz-docs-saas`
> (`/opt/src/gost-engine`, модули в `/usr/local/lib/ossl-modules`).

### 4.2. Python-пакеты (добавить в `pyproject.toml`)

| Назначение | Пакет | Примечание |
|---|---|---|
| Шифрование Vault | **`cryptography>=42`** | AES-256-GCM; **нужно добавить** в deps |
| HTTP к NCANode/площадке | `httpx` | уже в `dev`; вынести в основные deps |
| Очередь / планировщик | `dramatiq[redis]`, `redis` | **уже есть** в проекте |
| (вариант B) подпись | — | через `subprocess` + системный `openssl`, без пакета |
| XML-подпись (если нужна) | `signxml` / `lxml` | `lxml` уже есть; ГОСТ-XMLDSig — ограниченно |

```toml
# pyproject.toml → [project].dependencies — добавить:
"cryptography>=42",
"httpx>=0.27",     # перенести из optional dev в основные
```

> Для **CMS-подписи** отдельная Python-крипто-библиотека не нужна — `cryptography` не умеет
> ГОСТ. Подпись делает **системный `openssl` через `subprocess`** (вариант B) **или**
> **NCANode по HTTP** (вариант A).

---

## 5. Реализация подписи

### Вариант A — NCANode (рекомендуется)

[**NCANode**](https://github.com/malikzh/NCANode) — открытый Java-сервис, созданный специально
для **server-side подписи документов ЭЦП-ключами НУЦ РК (`.p12` + пароль)**. Умеет ГОСТ и RSA,
отдаёт CMS/XML, проверяет цепочку и OCSP, ставит штамп времени (TSP). Запускается в Docker.

```yaml
# docker-compose.yml — добавить сервис
services:
  ncanode:
    image: malikzh/ncanode:3
    container_name: gz-ncanode
    ports:
      - "127.0.0.1:14579:14579"   # только внутрь, наружу не публиковать
    restart: unless-stopped
```

Клиент — один HTTP-вызов через `httpx`:

```python
# src/goszakup/signing/ncanode.py
"""Клиент к NCANode — server-side CMS-подпись ЭЦП-ключом клиента (.p12 + пароль).

NCANode крутится локально (docker), наружу не смотрит. Один вызов /cms/sign
отдаёт готовый base64-CMS, который уходит в площадку. withTsp=True добавляет
штамп времени (TSP) — для тендеров желателен, фиксирует момент подписи.
"""
from __future__ import annotations

import os

import httpx

NCANODE_URL = os.environ.get("GZ_NCANODE_URL", "http://127.0.0.1:14579")


def sign_cms(
    p12_b64: str,
    password: str,
    data_b64: str,
    *,
    detached: bool = True,
    timeout: float = 30.0,
) -> str:
    """Подписать данные ЭЦП-ключом, вернуть base64-CMS.

    p12_b64 / password — расшифрованные из Vault ТОЛЬКО на время вызова.
    data_b64 — base64 тела заявки (payload/PDF).
    """
    resp = httpx.post(
        f"{NCANODE_URL}/cms/sign",
        json={
            "data": data_b64,
            "signers": [{"key": p12_b64, "password": password, "keyAlias": None}],
            "withTsp": True,
            "tsaPolicy": "TSA_GOST_POLICY",
            "detached": detached,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    cms = body.get("cms")
    if not cms:
        raise RuntimeError(f"NCANode sign failed: {body}")
    return cms  # base64 CMS — то, что отправляем в площадку
```

**Плюсы:** не нужно вручную возиться с openssl/ASN.1, есть TSP и валидация цепочки/OCSP.
**Минус:** отдельный Java-сервис в инфре.

---

### Вариант B — OpenSSL + GOST engine через `subprocess`

Полный контроль, без лишних сервисов. Паттерн — из рабочего кода `kz-docs-saas`
(`src/modules/signing/cms-verify.ts`, `local-signing.utils.ts`), переписан под Python.

```python
# src/goszakup/signing/openssl_cms.py
"""CMS-подпись (PKCS#7) ГОСТ/RSA ключом НУЦ РК через системный openssl.

Почему subprocess, а не python-крипто: `cryptography` не поддерживает ГОСТ
Р 34.10/34.11. Системный openssl с gost-engine — единственный надёжный путь
для ключей GOST512_* от НУЦ РК. Все промежуточные файлы (расшифрованный ключ!)
пишутся во временную папку и ГАРАНТИРОВАННО удаляются (TemporaryDirectory).

Пароль к .p12 НЕ передаётся через argv (виден в `ps`), а через окружение:
-passin env:GZ_KEYPW.
"""
from __future__ import annotations

import base64
import logging
import secrets
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _run(args: list[str], env: dict | None = None) -> None:
    proc = subprocess.run(args, capture_output=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"openssl {' '.join(args[:2])} failed: {proc.stderr.decode(errors='replace')}"
        )


def detect_key_alg(cert_path: Path) -> str:
    """Вернуть 'gost' или 'rsa' по сертификату — от этого зависит алгоритм хэша."""
    proc = subprocess.run(
        ["openssl", "x509", "-in", str(cert_path), "-text", "-noout"],
        capture_output=True, text=True,
    )
    text = proc.stdout.lower()
    return "gost" if ("gost" in text or "streebog" in text or "34.10" in text) else "rsa"


def sign_cms_openssl(
    p12_bytes: bytes,
    password: str,
    data: bytes,
    *,
    detached: bool = True,
) -> str:
    """Сформировать CMS-подпись, вернуть base64 (DER).

    p12_bytes / password — уже расшифрованы из Vault, живут только в этом вызове.
    """
    rid = secrets.token_hex(6)
    with tempfile.TemporaryDirectory(prefix="gz-sign-") as d:
        dirp = Path(d)
        p12_path = dirp / f"key_{rid}.p12"
        key_path = dirp / f"key_{rid}.pem"
        cert_path = dirp / f"cert_{rid}.pem"
        data_path = dirp / f"data_{rid}.bin"
        out_path = dirp / f"sig_{rid}.p7s"

        p12_path.write_bytes(p12_bytes)
        data_path.write_bytes(data)

        # Пароль — через окружение, не через argv (защита от утечки в `ps`).
        env = {"GZ_KEYPW": password}

        # 1. Извлечь закрытый ключ (gost-engine нужен для ГОСТ-контейнеров).
        _run([
            "openssl", "pkcs12", "-in", str(p12_path), "-nocerts", "-nodes",
            "-passin", "env:GZ_KEYPW", "-out", str(key_path),
        ], env=env)

        # 2. Извлечь сертификат владельца.
        _run([
            "openssl", "pkcs12", "-in", str(p12_path), "-clcerts", "-nokeys",
            "-passin", "env:GZ_KEYPW", "-out", str(cert_path),
        ], env=env)

        # 3. Выбрать алгоритм хэша по типу ключа и подписать.
        md = "streebog256" if detect_key_alg(cert_path) == "gost" else "sha256"
        args = [
            "openssl", "cms", "-sign", "-binary",
            "-signer", str(cert_path),
            "-inkey", str(key_path),
            "-in", str(data_path),
            "-md", md,
            "-outform", "DER",
            "-out", str(out_path),
        ]
        if not detached:
            args.append("-nodetach")
        _run(args, env=env)

        return base64.b64encode(out_path.read_bytes()).decode()
        # TemporaryDirectory здесь же удалит расшифрованный ключ и все temp-файлы.
```

### 5.1. Проверка подписи перед отправкой
Никогда не отправляем неотвалидированную подпись. Логика — из `kz-docs-saas/cms-verify.ts`
(`openssl cms -verify` с `-CApath` на trust store НУЦ РК). **Скопируйте `trust/nca_rk/` из
`kz-docs-saas`** в `goszakup`.

```python
# src/goszakup/signing/verify.py
from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path


def verify_cms(cms_b64: str, original: bytes, trust_dir: str) -> bool:
    """Проверить detached-CMS против trust store НУЦ РК. True = валидна."""
    with tempfile.TemporaryDirectory(prefix="gz-verify-") as d:
        dirp = Path(d)
        sig = dirp / "sig.p7s"
        data = dirp / "data.bin"
        sig.write_bytes(base64.b64decode(cms_b64))
        data.write_bytes(original)
        proc = subprocess.run([
            "openssl", "cms", "-verify", "-binary",
            "-in", str(sig), "-inform", "DER",
            "-content", str(data),
            "-CApath", trust_dir,
            "-out", "/dev/null",
        ], capture_output=True)
        return proc.returncode == 0
```

---

## 6. Подача заявки в площадку

> ✅ **Протокол подачи разобран разведкой по HAR — см. раздел 6.1.** Это
> многошаговый визард с **двумя** крипто-операциями (шифрование цены + CMS-подпись)
> и **двумя** локальными провайдерами (Tumar CSP + NCALayer). У `goszakup.kz`
> есть REST/GraphQL API (`ows.goszakup.gov.kz`), но он на Bearer-токене (годовой,
> от ЦЭФ) и ориентирован на **чтение** реестров — подачи подписанных заявок там
> нет. В проекте уже есть scraper-слой (`src/goszakup/scraper/`) — клиент подачи
> логично положить рядом. Псевдокод ниже (`submit.py`) — концептуальный; реальный
> контракт см. в 6.1.

### 6.1. Проверенный протокол подачи заявки (разведка по HAR, 2026-06-19)

Снят HAR реальной подачи заявки на лот (`v3bl.goszakup.gov.kz (1).har`, 359
записей). Подача — **пошаговый визард** под `/ru/application/...`; на каждом шаге
ajax-POST `..._next` с 302-редиректом на следующий. `{annoId}` = id объявления,
`{appId}` = id черновика заявки (в примере `17203176` / `71705198`).

```
ШАГ 1. ЛОТЫ      GET  /ru/application/lots/{annoId}/{appId}
                 POST /ru/application/ajax_add_lots/...   selectLots[]=<lotId>
                 POST /ru/application/ajax_lots_next/...  next=1&confirmed=0   → 302

ШАГ 2. ДОКУМЕНТЫ GET  /ru/application/docs/{annoId}/{appId}
                 (show_doc/... — просмотр/прикрепление документов заявки)
                 POST /ru/application/ajax_docs_next/...  next=1              → 302

ШАГ 3. ЦЕНА (КРИПТО — ядро)  GET /ru/application/priceoffers/{annoId}/{appId}
  3a. WS  wss://127.0.0.1:6127/tumarcsp/  — Tumar CSP (ГОСТ-шифрование цены)
  3b. POST ajax_get_encr_info   lpId=<lotPriceId>&version=1.0.13.2286
        ← (server) сертификат тендера + info-токен + sign + salt
  3c. Tumar CSP EncryptOfferPrice(public_key=<cert тендера>, pl_sum, d_sum, ...)
        ← encryptData (зашифрованная цена) + encryptKey (обёрнутый сессионный ключ)
  3d. POST ajax_add_encrypt     itemID=<lpId>&encryptedData=..&sessionKey=..
                                &salt=..&info=..&sign=..
  3e. WS  wss://127.0.0.1:13579/  — NCALayer, module NURSign type "multitext":
        → {"<lpId>": "<encryptData>"}     ← CMS SignedData (base64) по ciphertext
  3f. POST ajax_save_gamma_signs  xmlData[<lpId>]=<encryptData>&signData[<lpId>]=<CMS>
  3g. POST ajax_priceoffers_next  offer[<priceId>][<lpId>][price]=<encryptData>  → 302

ШАГ 4. ПОДТВЕРЖДЕНИЕ  GET /ru/application/preview/{annoId}/{appId}  (+ reCAPTCHA)
                 POST ajax_public_application
                      public_app=Y&agree_price=true&agree_contract_project=true
                      &agree_covid19=false
ШАГ 5. КВИТАНЦИЯ GET  /ru/myapp/actionShowApp/{appId}   — поданная заявка
```

**Что реально происходит с ценой (sealed-bid, ключевой момент):**

1. **Цена шифруется, а не просто подписывается.** Сервер (`ajax_get_encr_info`)
   отдаёт **сертификат-получатель тендера** (issuer `CN=CA, O=EFC` — Центр
   электронных финансов). Tumar CSP функцией `EncryptOfferPrice` делает
   **ГОСТ-конверт**: генерит сессионный ключ, шифрует им число цены
   (`encryptData`), а сам ключ оборачивает в публичный ключ тендера
   (`encryptKey`, blob `AQIAAC...`). Расшифровать сможет только комиссия при
   вскрытии — до тех пор цена запечатана. Это анти-сговор, а не просто подпись.

2. **Сама цифра цены НЕ покидает Tumar CSP.** В параметрах `EncryptOfferPrice`
   передаются только границы (`pl_sum` — плановая сумма, `d_sum` — демпинговый
   минимум) и тексты валидации — а **число цены оператор вводит в нативном окне
   Tumar CSP**, наружу выходит только шифртекст. То есть цена скрыта от браузера
   так же, как PIN скрыт от страницы. Для автоматизации это главный барьер:
   интерактивный ввод надо заменить программным шифрованием.

3. **Поверх шифртекста — CMS-подпись поставщика** (`NURSign multitext` → NCALayer
   → `ajax_save_gamma_signs`). Подпись — CMS/PKCS#7 SignedData (`MIIP2Q...`,
   OID `1.2.840.113549.1.7.2`), ключ ГОСТ-2015 (в примере — сертификат
   `СМАГУЛОВА ПЕРИЗАТ, IIN…, ТОО "SalemSo…"`). Подписывается именно **ciphertext
   цены**, не открытая сумма.

**Эндпоинты подачи (все под `/ru/application/`, POST, ответы — JSON/HTML):**

| Эндпоинт | Назначение | Главные поля запроса |
|---|---|---|
| `ajax_add_lots/{anno}/{app}` | выбрать лоты | `selectLots[]` |
| `ajax_lots_next/...` | шаг → далее | `next=1&confirmed=0` |
| `ajax_docs_next/...` | шаг → далее | `next=1` |
| `ajax_get_encr_info/...` | получить сертификат тендера | `lpId`, `version` |
| `ajax_add_encrypt/...` | сохранить зашифр. цену | `itemID`, `encryptedData`, `sessionKey`, `salt`, `info`, `sign` |
| `ajax_save_gamma_signs/...` | сохранить CMS-подписи | `xmlData[lpId]`, `signData[lpId]` |
| `ajax_priceoffers_next/...` | шаг → далее | `offer[priceId][lpId][price]` |
| `ajax_public_application/...` | **финальная подача** | `public_app=Y`, `agree_*`, reCAPTCHA |

**Выводы для автоматизации (тяжелее входа из 2.4):**

- Подача требует **ГОСТ-шифрования** (envelope на сертификат тендера), а не только
  подписи. Это делает Tumar CSP функцией `EncryptOfferPrice` (вшита логика
  goszakup: границы суммы, `id_priceoffer`, salt/sign). Серверный аналог должен
  уметь **GOST envelope encrypt** на чужой сертификат — проверить, умеет ли это
  NCANode (`/cms/encrypt`/аналог); если нет — нужен Tumar SDK/headless или своя
  реализация GOST-2015 шифрования. **Это главный технический риск проекта.**
- **reCAPTCHA на финальном шаге** (`ajax_public_application`, виден
  `recaptcha/api.js`) — потенциальный блокер полной автоматизации; уточнить,
  обязательна ли она для всех способов закупки или только для части.
- Подпись цены — обычный **CMS ГОСТ** (это мы умеем, раздел 5, вариант A/B).
  Барьер — именно шифрование и капча, не подпись.
- `version=1.0.13.2286` — версия Tumar CSP, может проверяться сервером; учесть.

> ⚠️ Подача делалась под **другой организацией/ЭЦП** (тестовый прогон владельца).
> HAR содержит реальный сертификат поставщика и зашифрованную цену — **в git не
> коммитить**, после анализа удалить.

### 6.2. Можно ли зашифровать цену headless? (углублённый разбор 2026-06-21)

Главный технический риск проекта (раздел 6.1, шаг 3c). Первая оценка (2026-06-19)
гласила «готового headless-пути нет, формат проприетарный Tumar». Повторная
форензика **websocket-фреймов** из HAR + криптоанализ блобов **этот вердикт
уточняют: формат — стандартный, headless воспроизводим, барьер сузился**.
Короткий ответ теперь: **готового пакета нет, но путь реализуем — см. светофор
ниже.**

**Два твёрдых факта из форензики фреймов Tumar (`:6127`):**

1. **Открытое число цены НЕ покидает нативное окно Tumar.** В `EncryptOfferPrice`
   уходят только границы (`pl_sum`/`d_sum`), `id_priceoffer`, сертификат тендера и
   серверные токены — самой цифры в трафике нет. Два прогона с равными границами
   дали разные `encryptData`/`encryptKey`. → **Драйвить websocket Tumar, подставив
   цену с сервера, нельзя; реплей блоба невозможен.** Это закрывает «наивный»
   путь A (управление CryptoSocket снаружи) — даже на Windows цена вводится только
   в GUI-окне CSP.

2. **Формат конверта — НЕ проприетарный, а стандартный CryptoPro GOST
   KeyTransport.** `encryptKey` (~291 байт) = CryptoAPI **SIMPLEBLOB**
   (BLOBHEADER `bType=01/bVersion=02/aiKeyAlg=0x00046620`) + DWORD алг.транспорта +
   ASN.1 **`GostR3410-KeyTransport` (RFC 4490)**: `UKM[8]` + эфемерный откр.ключ +
   `CEK_ENC[32]+MAC[4]`, key-wrap/VKO по **RFC 4357 / RFC 7836**. `encryptData`
   (~30 б) — ГОСТ-28147 в режиме гаммирования (отсюда `plugin_gamma.js`,
   `ajax_save_gamma_signs`). Эфемерный ключ 128 б → ГОСТ-34.10-2015-512.
   Нестандартна **только обвязка goszakup**: `salt` (IV сессии), `info` (=серийник
   серта тендера), `sign` (ГОСТ-токен).

**Что это меняет:** «реверс без спецификации» из первой оценки заменяется на
**сборку стандартной структуры из RFC** + восстановление трёх прикладных полей.
Причём **параметры кривой получателя не надо добывать заранее** — сертификат
тендера (с кривой/paramset) сервер сам отдаёт в `ajax_get_encr_info` на каждую
подачу, он уже есть в HAR. Остаточный риск — семантика `salt/sign/info`
(особенно `sign`: проброс серверного токена vs генерация Tumar'ом над
шифртекстом), и его решает байт-в-байт сверка с эталонными образцами из HAR.

**Проверки прежней оценки, которые остаются в силе:**

**NCANode — НЕ умеет шифровать.** Полный список его эндпоинтов (docs + офиц.
PHP-клиент `malikzh/php-ncanode`): `/pkcs12/info`, `/pkcs12/aliases`, `/cms/sign`,
`/cms/sign/add`, `/cms/verify`, `/cms/extract`. Только **подпись и проверка**,
ни одного метода encryption/envelope/decrypt. Для CMS-**подписи** цены (шаг 3e)
NCANode годится; для **шифрования** (шаг 3c) — нет.

**Формат — CryptoAPI-упаковка стандартного GOST KeyTransport (поправка к первой
оценке).** Первая версия считала формат «проприетарным Tumar». Точный декод
показал иное: `01 02 00 00  20 66 04 00 …` → **Microsoft CryptoAPI SIMPLEBLOB**
(`bType=1`, `bVersion=2`), а тело — каноничная ASN.1 `GostR3410-KeyTransport`
(RFC 4490) с key-wrap по RFC 4357. То есть ключ завёрнут **стандартным**
алгоритмом, лишь сериализован в Windows-CryptoAPI BLOB (не в CMS `EnvelopedData`).
Сервер ждёт именно этот BLOB-конверт (`encryptData` + SIMPLEBLOB `encryptKey` +
`salt` + `sign` + `info`). Раз примитив стандартный — его собирает библиотека
(pygost), а не только нативный Tumar (см. светофор path C).

**Tumar CSP (вендор Gamma Technologies, gamma.kz):**
- CryptoAPI-совместимый провайдер. ~~Windows-only~~ — **неверно, см. ревизию
  §6.3 (2026-07-28)**: у площадки есть актуальная **macOS**-сборка клиента, а
  само ядро CSP официально собрано и под Linux. Openssl-несовместим.
- Официального SDK / REST / headless / серверного варианта в открытом доступе нет
  — только десктоп-приложение + локальный «CryptoSocket» на `wss://127.0.0.1:6127`.
- `EncryptOfferPrice` — **кастомная функция под goszakup**, за выданным площадкой
  `apiKey` (`SetAPIKey`), со вшитой логикой (границы суммы, `id_priceoffer`,
  `salt`, `sign`). Не generic GOST-encrypt.
- В перехваченных параметрах `EncryptOfferPrice` **самой цены нет** (только
  `pl_sum`/`d_sum`-границы) — цифру оператор вводит в **нативном окне Tumar**.
  Значит даже через его websocket программно подать цену нечем без неинтерактивной
  функции, которой в HAR не видно.

**Openssl + gost-engine** ГОСТ key-transport по RFC 4490 умеет, НО его
GOST-provider не знает KZ-OID `1.2.398.3.10.*`. Это, однако, оказалось НЕ
проблемой: PoC показал, что казахстанская кривая под этим OID **математически
совпадает с российской `id-tc26-gost-3410-12-512-paramSetA`** (см. ниже), которую
понимает чистый Python `gostcrypto`. Поэтому path C строим на **`gostcrypto`**
(pygost недоступен на PyPI) — ручная сборка SIMPLEBLOB + VKO/key-wrap; gost-engine
оставляем как бит-совместимый движок 28147-gamma для шифртекста цены.

**Три реалистичных трека (решение, не тупик):**

| Трек | Суть | Оценка |
|---|---|---|
| **C. Headless-реимплементация на gostcrypto** (рекомендуется) | собрать GOST `GostR3410-KeyTransport` BLOB самим: серт тендера из `ajax_get_encr_info` → VKO (RFC 7836) → key-wrap (RFC 4357) → SIMPLEBLOB; цену шифровать 28147-gamma; CMS-подпись через NCANode | PoC 🟡: кривая, VKO и структура BLOB доказаны; остаётся реверс `sign`; **~4–5 дней** |
| **A′. Golden-client** (фолбэк; с 2026-07-28 — **на macOS**, см. §6.3) | узел с реальным Tumar + ключ клиента; автоматизировать нативное окно ввода цены (на macOS — Accessibility API / интерпозер, на Windows — pywinauto/AutoIt), остальное драйвить своим кодом; отдать как headless-RPC | крипто гарантированно корректно; цена — операционная хрупкость (GUI, лицензия `apiKey` до 2026-07-01, гейт версии `1.0.13.2286`) |
| **B. Серверный SDK/лицензия Gamma** (параллельно) | запросить `ws_doc.zip`+SDK + неинтерактивную `EncryptOfferPrice` / серверный CryptoSocket (письмо — `GAMMA_INQUIRY_DRAFT.md`) | легально и «правильно», но долгий лид-тайм, ответ не гарантирован |

> Прежняя таблица «реверс без спецификации — высокий риск» устарела: формат
> опознан как стандартный RFC 4357/4490, эталонные образцы есть в HAR, кривую
> добывать не нужно. Поэтому path C переехал из «крайней меры» в основной путь.

**Де-риск-PoC (выполнен 2026-06-21) — светофор PATH C: 🟡 ЖЁЛТЫЙ.**
Собран тестовый BLOB на `gostcrypto` (pygost оказался неустанавливаем — PyPI 404 +
битый TLS у cypherpunks-хоста; ставить с `--insecure` отказались) против реального
сертификата тендера из HAR. Доказано (🟢):
- **Кривая воспроизводима без реверса параметров.** KZ-OID открытого ключа серта —
  `1.2.398.3.10.1.4.1.2`, paramset `1.2.398.3.10.1.4.1.2.1`, digest
  `1.2.398.3.10.1.3.3` (Streebog-512). Точка ключа (128 б = X‖Y, по 64 б, LE)
  **математически лежит на стандартной российской `id-tc26-gost-3410-12-512-paramSetA`**
  (на B/C — нет). То есть KZ-paramSetA == TC26 paramSetA, и `gostcrypto` содержит её
  из коробки. Эфемерный ключ в BLOB — на той же кривой.
- **VKO считается против реального ключа** (scalar·Q_recipient на кривой, KEK =
  Streebog-512 от X‖Y) и **SIMPLEBLOB собирается байт-идентично по структуре**:
  BLOBHEADER `0102000020660400`, DWORD `20a00000`, SEQ `30820113`, заголовки
  version/UKM/эфемерного OCTET — совпали с эталоном. Не совпадают лишь сами
  рандомные эфемерный ключ и wrapped-CEK (так и должно).
- **`salt`** (16 б) — байт-в-байт эхо серверного → просто пробрасываем.
  **`info`** (20 б) — == серийнику серта тендера → деривируем.
- **`encryptData`** — 30 б без padding → потоковая ГОСТ-гамма (28147); длина
  шифртекста = длине открытого текста.

Остаточный риск (🟡), из-за которого свет жёлтый, а не зелёный:
- **`sign` (32 б, Streebog-256) регенерируется внутри закрытого Tumar CSP**
  (`sign`-IN ≠ `sign`-OUT, наружу в `ajax_add_encrypt` уходит OUT). **Это
  единственная нестандартная часть.** Оффлайн-перебор формулы исчерпан: >13 000
  гипотез на каждый из двух реальных образцов (одиночные поля и все
  перестановки `encryptData/encryptKey/salt/info/sign-IN/id` × raw/base64/hex,
  с разделителями, HMAC-Streebog с ключом из проводных полей, Streebog-512→32б) —
  **ноль совпадений даже на одном образце**. Вывод: `sign` — keyed-тег с ключом,
  которого НЕТ в трафике. Клиентский `plugin_gamma.js` (1288 б) — тонкая обёртка,
  считает его не он, а нативный CSP; вытащить его с CDN без активной сессии нельзя
  (nginx 403).
  **Нюанс, сохраняющий path C живым:** перебор НЕ опроверг конструкции, где `sign`
  ключуется самим сеансовым **CEK** (или ключом, производным от `sign-IN`+CEK) —
  а CEK в path C мы генерируем сами. Значит `sign`, вероятно, **вычислим в нашей
  реализации**, как только узнаем точную MAC-конструкцию. Это вопрос спецификации
  к Gamma (трек B, письмо готово) или ограниченного RE нативного Tumar — НЕ
  недоступный навсегда секрет.
- **Обёртка CEK занимает 128 б** (не RFC-шные `CEK_ENC32+MAC4=36`) — точную схему
  key-wrap (CryptoPro RFC 4357 vs KZ-вариант) подтвердить по образцу.

**Остаточный объём до рабочего headless-шифратора (оценка ~4–5 раб. дней):**
(1) реверс `sign` — Streebog-256 над каким payload'ом, ~1–2 дня (есть 2 образца для
сверки); (2) точная схема обёртки CEK (128 б), ~1 день; (3) бит-совместимый
28147-gamma из gost-engine (уже стоит), ~0.5 дня; (4) сборка эфемерного ключа в
BLOB + интеграция в шаг priceoffers, ~1 день; (5) E2E на тестовом тендере, ~0.5–1
день. Артефакты PoC — `/tmp/poc/`, venv — `/tmp/poc_gost` (gostcrypto 1.2.5).

**Итог (обновлён 2026-06-21, после PoC):** вход (2.4) и CMS-подпись (5) — решаемы.
ГОСТ-шифрование цены **перестало быть «непроходимым проприетарным горлышком»**:
PoC доказал, что ~90% конверта воспроизводимо headless на `gostcrypto` (кривая
TC26 paramSetA, VKO, SIMPLEBLOB, gamma, salt, info). Остался **один** блокер —
тег `sign` из закрытого Tumar; оффлайн он не реверсится, но, вероятно, ключуется
нашим же CEK, т.е. вычислим при наличии MAC-конструкции от Gamma.

Практический расклад:
- **Ближайший надёжный путь — A′ (golden-client):** реальный Tumar даёт
  корректный `sign`; цену вводим в его нативное окно через UI-автоматизацию,
  остальное драйвим своим кодом. Крипто гарантированно валидно. **Платформа
  узла — macOS, а не Windows (ревизия §6.3).**
- **Стратегический путь — C (полный headless):** разблокируется, как только
  получим у Gamma конструкцию `sign` + подтверждение схемы обёртки CEK (трек B).
  Тогда ~4–5 дней инженерии. Без Gamma — ограниченный RE нативного Tumar
  (после ревизии §6.3 — разбор плагина `libEFCAPI`, выполнимый на Linux).

Обхода через выбор способа закупки **нет** — ЗЦП и все конкурентные IT-способы
требуют криптосокет (с 16.04.2026 портал блокирует подачу при старой версии).
Перепроверено 2026-07-28 по OWS: оба разобранных HAR — это `17184233`
(**Открытый конкурс**) и `17203176` (ЗЦП), и оба идут через `ajax_add_encrypt`.
В `js/application/priceoffers.js` действительно есть вторая, «негаммовая» ветка
(`#sign_offers` → `ajax_add_priceoffers`, цена открытым текстом в форме + CMS
через NCALayer) — но к нашим способам закупки она не применяется, рассчитывать
на неё нельзя.

> ⚠️ Псевдокод ниже — **исторический набросок** из первой версии гайда, он шлёт
> заявку на `ows.goszakup.gov.kz`. Такого эндпоинта не существует: перепроверено
> 2026-07-28 (см. §6.3) — в OWS v2/v3 все эндпоинты GET, мутаций в GraphQL нет.
> Реальный контракт подачи — визард из §6.1.

```python
# src/goszakup/tender/submit.py
from __future__ import annotations

import base64

import httpx

from ..signing.ncanode import sign_cms          # или openssl_cms.sign_cms_openssl
from ..signing.verify import verify_cms
from ..vault.keys import get_decrypted_key
from .payload import build_application_payload


def submit_application(tender_id: int, client_id: int, trust_dir: str) -> dict:
    # 1. Сформировать тело заявки в формате площадки.
    payload: bytes = build_application_payload(tender_id, client_id)

    # 2. Достать ключ клиента из Vault (расшифровать ТОЛЬКО сейчас).
    p12_b64, password = get_decrypted_key(client_id)

    # 3. Подписать.
    cms_b64 = sign_cms(
        p12_b64=p12_b64,
        password=password,
        data_b64=base64.b64encode(payload).decode(),
        detached=True,
    )

    # 4. Самопроверка перед отправкой.
    if not verify_cms(cms_b64, payload, trust_dir):
        raise RuntimeError("self-verify failed — не отправляем недействительную подпись")

    # 5. Отправить (формат — по API площадки!).
    resp = httpx.post(
        "https://ows.goszakup.gov.kz/v3/...",
        json={"application": base64.b64encode(payload).decode(), "signature": cms_b64},
        headers={"Authorization": f"Bearer {client_portal_token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()  # сохранить квитанцию/номер заявки
```

### 6.3. Ревизия 2026-07-28: крипто НЕ Windows-only, `sign` — в открытом плагине

Перепроверка вопроса «можно ли без Windows» по официальному дистрибутиву
`https://v3bl.goszakup.gov.kz/uploads/crypto/CryptoSocketInstaller.zip`
(качается анонимно через KZ-туннель; копия — `data/vendor/`, каталог в
`.gitignore`). Разбор установщика **опровергает две несущие посылки §6.2**.

**1. Есть актуальная macOS-сборка, и она проходит версионный гейт портала.**
Внутри установщика — и клиент `CryptoSocket.app`, и ядро `TUMAR_CSP.app`, и
библиотеки под x86_64, и под arm64 (`lib/`, `lib_arm64/`). Бинарь клиента
рапортует `CryptoSocketVersion` = **1.0.13.2287** — на билд НОВЕЕ, чем
`1.0.13.2286`, которую слал Windows-клиент в HAR (эта версия уходит в
`ajax_get_encr_info`). `Info.plist` при этом показывает устаревшее «1.0.11» —
ему верить нельзя. Официально портал поддерживает Windows и macOS; Linux в
списке клиентских ОС нет.

**2. `sign` рождается в отдельном плагине, а не внутри закрытого ядра CSP.**
Функция `EncryptOfferPrice` **вместе с окном ввода цены** реализована в
`cryptosocket/plug/libEFCAPI.dylib` — 1.7 МБ Mach-O с сохранёнными
ObjC-символами (`DynamicFormEncryptOfferPriceController` с контролами
`priceInput`/`okButton`/`cryptButtonClicked`, `ViewEncryptOfferPrice`) и
spdlog-строками. Криптографию плагин делает **стандартными вызовами ядра**
(`CPEncrypt`, `CPSignHash`, `CPVerifySignature`). Сертифицированное ядро
`lib/libcertex-csp.5.2.0.0.dylib` (1.8 МБ) прикладной логики goszakup не
содержит. Вывод §6.2 «`sign` регенерируется внутри закрытого Tumar CSP,
оффлайн невосстановим» надо читать как «внутри **плагина**, который у нас
теперь есть в виде обычного Mach-O» — цель для статического анализа на
порядок доступнее, и анализ делается **на Linux** (Ghidra), Windows не нужен.

**3. Протокол уточнён по WS-кадрам (подтверждает и достраивает §6.2.)**
Запрос к `wss://127.0.0.1:6127/tumarcsp/`:
`{"TumarCSP":"EFCAPI","Function":"EncryptOfferPrice","Param":{pl_sum, d_sum,
d_messageUp, d_messageDown, id_priceoffer, public_key}}` — цены в запросе нет
(подтверждено). Ответ: `result`, `code`, `encryptData`, `encryptKey`, **`sn`**,
**`sign`**. Отсюда закрываются два прежних вопроса: **`info`** в
`ajax_add_encrypt` — это ровно `sn` из ответа плагина, **`salt`** — эхо
серверного значения из `ajax_get_encr_info`. Неизвестной остаётся **ровно одна
величина — `sign` (32 б)**.

**4. Ядро CSP официально есть под Linux, плагина — нет.** «ТУМАР-CSP» v5.x
поставляется под 64-битный Linux (`TumarCSP_linux64_5.2.xx.xx.tgz` +
`setup_csp.sh`, каталоги `bin/etc/lib`, интерфейс CryptoAPI-совместимый;
руководство — `cms.npck.kz/downloads/res-open/manuals/tumarcsp_linux.pdf`).
Но EFCAPI/CryptoSocket под Linux вендор не публикует. Поэтому запрос к Gamma
(трек B) сужается с «дайте спецификацию `sign`» до «дайте сборку EFCAPI под
Linux» — ядро у вас уже сертифицировано под Linux.

**5. Побочный трофей: официальная документация API CryptoSocket** зашита в сами
плагины (HTML/JSDoc в строках дистрибутива) — это то, что мы просили письмом в
`GAMMA_INQUIRY_DRAFT.md`. Полный набор функций по плагинам:
`EFCAPI` → `EncryptOfferPrice`; `BaseAPI` → `EncryptData`, `DecryptData`,
`NativeCrypt`, `ElGamalCrypt`, `Sign`, `NativeSign`, `CreateHash`, `GenKey`,
`ImportKey`, `LoadKeyFrom{File,Blob,Profile,Tokens}`, работа с профилями/
сертификатами/OCSP/TSP/CMP; `ASNAPI` → `CryptMessage`, `DecryptMessage`,
`SignCMS`; `XMLAPI` → `SignXML`, `SignSOAPXML`, `Verify*`; `SYSAPI` →
отпечатки/токены; `InetAPI` → OCSP/TSP/CMP-транспорт; `FORMAPI` → диалоги
файлов.

**Что это меняет по путям:**
- **A′ переезжает с Windows на macOS.** Отдельный Windows-VPS больше не нужен;
  диалог цены — обычное Cocoa-окно с именованными контролами, автоматизируется
  Accessibility API надёжнее, чем pywinauto по заголовку окна. Потенциально
  GUI вообще убирается из критпути (интерпозер через `DYLD_INSERT_LIBRARIES`,
  подставляющий цену) — это десятки мс вместо 1–3 с; требует проверки на живой
  машине (подпись приложения / hardened runtime / SIP).
- **C стал достижим без Gamma.** Прежний тупик («перебор 13 000 гипотез не
  сошёлся») держался на двух эталонных образцах из HAR. С mac-узлом образцов
  можно снимать сколько угодно с контролируемыми входами — это дифференциальный
  анализ вместо угадывания, плюс статический разбор `libEFCAPI`.
- **Официальный API OWS как путь подачи закрыт окончательно:** в v2/v3 все
  эндпоинты — GET, единственный POST это сам GraphQL, в интроспекции схемы
  (117 типов) нет ни одной мутации.


---

## 7. Планировщик: подача ровно к `time_open` (Dramatiq)

В проекте **уже есть Dramatiq + Redis** — используем его. Dramatiq умеет **отложенную доставку**
через `send_with_options(delay=...)` (delay в **миллисекундах**). Это даёт точную подачу к
`time_open` без поллинга по секундам.

```python
# src/goszakup/queue/submit_actor.py
"""Actor подачи тендерной заявки. Ставится с delay до time_open.

Именованная очередь `submissions` — чтобы держать отдельного воркера от
скрейпинга (listing/detail) и LLM (как уже сделано в queue/actors.py).
"""
from __future__ import annotations

import logging

import dramatiq

from ..config import TRUST_STORE_DIR
from ..tender.submit import submit_application
from .broker import broker  # noqa: F401 — импорт broker до actor'а обязателен

log = logging.getLogger(__name__)


@dramatiq.actor(queue_name="submissions", max_retries=3, time_limit=120_000)
def submit_actor(tender_id: int, client_id: int) -> None:
    log.info("submit start: tender=%s client=%s", tender_id, client_id)
    result = submit_application(tender_id, client_id, TRUST_STORE_DIR)
    log.info("submit ok: tender=%s receipt=%s", tender_id, result.get("id"))
```

Наполнитель очереди — typer-CLI, который системный **cron** дёргает раз в N минут. Он находит
тендеры, открывающиеся в ближайшее окно, и ставит отложенные таски:

```python
# src/goszakup/jobs/schedule_submissions.py
"""Раз в N минут: найти тендеры, открывающиеся в ближайший час, и поставить
submit_actor с delay до time_open. Запускается системным cron через typer CLI.

Почему delay, а не enqueue в сам момент open: Dramatiq доставит ровно к сроку,
конкуренция за секунды на goszakup критична. Флаг scheduled на тендере не даёт
поставить дважды.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from ..db.engine import SessionLocal
from ..db.models import Tender, TenderClientKey
from ..queue.submit_actor import submit_actor

log = logging.getLogger(__name__)

WINDOW_SEC = 3600  # ставим в очередь тендеры, открывающиеся в ближайший час


def schedule_due_submissions() -> int:
    now = datetime.now(UTC)
    horizon = now.timestamp() + WINDOW_SEC
    scheduled = 0

    with SessionLocal() as session:
        rows = session.scalars(
            select(Tender).where(
                Tender.scheduled.is_(False),
                Tender.time_open >= now,
            )
        ).all()

        for t in rows:
            if t.time_open.timestamp() > horizon:
                continue
            # delay в МИЛЛИСЕКУНДАХ до момента открытия (>= 0).
            delay_ms = max(0, int((t.time_open.timestamp() - now.timestamp()) * 1000))

            keys = session.scalars(
                select(TenderClientKey).where(TenderClientKey.tender_id == t.id)
            ).all()
            for ck in keys:
                submit_actor.send_with_options(
                    args=(t.id, ck.client_id),
                    delay=delay_ms,
                )
                scheduled += 1

            t.scheduled = True

        session.commit()

    log.info("scheduled %d submissions", scheduled)
    return scheduled
```

Typer-команда + системный cron:
```python
# в src/goszakup/cli.py (рядом с существующими typer-командами)
@app.command()
def schedule_submissions() -> None:
    """Поставить в очередь тендеры, открывающиеся в ближайший час."""
    from .jobs.schedule_submissions import schedule_due_submissions
    schedule_due_submissions()
```
```cron
# crontab — каждые 5 минут
*/5 * * * * cd /app && /app/.venv/bin/python -m goszakup schedule-submissions >> /var/log/gz-sched.log 2>&1
```

> **Идемпотентность:** флаг `Tender.scheduled` защищает от повторной постановки. Если нужна
> защита и на уровне доставки (например, рестарт воркера) — добавьте проверку «уже подано»
> в начале `submit_application` по записи в БД (статус заявки), прежде чем подписывать.

> **Время:** `time_open` храните в UTC (в проекте уже `datetime.now(UTC)`), но площадки РК
> работают по **Asia/Almaty (UTC+5)**. Сервер синхронизируйте по NTP — расхождение в секунды
> критично для конкурентной подачи.

---

## 8. Безопасность (самый важный раздел)

Мы храним **чужие закрытые ЭЦП-ключи и пароли** — высочайший уровень ответственности.
Компрометация = возможность подписать что угодно от имени клиента.

### 8.1. Юридическая основа — обязательно
- **Явное письменное согласие** клиента на хранение ключа и подпись от его имени
  (договор/оферта: какие действия, какие тендеры, право отзыва).
- Зафиксируйте **scope**: подпись только тендерных заявок, только согласованных тендеров.
- Ведите **неизменяемый audit-log**: кто, когда, какой ключ, какой тендер, результат.

### 8.2. Хранение ключей (KeyVault)
- **Никогда** не храните `.p12` и пароль в открытом виде в БД/на диске/в git.
- Шифруйте каждый ключ **AES-256-GCM** (`cryptography`); мастер-ключ — в **HSM или KMS**
  (HashiCorp Vault / AWS KMS / Yandex KMS), не в `.env` рядом с кодом.
- Расшифровывайте **только в RAM и только в момент подписи** (temp-файлы удаляются через
  `TemporaryDirectory` — см. раздел 5).

```python
# src/goszakup/vault/crypto.py
"""AES-256-GCM шифрование контейнеров .p12 и паролей клиентов.

Мастер-ключ в проде приходит из KMS/HSM (get_master_key), НЕ из .env. На диске
и в БД лежит только шифртекст + nonce. AESGCM проверяет целостность (встроенный
tag) — подмена шифртекста даёт InvalidTag, а не «тихо неверные» данные.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def get_master_key() -> bytes:
    # ПРОД: запрос в KMS/HSM. Здесь — заглушка из окружения для dev.
    key = base64.b64decode(os.environ["GZ_VAULT_MASTER_KEY"])  # base64 от 32 байт
    if len(key) != 32:
        raise ValueError("master key must be 32 bytes (AES-256)")
    return key


def encrypt(plaintext: bytes) -> dict[str, str]:
    nonce = os.urandom(12)
    ct = AESGCM(get_master_key()).encrypt(nonce, plaintext, None)  # tag внутри ct
    return {
        "nonce": base64.b64encode(nonce).decode(),
        "data": base64.b64encode(ct).decode(),
    }


def decrypt(rec: dict[str, str]) -> bytes:
    nonce = base64.b64decode(rec["nonce"])
    ct = base64.b64decode(rec["data"])
    return AESGCM(get_master_key()).decrypt(nonce, ct, None)
```

```python
# src/goszakup/vault/keys.py
"""Достать и расшифровать ключ клиента непосредственно перед подписью."""
from __future__ import annotations

import base64

from sqlalchemy import select

from ..db.engine import SessionLocal
from ..db.models import ClientKey
from .crypto import decrypt


def get_decrypted_key(client_id: int) -> tuple[str, str]:
    """Вернуть (p12_base64, password). Расшифровка — только здесь, по запросу."""
    with SessionLocal() as session:
        row = session.scalars(
            select(ClientKey).where(ClientKey.client_id == client_id)
        ).one()
        p12_bytes = decrypt({"nonce": row.p12_nonce, "data": row.p12_enc})
        password = decrypt({"nonce": row.pw_nonce, "data": row.pw_enc}).decode()
    return base64.b64encode(p12_bytes).decode(), password
```

### 8.3. Инфраструктура
- Подписывающий стек (NCANode/openssl) — без публичных портов (NCANode слушает `127.0.0.1`).
- Доступ к Vault — по mTLS + короткоживущим токенам, минимум прав.
- Пароль не передавайте через `argv` (см. `-passin env:` в разделе 5) — только env/stdin.
- Логируйте **факт** подписи, но **никогда** — ключ, пароль или CMS целиком.
- Ротация мастер-ключа, шифрование дисков, зашифрованные бэкапы Vault.

### 8.4. Операционные риски
- **Отзыв/срок действия:** проверяйте срок и OCSP/CRL сертификата **до** подачи (в `kz-docs-saas`
  OCSP — TODO, здесь надо доделать) — иначе подпись недействительна, заявка отклонена.
- **Идемпотентность:** флаг `Tender.scheduled` + проверка статуса заявки в БД перед подписью.
- **Ошибки подписи:** алертинг (в проекте уже есть `sentry-sdk`) + ручной фолбэк — цена
  пропуска `time_open` = упущенный тендер.

---

## 9. Чек-лист внедрения

- [ ] В `Dockerfile` добавить OpenSSL 3.x + GOST engine — проверить `openssl engine -t gost`
- [ ] Скопировать trust store НУЦ РК (`trust/nca_rk/`) из `kz-docs-saas` в `goszakup`
- [ ] Добавить deps: `cryptography`, `httpx` (в основные) в `pyproject.toml`
- [ ] Модели SQLAlchemy: `Tender(time_open, scheduled)`, `ClientKey`, `TenderClientKey` + Alembic-миграция
- [ ] KeyVault: `vault/crypto.py` (AES-256-GCM) + мастер-ключ из KMS/HSM
- [ ] Юридическое согласие клиента + audit-log
- [ ] Модуль подписи: NCANode (вар. A) **или** `openssl_cms.py` (вар. B) + `verify.py`
- [ ] Dramatiq actor `submit_actor` (очередь `submissions`) + воркер
- [ ] `schedule_submissions` (typer CLI) + системный cron каждые 5 мин + NTP
- [x] Разобрать протокол **входа** по ЭЦП (HAR) — сделано, см. раздел 2.4
      (XMLDSig ГОСТ + пароль портала, двухфактор)
- [x] Снять и разобрать HAR **подачи заявки** на лот — сделано, см. раздел 6.1
      (визард lots→docs→priceoffers→preview; цена шифруется + CMS-подпись)
- [x] **Проверить headless ГОСТ-шифрование цены** — сделано, см. раздел 6.2
      (углублённый разбор 2026-06-21): цена в websocket НЕ уходит (вводится в GUI
      Tumar); формат конверта — **стандартный** CryptoPro GOST KeyTransport
      (RFC 4357/4490) в CryptoAPI SIMPLEBLOB, НЕ проприетарный. Headless
      воспроизводим (path C).
- [ ] **Де-риск-PoC path C** (запущен 2026-06-21): серт тендера + `encryptKey` из
      HAR → тестовый BLOB на pygost → байт-в-байт сверка; семантика `salt/sign/info`.
      Проставить светофор PATH C в 6.2.
- [ ] **Реализовать path C** (если PoC зелёный/жёлтый): pygost-шифратор цены
      (VKO/key-wrap/SIMPLEBLOB) + интеграция в шаг priceoffers; иначе фолбэк A′
      (golden-client — **на macOS**, см. 6.3)
- [x] **Перепроверить, нужен ли Windows** — сделано 2026-07-28, см. 6.3: НЕ нужен
      (актуальная macOS-сборка 1.0.13.2287 проходит версионный гейт), `sign`
      считает открытый плагин `libEFCAPI`, ядро CSP есть и под Linux
- [ ] **Запрос в Gamma** (трек B, параллельно): `GAMMA_INQUIRY_DRAFT.md` →
      после 6.3 запрос сужается до **сборки EFCAPI/CryptoSocket под Linux**
      (`ws_doc` уже добыт из самих плагинов)
- [x] Проверить способы закупки **без шифрования цены** — сделано (6.2): обхода
      нет, ЗЦП и конкурентные IT-способы требуют криптосокет
- [x] Проверить **reCAPTCHA** на `ajax_public_application` — **подтверждена** (HAR
      от 2026-06-21: грузится `gstatic/recaptcha` на шаге preview/подачи) → блокер
      полной автоматизации финального шага
- [ ] Учесть **пред-проверки eligibility перед подачей**: в HAR `ajax_public_application`
      вернул ошибку «нужны актуальные сведения о налоговой и пенсионной
      задолженности» — заявку нельзя подать без актуальных запрошенных справок;
      добавить проверку/прогрев этих сведений в пайплайн до момента `time_open`
- [ ] Изучить точный API подачи заявки на goszakup.kz (формат payload, CMS/XML, TSP)
- [ ] Проверка срока/OCSP сертификата перед подачей
- [ ] E2E-тест на **тестовом** ключе НУЦ РК (test.pki.gov.kz) до боевых ключей
- [ ] Sentry-алерты на ошибки подписи/подачи

---

## 10. Ссылки и источники паттернов

- Рабочая проверка CMS (TS): `kz-docs-saas/src/modules/signing/cms-verify.ts`
- GOST-хэш через openssl (TS): `kz-docs-saas/src/modules/signing/gost.ts`, `local-signing.utils.ts`
- Trust store НУЦ РК: `kz-docs-saas/trust/nca_rk/`
- NCANode (server-side подпись `.p12`): https://github.com/malikzh/NCANode
- GOST engine для OpenSSL: https://github.com/gost-engine/engine
- НУЦ РК / тестовые ключи: https://pki.gov.kz , https://test.pki.gov.kz
- goszakup.kz API: https://ows.goszakup.gov.kz (актуальную версию уточнять)

---

**Главный вывод:** технически задача решаема — весь стек GOST/CMS проверен в `kz-docs-saas`, а
очередь Dramatiq и httpx уже есть в `goszakup`. **Основная сложность — не подпись, а безопасное
хранение чужих ключей и юридическая чистота** (согласие, scope, audit). Начните с варианта A
(NCANode) + строгого KeyVault, протестируйте на тестовых ключах НУЦ РК, и только потом — боевые.
