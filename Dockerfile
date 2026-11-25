FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QT_QPA_PLATFORM=offscreen \
    CYELEC_STANDARDS_DIR=/app/standards \
    PORT=8080

# Install Latin + Greek TTF fonts for deterministic CAD/SVG/report rendering
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-core \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Create stateless non-root user
RUN groupadd --gid 10001 cyelec \
    && useradd --uid 10001 --gid cyelec --shell /usr/sbin/nologin --create-home cyelec

WORKDIR /app

COPY pyproject.toml README.md ./
COPY standards ./standards
COPY src ./src

# Install FastAPI server + Apache-2.0 GCP Firestore/GCS adapters (zero AGPLv3)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[api,gcp]" \
    && chown -R cyelec:cyelec /app

USER cyelec

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8080\")}/health', timeout=3)" || exit 1

ENTRYPOINT ["cyelec"]
CMD ["serve", "--host", "0.0.0.0"]
