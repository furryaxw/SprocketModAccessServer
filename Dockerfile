# 管理端先构建成静态产物，再由后端在同一个端口上提供：/admin 给界面，/v1 与 /ws 给客户端。
FROM node:22-slim AS admin-ui

WORKDIR /ui
COPY admin-ui/package.json ./
RUN npm install
COPY admin-ui/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    SMAS_DATABASE_URL=sqlite:///data/access.db \
    SMAS_OBJECT_STORAGE_URL=file:./data/packages

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY --from=admin-ui /ui/dist ./admin-ui/dist

RUN mkdir -p /app/data/packages /app/data/secrets
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3)"

CMD ["uvicorn", "--factory", "sprocket_access_server.core.runtime:build_app", "--host", "0.0.0.0", "--port", "8787"]
