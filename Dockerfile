FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY hexfield ./hexfield
RUN pip install --no-cache-dir --root-user-action=ignore --prefix=/install .

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
LABEL org.opencontainers.image.source=https://github.com/vaari-dev/hexfield \
      org.opencontainers.image.description="Hexfield: a hex-board settling and trading game" \
      org.opencontainers.image.licenses=MIT
RUN useradd --create-home --uid 10001 hexfield && install -d -o hexfield -m 700 /data /backups
COPY --from=build /install /usr/local
USER hexfield
WORKDIR /data
ENV HEXFIELD_HOME=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
ENTRYPOINT ["hexfield-web", "--lan", "--no-browser"]
ARG HEXFIELD_BUILD
ENV HEXFIELD_BUILD=$HEXFIELD_BUILD
