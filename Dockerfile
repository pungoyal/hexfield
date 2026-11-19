# The web server in a container. It has no dependencies beyond Python itself.
#
#   docker build -t hexfield .
#   docker run -p 8765:8765 -v hexfield-data:/data -e HEXFIELD_HOST_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" hexfield
#
# Then open http://<server>:8765/?host=<the key> once: that browser runs the games.
FROM python:3.13-slim

RUN useradd --create-home --uid 10001 hexfield && mkdir /data && chown hexfield /data
WORKDIR /src
COPY pyproject.toml README.md ./
COPY hexfield ./hexfield
RUN pip install --no-cache-dir . && rm -rf /src

USER hexfield
WORKDIR /data
ENV HEXFIELD_HOME=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2)"
ENTRYPOINT ["hexfield-web", "--lan", "--no-browser"]
