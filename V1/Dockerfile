FROM ubuntu:26.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG DEBIAN_FRONTEND=noninteractive
ARG INSTALL_MODE="stable"
ARG PANELVER=""
ENV TZ=Asia/Shanghai

LABEL org.opencontainers.image.version="${PANELVER}" \
      org.opencontainers.image.source="https://github.com/okxlin/docker-1panel" \
      org.opencontainers.image.description="1Panel CN Edition Docker Image" \
      org.opencontainers.image.title="1Panel CN" \
      org.opencontainers.image.vendor="okxlin"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget tar unzip zip git sudo gnupg sqlite3 tzdata \
    expect ca-certificates lsb-release && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /tmp/docker.gpg.asc && \
    gpg --batch --show-keys --with-colons /tmp/docker.gpg.asc | awk -F: '$1 == "fpr" { print $10; exit }' | grep -qx "9DC858229FC7DD38854AE2D88D81803C0EBFCD88" && \
    gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg /tmp/docker.gpg.asc && \
    rm -f /tmp/docker.gpg.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list && \
    apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin && \
    printf '%s\n' '#!/bin/sh' 'exec docker compose "$@"' > /usr/local/bin/docker-compose && \
    chmod +x /usr/local/bin/docker-compose && \
    ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose && \
    apt-get install -y --no-install-recommends supervisor && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY ./1pctl /app/1pctl.custom
COPY ./install-v1.override.sh /app/install-v1.override.sh
COPY ./entrypoint.sh /usr/local/bin/entrypoint.sh
COPY ./supervisord.conf /etc/supervisor/conf.d/supervisord.conf

RUN chmod +x /usr/local/bin/entrypoint.sh && \
    chmod +x /app/install-v1.override.sh && \
    if [ -z "$PANELVER" ]; then \
        PANELVER=$(curl -s https://resource.fit2cloud.com/1panel/package/stable/latest); \
    fi && \
    ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "armhf" ]; then ARCH="armv7"; fi && \
    if [ "$ARCH" = "ppc64el" ]; then ARCH="ppc64le"; fi && \
    PKG_NAME="1panel-${PANELVER}-linux-${ARCH}.tar.gz" && \
    PKG_URL="https://resource.fit2cloud.com/1panel/package/${INSTALL_MODE}/${PANELVER}/release/${PKG_NAME}" && \
    echo "Downloading ${PKG_URL}..." && \
    curl -sSL -o package.tar.gz "$PKG_URL" && \
    tar zxvf package.tar.gz --strip-components 1 && \
    rm package.tar.gz && \
    mv /app/1pctl.custom /app/1pctl && \
    sed -i "s|ORIGINAL_VERSION=.*|ORIGINAL_VERSION=${PANELVER}|g" /app/1pctl && \
    chmod +x 1pctl && \
    bash install-v1.override.sh && \
    mkdir -p /opt/1panel/tmp /opt/1panel/db /opt/1panel/log && \
    mkdir -p /usr/share/1panel-default && \
    echo "Pre-generating database..." && \
    (timeout 10s /usr/local/bin/1panel || true) && \
    if [ -f /opt/1panel/db/1Panel.db ]; then \
        echo "Database generated successfully." && \
        mv /opt/1panel/* /usr/share/1panel-default/; \
        rm -rf /opt/1panel/*; \
    else \
        echo "Error: Database generation failed. Core log:" && \
        cat /opt/1panel/log/* || true && \
        exit 1; \
    fi && \
    rm -rf /app/*

WORKDIR /
EXPOSE 10086
VOLUME ["/var/run/docker.sock", "/opt"]
CMD ["/usr/local/bin/entrypoint.sh"]
