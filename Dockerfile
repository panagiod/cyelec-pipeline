FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QT_QPA_PLATFORM=offscreen \
    CYELEC_STANDARDS_DIR=/app/standards

# Install Latin + Greek TTF fonts for deterministic CAD/report rendering
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

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && chown -R cyelec:cyelec /app

USER cyelec

ENTRYPOINT ["cyelec"]
CMD ["--help"]
