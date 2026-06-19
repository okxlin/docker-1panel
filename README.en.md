# 1Panel Docker Images

Languages: [English](README.en.md) | [Simplified Chinese](README.md)

[![Docker Image Version](https://img.shields.io/docker/v/moelin/1panel/latest?color=%2348BB78&logo=docker&label=version)](https://hub.docker.com/r/moelin/1panel)
[![Docker Pulls](https://img.shields.io/docker/pulls/moelin/1panel?color=%2348BB78&logo=docker&label=pulls)](https://hub.docker.com/r/moelin/1panel)
[![Docker Stars](https://img.shields.io/docker/stars/moelin/1panel?color=%2348BB78&logo=docker&label=stars)](https://hub.docker.com/r/moelin/1panel)
[![GitHub Stars](https://img.shields.io/github/stars/okxlin/docker-1panel)](https://github.com/okxlin/docker-1panel)

This project provides container images for 1Panel. It supports V1/V2, CN builds, and Global/Intl builds. Images use an Ubuntu LTS base and run 1Panel processes through Supervisor.

> [!CAUTION]
> 1Panel V1 cannot upgrade to V2 in place. If you need to migrate existing V1 data to V2, read the official migration guide first: <https://1panel.cn/docs/v2/installation/v1_migrate/>

## Versions And Images

| Series | Source | Dockerfile | Latest source version | Use case |
| --- | --- | --- | --- | --- |
| V1 CN | `resource.fit2cloud.com` | `V1/Dockerfile` | `v1.10.34-lts` | Maintain existing V1 deployments |
| V1 Global | `resource.1panel.pro` | `V1/Dockerfile-Global` | `v1.10.34-lts` | Maintain existing Global V1 deployments |
| V2 CN | `resource.fit2cloud.com/1panel/package/v2` | `V2/Dockerfile` | `v2.2.2` | Recommended for new CN deployments |
| V2 Global | `resource.1panel.pro/v2` | `V2/Dockerfile-Global` | `v2.2.2` | Recommended for new Global deployments |

Base image: `ubuntu:26.04`

Common tags:

```bash
# V1 CN
moelin/1panel:v1.10.34-lts
moelin/1panel:v1

# V1 Global
moelin/1panel:global-v1.10.34-lts
moelin/1panel:global-v1

# V2 CN
moelin/1panel:v2.2.2
moelin/1panel:v2
moelin/1panel:latest

# V2 Global
moelin/1panel:global-v2.2.2
moelin/1panel:global-v2
```

Pin a version tag in production. Floating tags such as `v1`, `v2`, `global-v1`, and `global-v2` work better for testing.

## Deployment

### Docker Run

V2 CN:

```bash
docker run -d \
  --name 1panel-v2 \
  --restart always \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt:/opt \
  -e TZ=Asia/Shanghai \
  -e PORT=10086 \
  -e USERNAME=admin \
  -e PASSWORD=your_secure_password \
  -e ENTRANCE=myentrance \
  moelin/1panel:v2
```

V2 Global:

```bash
docker run -d \
  --name 1panel-v2-global \
  --restart always \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt:/opt \
  -e TZ=Asia/Shanghai \
  -e PORT=10086 \
  -e USERNAME=admin \
  -e PASSWORD=your_secure_password \
  -e ENTRANCE=myentrance \
  moelin/1panel:global-v2
```

V1 uses the same run pattern. Replace the image with `moelin/1panel:v1` or `moelin/1panel:global-v1`. For full compatibility with older V1 deployments, you may also mount `/var/lib/docker/volumes` and `/root`:

```bash
-v /var/lib/docker/volumes:/var/lib/docker/volumes \
-v /root:/root
```

### Docker Compose

```yaml
services:
  1panel:
    image: moelin/1panel:v2
    container_name: 1panel-v2
    restart: always
    network_mode: host
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /opt:/opt
    environment:
      - TZ=Asia/Shanghai
      - PORT=10086
      - USERNAME=admin
      - PASSWORD=your_secure_password
      - ENTRANCE=myentrance
      - BASE_DIR=/opt
    labels:
      createdBy: Apps
```

Start it:

```bash
docker compose up -d
```

For V2 Global, change the image:

```yaml
image: moelin/1panel:global-v2
```

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `PORT` | `10086` | Panel access port |
| `USERNAME` | `1panel` | Administrator username |
| `PASSWORD` | `1panel_password` | Administrator password. Empty values and the default value generate a random password on first startup |
| `ENTRANCE` | `entrance` | Security entrance path, without leading or trailing `/` |
| `BASE_DIR` | `/opt` | 1Panel data directory. When you bind-mount it, set this to the real host path |
| `TZ` | `Asia/Shanghai` | Container timezone |
| `RESET` | `false` | Set to `true` to reset username, password, port, and entrance on startup |

The container logs print the random password:

```bash
docker logs 1panel-v2
```

> [!IMPORTANT]
> V1 environment-variable initialization applies only to `v1.10.34-lts` and later. Keep older V1 images on their original deployment flow.

## Existing Data And Password Resets

The entrypoint uses `.docker_initialized` in the data directory to decide whether Docker initialization has already run. The default data directory is `/opt/1panel`. If you set `BASE_DIR=/data`, the marker path becomes `/data/1panel/.docker_initialized` on the host.

If you have existing data and the database exists without this marker file, the entrypoint treats the container as a first startup. It then applies `USERNAME`, `PASSWORD`, `PORT`, and `ENTRANCE`, which can change the old username, password, port, and entrance. To keep existing credentials, create the marker before starting the new container:

```bash
# Default BASE_DIR=/opt
touch /opt/1panel/.docker_initialized

# When BASE_DIR=/data
touch /data/1panel/.docker_initialized
```

Do not set `RESET=true` for existing data you want to preserve. `RESET=true` forces username, password, port, and entrance configuration even when `.docker_initialized` exists.

Database paths:

```bash
# V1
/opt/1panel/db/1Panel.db

# V2
/opt/1panel/db/core.db
/opt/1panel/db/agent.db
```

## Build

Local single-architecture builds:

```bash
# V2 CN
docker build -t 1panel:v2 ./V2

# V2 Global
docker build -f ./V2/Dockerfile-Global -t 1panel:global-v2 ./V2
```

Build a specific version:

```bash
docker build \
  --build-arg PANELVER=v2.2.2 \
  -t 1panel:v2.2.2 \
  ./V2

docker build \
  -f ./V2/Dockerfile-Global \
  --build-arg PANELVER=v2.2.2 \
  -t 1panel:global-v2.2.2 \
  ./V2
```

Multi-architecture build and push:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  --build-arg PANELVER=v2.2.2 \
  -t <your-dockerhub-username>/1panel:v2.2.2 \
  -t <your-dockerhub-username>/1panel:v2 \
  --push \
  ./V2

docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  -f ./V2/Dockerfile-Global \
  --build-arg PANELVER=v2.2.2 \
  -t <your-dockerhub-username>/1panel:global-v2.2.2 \
  -t <your-dockerhub-username>/1panel:global-v2 \
  --push \
  ./V2
```

V1 builds:

```bash
docker build --build-arg PANELVER=v1.10.34-lts -t 1panel:v1 ./V1
docker build -f ./V1/Dockerfile-Global --build-arg PANELVER=v1.10.34-lts -t 1panel:global-v1 ./V1
```

## FAQ

### Which one should I use, V1 or V2?

Use V2 for new deployments. If an existing V1 deployment works well, you can keep using the V1 image. Back up your data and follow the official migration flow before moving from V1 to V2.

### How do I migrate a Docker-based V1 deployment to V2?

You can use the migration script to switch V1 from Docker mode to host mode, then upgrade to V2 with the official guide:

```bash
wget -O 1panel_docker_to_sys.sh https://raw.githubusercontent.com/okxlin/ToolScript/refs/heads/main/1Panel/1panel-execution-mode/1panel_docker_to_sys.sh
chmod +x 1panel_docker_to_sys.sh
bash 1panel_docker_to_sys.sh
```

For networks in mainland China, replace the download URL with the jsDelivr mirror:

```bash
https://cdn.jsdelivr.net/gh/okxlin/ToolScript@main/1Panel/1panel-execution-mode/1panel_docker_to_sys.sh
```

Official migration guide: <https://1panel.cn/docs/v2/installation/v1_migrate/>

### How do I run 1pctl inside the container?

```bash
docker exec -it 1panel-v2 bash
1pctl version
1pctl user-info
```

### Why should I avoid clicking upgrade inside the panel?

The image contains a specific 1Panel binary version and its initialization files. Pull a new image and redeploy when you need to upgrade, so the container filesystem stays aligned with the image version.

## Links

- [1Panel CN website](https://1panel.cn)
- [1Panel Global website](https://1panel.pro)
- [1Panel GitHub](https://github.com/1Panel-dev/1Panel)
- [Docker Hub](https://hub.docker.com/r/moelin/1panel)
- [This project on GitHub](https://github.com/okxlin/docker-1panel)
- [App store compatibility repository](https://github.com/okxlin/appstore)
