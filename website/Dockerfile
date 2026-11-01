FROM python:3.12-slim

WORKDIR /app
COPY server.py ./
COPY public ./public

# Runs as an unprivileged user; the database lives in the /data volume.
RUN mkdir /data && chown nobody:nogroup /data
USER nobody
ENV DATA_DIR=/data PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=4)"

CMD ["python", "server.py"]
