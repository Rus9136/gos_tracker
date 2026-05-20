"""Скачивание файлов документов в data/docs/{announcement_id}/<filename>.

Подсчитывает sha256, не перекачивает существующее (по url + размеру).
Уважает rate-limit ThrottledSession.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..config import DOCS_DIR, GZ_PROXY_URL
from .http import ThrottledSession

# v3bl блокирует наш FR-IP (геоблок). Файлы качаем через SOCKS5-туннель в KZ.
# Применяем прокси только к v3bl-хосту — основной домен goszakup.gov.kz
# работает напрямую и не нагружает туннель.
_PROXY_HOST = "v3bl.goszakup.gov.kz"


def _proxies_for(url: str) -> dict[str, str] | None:
    if not GZ_PROXY_URL or _PROXY_HOST not in url:
        return None
    return {"http": GZ_PROXY_URL, "https": GZ_PROXY_URL}

log = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    url: str
    ok: bool
    local_path: str | None = None
    sha256: str | None = None
    size: int | None = None
    content_type: str | None = None
    error: str | None = None


_UNSAFE = re.compile(r"[^\w\-.()\[\] ]+", re.UNICODE)

# ext4: 255 байт на имя файла. Кириллица в UTF-8 = 2 байта/символ, поэтому
# 200 байт ≈ 100 русских букв + расширение — с запасом, чтобы не споткнуться
# на длинных официальных названиях вроде «Приложение 10 (Сведения о ...)».
_FILENAME_MAX_BYTES = 200


def _safe_filename(name: str, fallback: str) -> str:
    name = unquote(name).strip()
    name = _UNSAFE.sub("_", name)
    name = name.strip(" .")
    if not name:
        return fallback
    encoded = name.encode("utf-8")
    if len(encoded) <= _FILENAME_MAX_BYTES:
        return name
    # Сохраняем расширение, обрезаем base. Если расширения нет — режем имя
    # как есть, обрезая на границе UTF-8 (errors='ignore' дропает хвостовые
    # обрубки многобайтных символов).
    if "." in name:
        base, ext = name.rsplit(".", 1)
        ext_bytes = len(("." + ext).encode("utf-8"))
        base_budget = _FILENAME_MAX_BYTES - ext_bytes
        if base_budget > 10:
            trunc = base.encode("utf-8")[:base_budget].decode("utf-8", errors="ignore")
            return f"{trunc}.{ext}"
    return encoded[:_FILENAME_MAX_BYTES].decode("utf-8", errors="ignore") or fallback


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1] or "file.bin"
    return _safe_filename(base, "file.bin")


def download_document(
    announcement_id: int,
    url: str,
    session: ThrottledSession | None = None,
    suggested_name: str | None = None,
) -> DownloadResult:
    sess = session or ThrottledSession()
    folder = DOCS_DIR / str(announcement_id)
    folder.mkdir(parents=True, exist_ok=True)
    fname = _safe_filename(suggested_name, "") if suggested_name else ""
    if not fname:
        fname = _filename_from_url(url)
    dest = folder / fname

    if dest.exists() and dest.stat().st_size > 0:
        size = dest.stat().st_size
        sha = _sha256_file(dest)
        return DownloadResult(
            url=url,
            ok=True,
            local_path=str(dest),
            sha256=sha,
            size=size,
            content_type=None,
        )

    try:
        resp = sess.get(
            url,
            stream=True,
            timeout=60,
            allow_redirects=True,
            proxies=_proxies_for(url),
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning("download failed %s: %s", url, e)
        return DownloadResult(url=url, ok=False, error=str(e))

    ctype = resp.headers.get("Content-Type")
    h = hashlib.sha256()
    size = 0
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with tmp.open("wb") as f:
            for chunk in resp.iter_content(64 * 1024):
                if not chunk:
                    continue
                h.update(chunk)
                size += len(chunk)
                f.write(chunk)
        tmp.replace(dest)
    except Exception as e:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return DownloadResult(url=url, ok=False, error=str(e))

    return DownloadResult(
        url=url,
        ok=True,
        local_path=str(dest),
        sha256=h.hexdigest(),
        size=size,
        content_type=ctype,
    )


def _sha256_file(path: Path, bufsize: int = 64 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()
