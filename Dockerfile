# Multi-stage build: compile Python dependencies in builder, copy only
# necessary artifacts to slim runtime image to reduce final image size.

# Stage 1: Builder - compile dependencies
FROM python:3.12-slim AS builder

WORKDIR /app

# Build tools for compiling C extensions (e.g. cffi/cryptography) on
# architectures without prebuilt manylinux wheels, such as linux/arm/v7.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager for fast, reproducible builds
RUN pip install --no-cache-dir uv

# Copy dependency files and source code. LICENSE is required at build time,
# not just for distribution: pyproject.toml's `license = { file = "LICENSE" }`
# makes hatchling (the build backend) validate that the file exists during
# the editable install `uv sync` performs below.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

# Build the project with locked dependencies (no dev tools)
RUN uv sync --frozen --no-dev

# Stage 2: Runtime - minimal image with only runtime dependencies
FROM python:3.12-slim

WORKDIR /app

# Copy the pre-built virtual environment from builder
# (--chown is not strictly necessary but helps maintain explicit permissions)
COPY --from=builder /app/.venv ./.venv

# Copy application source code
COPY --from=builder /app/src ./src

# Create non-root user (uid 1000) to run the app
# This improves security and is a Docker best practice
RUN useradd -m -u 1000 appuser

# Create data directory for SQLite database and persistent state
# Owned by the app user so it can write to it
RUN mkdir -p /data && chown appuser:appuser /data

# Add the venv's bin directory to PATH so 'familywall-mcp' command is available
ENV PATH="/app/.venv/bin:$PATH"

# Set sensible defaults for hosted mode (can be overridden at runtime)
ENV FAMILYWALL_MODE=hosted
ENV FAMILYWALL_PORT=8000
ENV FAMILYWALL_DATABASE_PATH=/data/familywall.sqlite3

# Switch to non-root user
USER appuser

# Expose the port the server listens on
EXPOSE 8000

# Health check: verify the /health endpoint responds with 200 OK
# Uses Python's built-in urllib to avoid external dependencies
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

# Run the MCP server in hosted mode (configuration via environment variables)
CMD ["familywall-mcp", "serve"]
