# syntax=docker/dockerfile:1
# Build context: repository root (needs integrations/, apps/api/ and VERSION).
# Base images are pinned by tag; Dependabot (docker) proposes updates.
FROM python:3.12.16-slim-trixie AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY integrations /src/integrations
COPY apps/api /src/api
RUN pip install /src/integrations /src/api

# Only the PGDG apt signing key is taken from here, so pg_dump below comes from the same
# repository and major version as the database (contract C11: the pre-migration dump must
# match the server; keep this tag on the same major as the db service in compose.yaml).
FROM postgres:16.15-trixie AS pgdg

FROM python:3.12.16-slim-trixie
ARG APP_VERSION=dev
ARG PG_MAJOR=16
LABEL org.opencontainers.image.title="Smart Home Control API" \
      org.opencontainers.image.description="API, worker en klimaat/energie-engine" \
      org.opencontainers.image.version="${APP_VERSION}"
COPY --from=pgdg /usr/local/share/keyrings/postgres.gpg.asc /usr/local/share/keyrings/postgres.gpg.asc
# PostgreSQL client from PGDG, plus security fixes released after the base image was built.
RUN set -eux; \
    . /etc/os-release; \
    echo "deb [ signed-by=/usr/local/share/keyrings/postgres.gpg.asc ] http://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main ${PG_MAJOR}" \
      > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get upgrade -y; \
    apt-get install -y --no-install-recommends "postgresql-client-${PG_MAJOR}"; \
    rm -rf /var/lib/apt/lists/*; \
    "/usr/lib/postgresql/${PG_MAJOR}/bin/pg_dump" --version | grep -q " ${PG_MAJOR}\."
# pg_dump/psql of exactly this major version come first in PATH (no version-picking wrapper).
ENV PATH=/usr/lib/postgresql/${PG_MAJOR}/bin:/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd -g 10001 shc && useradd -u 10001 -g shc -M -s /usr/sbin/nologin shc \
    && mkdir -p /data /backups && chown shc:shc /data /backups
COPY --from=build /opt/venv /opt/venv
# Single version source (contract C9): config.py reads /app/VERSION.
COPY VERSION /app/VERSION
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status==200 else 1)"
ENTRYPOINT ["shc-api"]
CMD ["serve"]
