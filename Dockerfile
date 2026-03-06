# ─── Stage 1: Build ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ─── Stage 2: Runtime ─────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Amazon Rekognition Delivery Monitor"
LABEL org.opencontainers.image.description="AI-powered delivery event detection system"
LABEL org.opencontainers.image.source="https://github.com/your-username/vision-delivery-alert"

# Install OpenCV runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash monitor
WORKDIR /app

# Copy dependencies from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Copy application code
COPY src/ ./src/
COPY config.yaml .

# Create data directories
RUN mkdir -p logs frames \
 && chown -R monitor:monitor /app

USER monitor

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import src.config; print('OK')" || exit 1

EXPOSE 8080

CMD ["python", "src/monitor.py"]
