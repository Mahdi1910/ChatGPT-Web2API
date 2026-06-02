FROM python:3.12-slim

# Install Chromium for Patchright
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxss1 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY config.example.json config.example.json

RUN pip install --no-cache-dir .

# Default config
ENV CHATGPT_W2A_PORT=8082
ENV CHATGPT_W2A_HOST=0.0.0.0
ENV CHATGPT_W2A_HEADLESS=true

EXPOSE 8082

ENTRYPOINT ["python", "-m", "chatgpt_web2api"]
