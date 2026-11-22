# StockFlow: one image, two services (web app on 8501, API + phone app on 8000). See deploy/README.md.
# ---- phone app (static files)
FROM node:22-slim AS mobile
WORKDIR /m
COPY mobile/package.json mobile/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY mobile/ ./
RUN npm run build

# ---- runtime
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/stock_mover
COPY stock_mover/requirements.txt ./
RUN pip install -r requirements.txt
COPY stock_mover/ ./
COPY --from=mobile /m/dist /srv/mobile/dist
RUN useradd --system --uid 10001 --home /srv stockflow && mkdir -p workspace && chown -R stockflow /srv
USER stockflow
EXPOSE 8501 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8501/_stcore/health || curl -fsS http://localhost:8000/api/v1/health || exit 1
# default: the web app; the API service overrides the command (docker-compose.yml)
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true", \
     "--browser.gatherUsageStats=false", "--server.enableXsrfProtection=true"]
