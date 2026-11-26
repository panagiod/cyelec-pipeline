# Stage 1: Build standalone zero-dep GNU LibreDWG `dwg2dxf` CLI binary (isolated subprocess only,
# zero Python library linking, SHA-256 verified upstream release tarball).
FROM python:3.12-slim AS dwg-builder

ARG LIBREDWG_VER=0.13.3
ARG LIBREDWG_SHA256=83f1f6e78a744777a481ff4520e4cef3f8ac4b2c1c25671077ca12fe81e8816e

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/dwg \
    && curl -fsSL "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VER}/libredwg-${LIBREDWG_VER}.tar.xz" \
        -o /tmp/dwg/libredwg.tar.xz \
    && echo "${LIBREDWG_SHA256}  /tmp/dwg/libredwg.tar.xz" | sha256sum -c - \
    && tar -xf /tmp/dwg/libredwg.tar.xz -C /tmp/dwg \
    && cd "/tmp/dwg/libredwg-${LIBREDWG_VER}" \
    && CFLAGS="-O1" ./configure --disable-bindings --disable-shared --enable-static --disable-write \
    && make -j"$(nproc)" -C src \
    && make -j"$(nproc)" -C programs dwg2dxf \
    && strip programs/dwg2dxf \
    && cp programs/dwg2dxf /usr/local/bin/dwg2dxf \
    && rm -rf /tmp/dwg

# Stage 2: Slim non-root EU Cloud Run runtime (Latin+Greek DejaVu fonts + WeasyPrint + IFC + dwg2dxf)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QT_QPA_PLATFORM=offscreen \
    CYELEC_STANDARDS_DIR=/app/standards \
    PORT=8080

# Install DejaVu Latin+Greek TTF fonts (~5 MB vs 120 MB Noto) & WeasyPrint Pango/Cairo libs
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
    && rm -rf /var/lib/apt/lists/*

# Standalone isolated dwg2dxf CLI binary (libm/libc only, invoked via subprocess shell=False)
COPY --from=dwg-builder /usr/local/bin/dwg2dxf /usr/local/bin/dwg2dxf

# Create stateless non-root user
RUN groupadd --gid 10001 cyelec \
    && useradd --uid 10001 --gid cyelec --shell /usr/sbin/nologin --create-home cyelec

WORKDIR /app

COPY pyproject.toml README.md ./
COPY standards ./standards
COPY src ./src

# Install FastAPI + GCP Firestore/GCS + LGPLv3 IFC BIM + BSD WeasyPrint PDF (zero AGPLv3)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[api,gcp,bim,render]" \
    && chown -R cyelec:cyelec /app

USER cyelec

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8080\")}/health', timeout=3)" || exit 1

ENTRYPOINT ["cyelec"]
CMD ["serve", "--host", "0.0.0.0"]
