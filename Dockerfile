# Dockerfile for cfr-compliance-mcp
# Production-oriented AI compliance engineering platform
#
# Build: docker build -t cfr-compliance-mcp .
# Run:   docker run -p 8000:8000 -e OPENAI_API_KEY=... cfr-compliance-mcp

# Use a lightweight Python base image
FROM python:3.12-slim AS builder

# Set working directory
WORKDIR /app

# Install system dependencies (minimal for security and size)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Copy only dependency files first for layer caching
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -e ".[dev]" 2>&1 | tail -5

# Copy project source code
COPY src/ /Users/vaibhavvikasranjan/Downloads/cfr-compliance-mcp/src/
COPY agent/ /Users/vaibhavvikasranjan/Downloads/cfr-compliance-mcp/agent/
COPY api.py /Users/vaibhavvikasranjan/Downloads/cfr-compliance-mcp/api.py
COPY reports/ /Users/vaibhavvikasranjan/Downloads/cfr-compliance-mcp/reports/
COPY contracts/ /Users/vaibhavvikasranjan/Downloads/cfr-compliance-mcp/contracts/

# ---- Runtime stage ----
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install minimal runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
# Copy project source
COPY --from=builder /app /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # OpenTelemetry: export to Jaeger by default (can be overridden)
    OTEL_TRACES_EXPORTER=jaeger \
    OTEL_ENDPOINT=http://jaeger:14268/api/traces

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app/home
WORKDIR /app
USER appuser

# Expose the FastAPI port
EXPOSE 8000

# Entrypoint
# - Uses uvicorn to serve the FastAPI app
# - Can be overridden via command line
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]