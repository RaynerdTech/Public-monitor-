FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt ./
RUN mkdir -p "$PLAYWRIGHT_BROWSERS_PATH" \
    && python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && chmod -R 777 "$PLAYWRIGHT_BROWSERS_PATH"

COPY . .

CMD ["python", "-m", "app.main", "watch-all"]
