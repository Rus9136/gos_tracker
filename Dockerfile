# Multi-stage: builder с gcc + dev-libs, runtime — только то, что нужно
# pdfplumber/uvicorn в продакшене. Финальный образ ~250 МБ.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential нужен для C-расширений (cffi/cryptography/lxml в случае
# отсутствия колеса под платформу), libxml2/libxslt — для lxml, jpeg/zlib —
# для pdfplumber (Pillow).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev libxslt1-dev \
        zlib1g-dev libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --upgrade pip && \
    pip wheel --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# poppler-utils — pdfplumber его не дёргает (он использует pdfminer.six),
# оставлен как дешёвая страховка под edge-case PDF, которые приходят
# битыми с goszakup. Удалить, если за месяц журнала не понадобился.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash --uid 1000 app

WORKDIR /app

# Сначала ставим колёса (этот слой кешируется на одинаковых deps),
# потом копируем код приложения — типичный паттерн для быстрых rebuilds.
COPY --from=builder /wheels /wheels
RUN pip install /wheels/*.whl && rm -rf /wheels

# Копируем то, что нужно в рантайме: код, миграции, alembic-конфиг, scripts.
# Не копируем data/, tests/, .git/ — отсечены через .dockerignore.
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations/ ./migrations/
COPY --chown=app:app src/ ./src/
COPY --chown=app:app scripts/ ./scripts/

# Каталог под данные/документы. На проде монтируется как volume.
RUN mkdir -p /app/data && chown -R app:app /app/data

USER app

EXPOSE 8765

# Дефолт — uvicorn. CLI/daily вызываются через `docker compose run`:
#   docker compose run --rm web python -m goszakup.cli daily
CMD ["python", "-m", "uvicorn", "goszakup.web.app:app", \
     "--host", "0.0.0.0", "--port", "8765"]
