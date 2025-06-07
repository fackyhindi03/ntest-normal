# Dockerfile

FROM python:3.10-slim

# 1) Install OS‐level dependencies (CA certificates for HTTPS, plus curl if you want it)
RUN apt-get update && \
    apt-get install -y \
      ca-certificates \
      curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 2) Prevent .pyc files and force unbuffered stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 3) Copy & install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4) Copy application code
COPY bot.py .
COPY hianimez_scraper.py .
COPY utils.py .

# 5) Create a directory for subtitle caching
RUN mkdir -p /app/subtitles_cache

# 6) Expose port 8080 (for health check + webhook)
EXPOSE 8080

# 7) Default command to start the bot
CMD ["python", "bot.py"]
