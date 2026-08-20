# Dockerfile for cfr-compliance-mcp
#
# Production image for the FastAPI compliance service (api.py). The image
# serves the REST API on port 8000 via uvicorn.
#
# The MCP server is a separate process (`uv run cfr-compliance-mcp`,
# default `stdio` transport) and is NOT run by this image. See README for
# the MCP launch paths.
#
# Build: docker build -t cfr-compliance-mcp .
# Run:
#   docker run --rm -p 8000:8000 \
#     -e ATM_API_KEY=... \
#     cfr-compliance-mcp
#
# Tracing is best-effort: the app exports spans to a Jaeger *agent* over
# UDP (thrift) using JAEGER_AGENT_HOST / JAEGER_AGENT_PORT. If no Jaeger
# agent is reachable the app logs a warning and continues serving.
#
# The build context must be filtered by .dockerignore (the local `.env`
# file and the large unreferenced contract PDFs are excluded there).

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root runtime user, created before any files so layers below can chown.
RUN groupadd --system appuser && useradd --system --gid appuser appuser

WORKDIR /app

# Install the package and its dependencies first for layer caching.
# Uses uv with the committed uv.lock so builds are reproducible across time.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir uv && \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Application modules that are not part of the installed package.
COPY agent/ ./agent/
COPY api.py ./

# Writable report directory for CFR_REPORTS_DIR persistence.
RUN mkdir -p /app/reports && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# The FastAPI app is the service; uvicorn is installed as a dependency.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]