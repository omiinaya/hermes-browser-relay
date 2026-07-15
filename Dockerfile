# browser-relay — Docker

# Stage 1: build environment
FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir build && python -m build --wheel

# Stage 2: runtime
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
COPY --from=builder /app/dist/*.whl .
RUN pip install --no-cache-dir *.whl && rm *.whl
RUN pip install --no-cache-dir --no-build-isolation aiohttp websockets Pillow mcp

# Copy extension files (served as static resources for the relay)
COPY extension/ ./extension/
COPY VERSION ./VERSION

# Default environment
ENV RELAY_HOST=0.0.0.0
ENV RELAY_PORT=9223

EXPOSE 9223

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:9223/status', timeout=3)" || exit 1

ENTRYPOINT ["python3", "server.py"]
CMD ["--host", "0.0.0.0", "--port", "9223"]
