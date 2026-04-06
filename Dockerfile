# ══════════════════════════════════════════════════════════════════════
#  MarketMind-Pro — Multi-stage Dockerfile
#  Optimized for Apple Silicon M4 (arm64) via Virtualization Framework
# ══════════════════════════════════════════════════════════════════════

# ── Stage 1: Build dependencies ──────────────────────────────────────
FROM --platform=linux/arm64 python:3.12-slim AS builder

# System dependencies for compiling psycopg2 and other C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Python dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime image ───────────────────────────────────────────
FROM --platform=linux/arm64 python:3.12-slim AS runtime

# Minimal runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r marketmind && useradd -r -g marketmind -d /app -s /bin/bash marketmind

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/app"

# Copy application source
COPY --chown=marketmind:marketmind src/ ./src/
COPY --chown=marketmind:marketmind alembic/ ./alembic/
COPY --chown=marketmind:marketmind alembic.ini ./
COPY --chown=marketmind:marketmind pyproject.toml ./

# Create data directories and streamlit config dir
RUN mkdir -p data/raw data/processed/charts data/cache .streamlit && \
    chown -R marketmind:marketmind data/ .streamlit

# Create __init__ files
RUN touch src/__init__.py src/agents/__init__.py src/quant/__init__.py \
    src/database/__init__.py src/mcp/__init__.py src/ui/__init__.py \
    src/utils/__init__.py

USER marketmind

# Health check — verify the app can import core modules
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "from src.utils.config import settings; print('OK')" || exit 1

# Default: run the main application
CMD ["python", "-m", "src.main"]

# ── Stage 3: Development image (with test tools) ─────────────────────
FROM runtime AS development

USER root
RUN pip install --no-cache-dir pytest pytest-asyncio pytest-cov pytest-mock freezegun
USER marketmind

CMD ["python", "-m", "pytest", "tests/", "-v", "--tb=short"]
