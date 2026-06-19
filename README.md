# 1Panel Docker 镜像

[![Docker Image Version](https://img.shields.io/docker/v/moelin/1panel/latest?color=%2348BB78&logo=docker&label=version)](https://hub.docker.com/r/moelin/1panel)
[![Docker Pulls](https://img.shields.io/docker/pulls/moelin/1panel?color=%2348BB78&logo=docker&label=pulls)](https://hub.docker.com/r/moelin/1panel)
[![Docker Stars](https://img.shields.io/docker/stars/moelin/1panel?color=%2348BB78&logo=docker&label=stars)](https://hub.docker.com/r/moelin/1panel)
[![GitHub Stars](https://img.shields.io/github/stars/okxlin/docker-1panel)](https://github.com/okxlin/docker-1panel)

本项目提供 1Panel 的容器化部署镜像，支持 V1/V2、中国版 (CN) 与国际版 (Global/Intl)。镜像基于 Ubuntu LTS 构建，并通过 Supervisor 管理 1Panel 进程。

> [!CAUTION]
> 1Panel V1 与 V2 无法直接跨版本升级。已有 V1 数据需要迁移到 V2 时，请先阅读官方迁移文档：<https://1panel.cn/docs/v2/installation/v1_migrate/>

## 版本与镜像

| 系列 | 版本源 | Dockerfile | 最新源 | 适用场景 |
| --- | --- | --- | --- | --- |
| V1 CN | `resource.fit2cloud.com` | `V1/Dockerfile` | `v1.10.34-lts` | 继续维护已有 V1 部署 |
| V1 Global | `resource.1panel.pro` | `V1/Dockerfile-Global` | `v1.10.34-lts` | 继续维护已有国际版 V1 部署 |
| V2 CN | `resource.fit2cloud.com/1panel/package/v2` | `V2/Dockerfile` | `v2.2.2` | 新部署推荐 |
| V2 Global | `resource.1panel.pro/v2` | `V2/Dockerfile-Global` | `v2.2.2` | 新部署国际版推荐 |

基础镜像：`ubuntu:26.04`

常用标签：

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

生产环境建议固定到具体版本号；测试环境可使用 `v1`、`v2`、`global-v1`、`global-v2` 等浮动标签。

## 部署

### Docker Run

V2 CN：

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

V2 Global：

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

V1 仍可使用相同运行方式，只需把镜像替换为 `moelin/1panel:v1` 或 `moelin/1panel:global-v1`。V1 如需完整兼容旧部署，可继续挂载 `/var/lib/docker/volumes` 与 `/root`：

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

启动：

```bash
docker compose up -d
```

如需国际版 V2，将 `image` 改为：

```yaml
image: moelin/1panel:global-v2
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `10086` | 面板访问端口 |
| `USERNAME` | `1panel` | 管理员用户名 |
| `PASSWORD` | `1panel_password` | 管理员密码；空值或默认值会在首次启动时生成随机密码 |
| `ENTRANCE` | `entrance` | 安全入口路径，不需要前后 `/` |
| `BASE_DIR` | `/opt` | 1Panel 数据目录。映射到宿主机时应填写宿主机真实路径 |
| `TZ` | `Asia/Shanghai` | 容器时区 |
| `RESET` | `false` | 设为 `true` 可在启动时重置账号、密码、端口和入口 |

随机密码会输出到容器日志：

```bash
docker logs 1panel-v2
```

> [!IMPORTANT]
> V1 环境变量配置仅适用于 `v1.10.34-lts` 及之后版本。旧版本 V1 请按原有方式部署和维护。

## 旧数据与密码重置

容器启动时会用数据目录里的 `.docker_initialized` 判断是否已经完成过 Docker 初始化。默认数据目录是 `/opt/1panel`；如果设置了 `BASE_DIR=/data`，对应宿主机路径就是 `/data/1panel/.docker_initialized`。

已有旧数据时，如果数据库存在但没有这个标记文件，入口脚本会按“首次初始化”处理，并根据 `USERNAME`、`PASSWORD`、`PORT`、`ENTRANCE` 重新配置账号、密码、端口和入口。为了保留旧账号密码，请在启动新容器前确认标记文件存在：

```bash
# 默认 BASE_DIR=/opt
touch /opt/1panel/.docker_initialized

# 自定义 BASE_DIR=/data 时
touch /data/1panel/.docker_initialized
```

同时不要设置 `RESET=true`。`RESET=true` 会强制重新配置账号、密码、端口和入口，即使 `.docker_initialized` 已存在。

数据库文件位置：

```bash
# V1
/opt/1panel/db/1Panel.db

# V2
/opt/1panel/db/core.db
/opt/1panel/db/agent.db
```

## 编译

单架构本地构建：

```bash
# V2 CN
docker build -t 1panel:v2 ./V2

# V2 Global
docker build -f ./V2/Dockerfile-Global -t 1panel:global-v2 ./V2
```

指定版本：

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

多架构构建并推送：

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

V1 构建：

```bash
docker build --build-arg PANELVER=v1.10.34-lts -t 1panel:v1 ./V1
docker build -f ./V1/Dockerfile-Global --build-arg PANELVER=v1.10.34-lts -t 1panel:global-v1 ./V1
```

## 常见问题

### V1 和 V2 如何选择？

新部署建议使用 V2。已有 V1 部署如果运行稳定，可继续使用 V1 镜像维护；迁移到 V2 前请先备份并按官方迁移流程执行。

### 如何从 Docker 方式的 V1 迁移到 V2？

可以先用迁移脚本将 V1 从 Docker 运行模式切换到宿主机运行模式，再按官方文档升级到 V2：

```bash
wget -O 1panel_docker_to_sys.sh https://raw.githubusercontent.com/okxlin/ToolScript/refs/heads/main/1Panel/1panel-execution-mode/1panel_docker_to_sys.sh
chmod +x 1panel_docker_to_sys.sh
bash 1panel_docker_to_sys.sh
```

国内网络可将下载地址替换为 jsDelivr 镜像：

```bash
https://cdn.jsdelivr.net/gh/okxlin/ToolScript@main/1Panel/1panel-execution-mode/1panel_docker_to_sys.sh
```

官方迁移文档：<https://1panel.cn/docs/v2/installation/v1_migrate/>

### 容器内如何执行 1pctl？

```bash
docker exec -it 1panel-v2 bash
1pctl version
1pctl user-info
```

### 为什么不建议在面板里直接点升级？

容器镜像内包含特定版本的 1Panel 二进制与初始化文件。升级时建议拉取新镜像并重新部署，避免容器内升级后的文件状态与镜像版本不一致。

## 相关链接

- [1Panel 中国官网](https://1panel.cn)
- [1Panel 国际站](https://1panel.pro)
- [1Panel GitHub](https://github.com/1Panel-dev/1Panel)
- [Docker Hub](https://hub.docker.com/r/moelin/1panel)
- [本项目 GitHub](https://github.com/okxlin/docker-1panel)
- [应用商店适配库](https://github.com/okxlin/appstore)
