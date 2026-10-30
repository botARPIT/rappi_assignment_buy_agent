FROM ghcr.io/astral-sh/uv:0.11.28 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    HOST=0.0.0.0 \
    PORT=8000 \
    DEEPEVAL_TELEMETRY_OPT_OUT=YES
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

RUN groupadd --gid 10001 purchasing \
    && useradd --uid 10001 --gid purchasing --create-home purchasing
COPY backend ./backend
COPY frontend ./frontend
COPY evaluations ./evaluations
RUN mkdir -p /app/.data /app/.deepeval \
    && chown -R purchasing:purchasing /app/.data /app/.deepeval

USER purchasing
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=3)"
CMD ["python", "-m", "backend.server"]
