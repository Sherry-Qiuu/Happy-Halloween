FROM python:3.12-slim

# 构建上下文是仓库根目录（见 render.yaml 的 dockerContext），
# 因为排程引擎在 xingjian-agent 外面：backend_services 会去 ../calendar-rescheduler 找 core.py。

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    XINGJIAN_DATA_DIR=/data \
    COOKIE_SECURE=0

WORKDIR /app

# 纯标准库，没有任何第三方依赖
COPY xingjian-agent/*.py ./
COPY xingjian-agent/web ./web

# 排程引擎放到 /calendar-rescheduler，正好是 /app 的上一级，
# backend_services 里的 parents[1] / "calendar-rescheduler" 就能命中。
COPY calendar-rescheduler /calendar-rescheduler

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown -R app:app /app /data

USER app

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health',timeout=3)"

CMD ["python", "server.py"]
