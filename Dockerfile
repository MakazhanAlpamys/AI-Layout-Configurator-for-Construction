FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md setup.py MANIFEST.in ./
COPY src ./src
COPY schemas ./schemas
COPY rules ./rules
COPY ids ./ids
COPY examples ./examples
RUN python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 layout \
    && mkdir /data \
    && chown layout:layout /data

USER layout
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
EXPOSE 8766
VOLUME /data
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request; r=urllib.request.Request('http://127.0.0.1:8766/health',headers={'Authorization':'Bearer '+os.environ.get('LAYOUT_API_TOKEN','')}); urllib.request.urlopen(r,timeout=4).close()"
ENTRYPOINT ["layout-configurator", "api", "--host", "0.0.0.0", "--output", "/data", "--port", "8766"]
