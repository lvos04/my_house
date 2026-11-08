# syntax=docker/dockerfile:1
# Build context: repository root (needs integrations/ and apps/api/).
FROM python:3.12.8-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY integrations /src/integrations
COPY apps/api /src/api
RUN pip install /src/integrations /src/api

FROM python:3.12.8-slim-bookworm
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd -g 10001 shc && useradd -u 10001 -g shc -M -s /usr/sbin/nologin shc \
    && mkdir -p /data /backups && chown shc:shc /data /backups
COPY --from=build /opt/venv /opt/venv
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status==200 else 1)"
ENTRYPOINT ["shc-api"]
CMD ["serve"]
