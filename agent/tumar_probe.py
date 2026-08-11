"""Крипто-стенд: зовём EncryptOfferPrice у локального CryptoSocket напрямую.

Зачем: единственная невоспроизведённая часть sealed-bid конверта — тег `sign`
(гайд §6.2/§6.3). Раньше у нас было ровно два образца из HAR, и перебор
конструкций по ним не сошёлся. CryptoSocket слушает `wss://127.0.0.1:6127`
локально и принимает вызовы от любого процесса на машине, поэтому образцы можно
снимать пачками с КОНТРОЛИРУЕМЫМИ входами — без портала и без живого конкурса.

Запускать НА УЗЛЕ с установленным CryptoSocket (macOS-бандл площадки).
Зависимость одна: `pip install websocket-client`.

Протокол снят с HAR реальной подачи (порядок обязателен):
    1. SYSAPI.SetAPIKey   {"apiKey": <лицензия площадки>}
    2. BaseAPI.GetVersion {"type": 3}          → "1.0.13.2286"
    3. EFCAPI.EncryptOfferPrice {pl_sum, d_sum, d_messageUp, d_messageDown,
                                 id_priceoffer, public_key, sign, salt}
       → {result, code, encryptData, encryptKey, sn, sign}

Внимание: `sign`/`salt` во ВХОДЕ — серверные токены из `ajax_get_encr_info`,
и они НЕ описаны во встроенной документации плагина. Возвращаемый `sign` — уже
другой (это и есть искомая величина). Саму цену плагин наружу не отдаёт: она
вводится в его нативном окне, поэтому стенд печатает, какое число ввести, и
ждёт, пока оператор нажмёт «Зашифровать».

Использование:
    # 0) диагностика: нужен ли вообще apiKey (может, хватит лицензий бандла)
    python -m agent.tumar_probe --price 1000 --label no-key

    # 1) с ключом площадки (свежий взять из DevTools на шаге цены, см. README)
    python -m agent.tumar_probe --api-key-file apikey.txt --price 1000 \
        --repeat 3 --label baseline

    # 2) серия с изменённым одним параметром — для дифференциального анализа
    python -m agent.tumar_probe --api-key-file apikey.txt --price 1000 \
        --id-priceoffer 670114_41710747 --label id-changed

Результат — JSONL (по умолчанию `tumar_samples.jsonl`), его и присылать.
Секретов клиента в нём нет: цена тестовая, сертификат — публичный,
apiKey в файл НЕ пишется (только его sha256, чтобы различать прогоны).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import ssl
import sys
import time

TUMAR_WS = "wss://127.0.0.1:6127/tumarcsp/"
TUMAR_WS_PLAIN = "ws://127.0.0.1:6127/tumarcsp/"
# CryptoSocket слушает IPv6-wildcard (`*:6126`, `*:6127`), а не только IPv4-loopback.
# Разница не косметическая: если 127.0.0.1:6127 занял ЧУЖОЙ процесс (у нас это был
# редактор Cursor), подключение уходит к нему и рвётся на рукопожатии — снаружи
# выглядит как поломка Tumar. Поэтому пробуем и IPv6-адреса, и соседний порт.
TUMAR_WS_ALT = [
    "wss://[::1]:6127/tumarcsp/",
    "wss://[::1]:6126/tumarcsp/",
    "wss://127.0.0.1:6126/tumarcsp/",
]
# CryptoSocket проверяет Origin — представляемся страницей площадки, как браузер.
ORIGIN = "https://v3bl.goszakup.gov.kz"
# Окно ввода цены блокирует ответ до действия оператора.
DIALOG_TIMEOUT = 300

# Сертификат сокета выписан на 127.0.0.1 локальным корнем из бандла: системного
# доверия к нему нет, да и канал не покидает машину — проверку отключаем всегда.
# Дальше перебор: сборка Gamma может говорить на старом TLS (свежий OpenSSL его
# режет политикой SECLEVEL) или вообще без шифрования. Что сработало — печатаем.
_BASE_SSL = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}


def _candidates() -> list[tuple[str, str, dict]]:
    # Шифро-строки идут ОТДЕЛЬНЫМИ вариантами от версий протокола: на сборках с
    # LibreSSL `@SECLEVEL` не понимается и set_ciphers падает раньше рукопожатия —
    # если склеить их в один вариант, старый TLS так и не будет проверен.
    cipher_opts = [("", {}), (" + все шифры", {"ciphers": "ALL"}), (" + SECLEVEL=0", {"ciphers": "ALL:@SECLEVEL=0"})]
    protocols = [("по умолчанию", {})]
    for name in ("PROTOCOL_TLSv1_2", "PROTOCOL_TLSv1_1", "PROTOCOL_TLSv1"):
        proto = getattr(ssl, name, None)
        if proto is not None:
            protocols.append((name, {"ssl_version": proto}))

    out = []
    for proto_name, proto_opt in protocols:
        for cipher_name, cipher_opt in cipher_opts:
            out.append((f"wss, {proto_name}{cipher_name}", TUMAR_WS, {**_BASE_SSL, **proto_opt, **cipher_opt}))
    for alt in TUMAR_WS_ALT:
        out.append((f"wss, {alt}", alt, dict(_BASE_SSL)))
    out.append(("ws без TLS", TUMAR_WS_PLAIN, {}))
    return out


def _connect(url: str | None = None) -> tuple:
    """Вернуть (сокет, описание соединения) — описание идёт в JSONL."""
    import websocket  # noqa: PLC0415 — внешняя зависимость только для стенда

    tried = []
    variants = [("явно заданный --url", url, dict(_BASE_SSL))] if url else _candidates()
    for name, target, sslopt in variants:
        try:
            ws = websocket.WebSocket(sslopt=sslopt)
            ws.connect(target, origin=ORIGIN)
        except Exception as exc:  # noqa: BLE001 — перебираем варианты до первого рабочего
            tried.append(f"{name}: {type(exc).__name__}: {exc}")
            continue

        info = {"variant": name, "url": target}
        sock = getattr(ws, "sock", None)
        if hasattr(sock, "version"):
            try:
                info["tls"] = sock.version()
                info["cipher"] = sock.cipher()
            except Exception:  # noqa: BLE001 — не TLS-сокет, деталей просто не будет
                pass
        print(f"  соединение: {name} → {target} [{info.get('tls')}, {info.get('cipher')}]")
        return ws, info
    raise RuntimeError("подключиться не удалось. Попытки:\n    " + "\n    ".join(tried))


def _call(ws, plugin: str, function: str, param: dict, timeout: int) -> dict:
    ws.send(json.dumps({"TumarCSP": plugin, "Function": function, "Param": param}))
    ws.settimeout(timeout)
    return json.loads(ws.recv())


def probe(
    *,
    api_key: str | None,
    cert_b64: str,
    id_priceoffer: str,
    pl_sum: int,
    d_sum: int,
    sign_in: str,
    salt_in: str,
    price: str,
    label: str,
    url: str | None = None,
) -> dict:
    rec: dict = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "label": label,
        "request": {
            "id_priceoffer": id_priceoffer,
            "pl_sum": pl_sum,
            "d_sum": d_sum,
            "sign_in": sign_in,
            "salt_in": salt_in,
            "cert_sha256": hashlib.sha256(base64.b64decode(cert_b64)).hexdigest(),
            "api_key_sha256": hashlib.sha256(api_key.encode()).hexdigest() if api_key else None,
        },
        "price_typed": price,
    }

    ws, conn = _connect(url)
    rec["connection"] = conn
    try:
        if api_key:
            rec["set_api_key"] = _call(ws, "SYSAPI", "SetAPIKey", {"apiKey": api_key}, 30)
            print("  SetAPIKey:", rec["set_api_key"])
        rec["version"] = _call(ws, "BaseAPI", "GetVersion", {"type": 3}, 30)
        print("  GetVersion:", rec["version"])
        # Без успешного SetAPIKey сокет не инициализирован (код 10001) — дальше
        # звать нечего: окно цены не откроется, а прогон только запутает выборку.
        if rec["version"].get("result") != "true":
            rec["aborted"] = "сокет не инициализирован — нужен действующий apiKey"
            print("  ", rec["aborted"])
            return rec

        print(f"  → сейчас откроется окно Tumar. Введи цену {price} и нажми «Зашифровать».")
        resp = _call(
            ws,
            "EFCAPI",
            "EncryptOfferPrice",
            {
                "pl_sum": pl_sum,
                "d_sum": d_sum,
                "d_messageUp": f"Введенное значение превышает плановую сумму {pl_sum} тнг",
                "d_messageDown": f"Введенное значение меньше демпинговой сумму {d_sum} тнг",
                "id_priceoffer": id_priceoffer,
                "public_key": cert_b64,
                "sign": sign_in,
                "salt": salt_in,
            },
            DIALOG_TIMEOUT,
        )
        rec["response"] = resp
        # Длины блобов — сразу в записи, чтобы не декодировать при разборе.
        for field in ("encryptData", "encryptKey", "sign", "sn"):
            value = resp.get(field)
            if isinstance(value, str):
                try:
                    rec.setdefault("lengths", {})[field] = len(base64.b64decode(value))
                except Exception:  # noqa: BLE001 — не base64, длина не важна
                    pass
    finally:
        ws.close()
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description="Снять образцы EncryptOfferPrice с локального Tumar")
    ap.add_argument("--api-key", help="лицензия площадки (строка)")
    ap.add_argument("--api-key-file", type=pathlib.Path, help="файл с лицензией площадки")
    ap.add_argument(
        "--cert-file",
        type=pathlib.Path,
        default=pathlib.Path(__file__).with_name("tender_cert_sample.b64"),
        help="base64 DER сертификата-получателя (по умолчанию образец рядом с модулем)",
    )
    ap.add_argument("--id-priceoffer", default="670114_41710746")
    ap.add_argument("--pl-sum", type=int, default=3333300)
    ap.add_argument("--d-sum", type=int, default=1)
    ap.add_argument("--sign-in", default="O4dLdg76eK2quuGLvW++fLEjvGu/QLfJc7paGXqPaiI=")
    ap.add_argument("--salt-in", default="uyQS9nDmbKBVYhwefY4EdA==")
    ap.add_argument("--url", help="адрес сокета, если автоперебор не подошёл")
    ap.add_argument(
        "--diagnose",
        action="store_true",
        help="только подключиться и спросить версию — без окна ввода цены",
    )
    ap.add_argument("--price", help="какую цену вводить в окне (для инструкции и записи)")
    ap.add_argument("--label", default="sample", help="метка серии — попадёт в JSONL")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("tumar_samples.jsonl"))
    args = ap.parse_args()

    api_key = args.api_key
    if args.api_key_file:
        api_key = args.api_key_file.read_text().strip()

    if args.diagnose:
        ws, _conn = _connect(args.url)
        try:
            if api_key:
                print("  SetAPIKey:", _call(ws, "SYSAPI", "SetAPIKey", {"apiKey": api_key}, 30))
            print("  GetVersion:", _call(ws, "BaseAPI", "GetVersion", {"type": 3}, 30))
        finally:
            ws.close()
        return

    if not args.price:
        sys.exit("нужен --price (какую цену вводить в окне) или --diagnose")
    if not api_key:
        print("! apiKey не задан — проверяем, обязателен ли он вообще (это тоже результат)")

    if not args.cert_file.exists():
        sys.exit(f"нет файла сертификата: {args.cert_file}")
    cert_b64 = args.cert_file.read_text().strip()

    with args.out.open("a", encoding="utf-8") as fh:
        for i in range(args.repeat):
            print(f"[{i + 1}/{args.repeat}] label={args.label}")
            try:
                rec = probe(
                    api_key=api_key,
                    cert_b64=cert_b64,
                    id_priceoffer=args.id_priceoffer,
                    pl_sum=args.pl_sum,
                    d_sum=args.d_sum,
                    sign_in=args.sign_in,
                    salt_in=args.salt_in,
                    price=args.price,
                    label=args.label,
                    url=args.url,
                )
            except Exception as exc:  # noqa: BLE001 — любой сбой это тоже данные
                rec = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "label": args.label,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print("  ОШИБКА:", rec["error"])
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            resp = rec.get("response") or {}
            print("  result:", resp.get("result"), "code:", resp.get("code"), "sign:", resp.get("sign"))

    print(f"\nГотово. Образцы: {args.out.resolve()} — пришли этот файл.")


if __name__ == "__main__":
    main()
