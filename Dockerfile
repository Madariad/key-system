FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY front-end/package*.json ./
RUN npm ci --no-audit --no-fund
COPY front-end/public ./public
COPY front-end/src ./src
RUN CI=true npm run build

FROM python:3.11-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libzbar0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY back-end/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir --no-deps face-recognition==1.3.0
RUN useradd --create-home appuser && mkdir /app/data && chown appuser:appuser /app/data
COPY back-end/server.py back-end/recognition.py ./
COPY back-end/faces ./faces
COPY --from=frontend /web/build ./static
ENV DATA_DIR=/app/data FACES_DIR=/app/faces STATIC_DIR=/app/static PORT=8080 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/healthz')"
CMD ["python", "server.py"]
