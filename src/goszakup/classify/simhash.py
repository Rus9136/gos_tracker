"""SimHash для дедупликации шаблонных ТЗ.

Регионы РК часто публикуют тендеры по типовому ТЗ-шаблону (поставка
ноутбуков, поддержка ИС, продукты питания и т.д.). Если у нового лота
ТЗ почти идентичен уже проанализированному — LLM-вызов бессмысленен,
можно скопировать результат.

Стратегия: 64-битный SimHash по слово-shingles размера 4. Близкими
считаем тексты с Хэмминговым расстоянием ≤ 3 на 64 битах — стандартный
порог, при котором ложные совпадения практически невозможны на
обычных текстах. Линейный скан по индексу анализов (десятки тысяч
записей → миллисекунды), LSH-banding не требуется.

В Postgres хранится как BIGINT (signed). При вычислениях возвращаемся
в unsigned 64, чтобы XOR-popcount считался корректно.
"""

from __future__ import annotations

import hashlib
import re

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
SHINGLE_K = 4
_MASK64 = 0xFFFFFFFFFFFFFFFF

# Стандартный порог. На реальных ТЗ (~30K символов) ≤3 = «почти идентичные»,
# 4-8 = «похожие, но не клон», ≥10 = разные документы.
HAMMING_THRESHOLD = 3


def simhash(text: str) -> int:
    """64-битный SimHash. Возвращает unsigned (0..2^64-1)."""
    tokens = _TOKEN_RE.findall((text or "").lower())
    if not tokens:
        return 0
    if len(tokens) < SHINGLE_K:
        shingles: list[str] = tokens
    else:
        shingles = [
            " ".join(tokens[i : i + SHINGLE_K])
            for i in range(len(tokens) - SHINGLE_K + 1)
        ]
    v = [0] * 64
    for s in shingles:
        h = int.from_bytes(
            hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big"
        )
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    """Расстояние Хэмминга между двумя 64-битными числами. Принимает любые int."""
    return bin((a ^ b) & _MASK64).count("1")


def to_signed64(u: int) -> int:
    """unsigned 64 → signed 64 (для записи в BIGINT)."""
    u &= _MASK64
    return u - (1 << 64) if u >= (1 << 63) else u


def from_signed64(s: int) -> int:
    """signed 64 → unsigned 64 (для XOR-popcount)."""
    return (s + (1 << 64)) & _MASK64 if s < 0 else s & _MASK64
