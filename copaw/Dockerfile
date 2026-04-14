FROM tailscale/tailscale:stable AS tailscale-bin

FROM agentscope/copaw:latest

USER root

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      dnsutils \
      gnupg \
      iproute2 \
      iputils-ping \
      jq \
      net-tools \
      procps \
      traceroute; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
    chmod a+r /etc/apt/keyrings/docker.gpg; \
    . /etc/os-release; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      docker-ce-cli \
      docker-compose-plugin; \
    rm -rf /var/lib/apt/lists/*

COPY --from=tailscale-bin /usr/local/bin/tailscale /usr/local/bin/tailscale

ENV TS_SOCKET=/var/run/tailscale/tailscaled.sock

RUN install -d /var/run/tailscale
