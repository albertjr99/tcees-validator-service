# ── Base ──────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ── Chromium + ChromeDriver direto do apt (sem downloads externos) ─────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Variáveis de ambiente para o Selenium encontrar os binários ───────────────
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# ── Aplicação ─────────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tcees_service.py .
COPY tcees_validator.py .

EXPOSE 5001
CMD ["gunicorn", "tcees_service:app", "--bind", "0.0.0.0:5001", \
     "--workers", "2", "--timeout", "120"]
