FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create data directory for SQLite DB
RUN mkdir -p /app/data

# Volume for persistent DB (mount this!)
VOLUME ["/app/data"]

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8080}')" || exit 1

ENV DB_PATH=/app/data/sites.db
ENV PORT=8080

CMD ["python", "main.py"]
