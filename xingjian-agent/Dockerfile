FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    XINGJIAN_DATA_DIR=/data \
    COOKIE_SECURE=0

WORKDIR /app

# No third-party dependencies: accounts, the API, and the static files
# all run on the Python standard library.
COPY accounts.py config.py prompts.py protocol.py deepseek.py \
     mock_agent.py server.py ./
COPY web ./web

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown -R app:app /app /data

USER app

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health',timeout=3)"

CMD ["python", "server.py"]
