FROM docker.io/library/python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS python-build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv && python -m venv /opt/build
ENV PATH=/opt/venv/bin:$PATH
COPY apps/api/requirements.txt /build/requirements.txt
COPY <<'LOCK' /build/setuptools.txt
setuptools==84.2.1 \
    --hash=sha256:3ff8b3b5492a0aa66960f4419365a86b84e013070d4313e4d4a6894f5d64d6bc \
    --hash=sha256:ca140b45d5325a9478f880e351484f62fef15f336462e449ca0c03c873d589c2
LOCK
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    grep -vE '^[./]' /build/requirements.txt > /build/locked.txt \
    && pip install --require-hashes --only-binary=:all: -r /build/locked.txt \
    && /opt/build/bin/pip install --require-hashes --only-binary=:all: -r /build/setuptools.txt
COPY integrations /src/integrations
COPY apps/api /src/api
RUN /opt/build/bin/pip wheel --no-deps --no-build-isolation --wheel-dir /build/wheels /src/integrations /src/api \
    && pip install --no-deps --no-index /build/wheels/*.whl \
    && pip check

FROM docker.io/library/node:22.23.4-alpine3.24@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202 AS web-build
WORKDIR /src
COPY apps/web/package.json apps/web/package-lock.json ./
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca_bundle; fi; \
    npm ci --no-audit --no-fund
COPY apps/web/ ./
COPY VERSION /build/VERSION
RUN VITE_APP_VERSION="$(cat /build/VERSION)" npm run build

FROM docker.io/koenkk/zigbee2mqtt:2.14.3@sha256:fb4a6b0f6868ad1cd6a9996479802b2420e599c3463b62bbff8c8cd9a908f43a AS zigbee
# Het seriële Node-pakket is voor musl gebouwd en gebruikt de runtime uit hetzelfde artefact.
RUN mkdir -p /runtime/usr/bin /runtime/usr/lib /runtime/lib \
    && cp /usr/bin/node /runtime/usr/bin/ \
    && cp -a /lib/*.so* /runtime/lib/ \
    && cp -a /usr/lib/*.so* /runtime/usr/lib/ \
    && node -e 'if (process.versions.node.split(".")[0] < 22) process.exit(1)'

FROM docker.io/library/postgres:19.0-trixie@sha256:bb6e70add742cd868fb62cb3b2f67d1dd2bdd2b65a521e778420748753687c60 AS postgres

FROM docker.io/library/python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
LABEL org.opencontainers.image.title="my_house" \
      org.opencontainers.image.description="Smart Home Control als één Custom App"
# Server, client, extensies en initialisatiesjablonen komen uit dezelfde PostgreSQL-image.
COPY --from=postgres /usr/lib/postgresql/16/ /usr/lib/postgresql/16/
COPY --from=postgres /usr/share/postgresql/16/ /usr/share/postgresql/16/
# De versiegebonden configuratiesjabloon in het Debian-pakket is een relatieve symlink.
COPY --from=postgres /usr/share/postgresql/postgresql.conf.sample /usr/share/postgresql/postgresql.conf.sample
RUN set -eux; \
    rm /etc/apt/sources.list.d/debian.sources; \
    printf '%s\n' \
      'deb [check-valid-until=no signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://snapshot.debian.org/archive/debian/20261115T000000Z trixie main' \
      'deb [check-valid-until=no signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://snapshot.debian.org/archive/debian-security/20261115T000000Z trixie-security main' \
      > /etc/apt/sources.list; \
    apt-get -o APT::Update::Error-Mode=any update; \
    apt-get -y upgrade; \
    apt-get install -y --no-install-recommends nginx mosquitto procps libpq5 libllvm19 libldap2 libxml2 \
      libxslt1.1 libicu76 libpam0g libuuid1 liblz4-1 libreadline8t64 libsystemd0; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd -g 568 apps; \
    useradd -u 568 -g 568 -M -d /appdata -s /usr/sbin/nologin apps; \
    install -d -o 568 -g 568 -m 0700 /appdata
COPY --from=zigbee /runtime /opt/zigbee-runtime
COPY --from=zigbee /app /opt/zigbee2mqtt
RUN set -eu; \
    for loader in /opt/zigbee-runtime/lib/ld-musl-*.so.1; do ln -s "$loader" "/lib/${loader##*/}"; done; \
    LD_LIBRARY_PATH=/opt/zigbee-runtime/lib:/opt/zigbee-runtime/usr/lib /opt/zigbee-runtime/usr/bin/node --version; \
    test -s /usr/share/postgresql/16/postgresql.conf.sample; \
    /usr/lib/postgresql/16/bin/pg_dump --version | grep -q ' 16\.'
COPY --from=python-build /opt/venv /opt/venv
COPY --from=web-build /src/dist/ /usr/share/nginx/html/
COPY VERSION /app/VERSION
COPY apps/web/nginx.conf.template apps/web/docker/15-shc-config.sh /opt/shc/
COPY infra/compose/config/acl infra/compose/config/zigbee2mqtt.yaml.template /opt/shc/
COPY deploy/allinone/nginx.conf /opt/shc/nginx.conf
COPY deploy/allinone/*.py /opt/venv/lib/python3.12/site-packages/shc_allinone/
ENV PATH=/usr/lib/postgresql/16/bin:/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=8s --start-period=90s --retries=3 \
    CMD ["/opt/venv/bin/python", "-IBu", "-m", "shc_allinone.healthcheck"]
ENTRYPOINT ["/opt/venv/bin/python", "-IBu", "-m", "shc_allinone.supervisor"]
