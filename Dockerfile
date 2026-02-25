# ── Base ──────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ── Chrome headless + ChromeDriver via apt ────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates curl unzip \
    # dependências do Chrome
    fonts-liberation libappindicator3-1 libasound2 libatk-bridge2.0-0 \
    libatk1.0-0 libcups2 libdbus-1-3 libgdk-pixbuf2.0-0 libnspr4 \
    libnss3 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libxkbcommon0 libpango-1.0-0 libpangocairo-1.0-0 \
    xdg-utils \
  && wget -q -O /tmp/chrome.deb \
     "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb" \
  && dpkg -i /tmp/chrome.deb || apt-get -f install -y \
  && rm /tmp/chrome.deb \
  # ChromeDriver compatível
  && CHROME_VER=$(google-chrome --version | grep -oP '\d+\.\d+\.\d+\.\d+') \
  && CHROME_MAJOR=$(echo $CHROME_VER | cut -d. -f1) \
  && wget -q -O /tmp/chromedriver.zip \
     "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VER}/linux64/chromedriver-linux64.zip" \
  && unzip /tmp/chromedriver.zip -d /tmp/ \
  && mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver \
  && chmod +x /usr/local/bin/chromedriver \
  && rm -rf /tmp/chromedriver* \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Aplicação ─────────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tcees_service.py .

EXPOSE 5001
CMD ["gunicorn", "tcees_service:app", "--bind", "0.0.0.0:5001", \
     "--workers", "2", "--timeout", "120"]
