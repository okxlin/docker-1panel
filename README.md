# 1Panel Docker 镜像

语言：[简体中文](README.md) | [English](README.en.md)

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
| V2 CN | `resource.fit2cloud.com/1panel/package/v2` | `V2/Dockerfile` | `v2.5.1` | 新部署推荐 |
| V2 Global | `resource.1panel.pro/v2` | `V2/Dockerfile-Global` | `v2.5.1` | 新部署国际版推荐 |

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
moelin/1panel:v2.5.1
moelin/1panel:v2
moelin/1panel:latest

# V2 Global
moelin/1panel:global-v2.5.1
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

### V2 虚拟机 (KVM)

V2 的 `amd64`、`arm64` 镜像包含 libvirt 客户端与 `qemu-img`，可连接同一宿主机上的 libvirt，管理上游支持的虚拟机、ISO、模板、虚拟机快照、网络和存储池。虚拟机与 libvirt 服务运行在宿主机；重建面板容器不会终止虚拟机。普通部署仍按上面的配置运行。

1Panel 从 [v2.5.0](https://github.com/1Panel-dev/1Panel/releases/tag/v2.5.0) 起向社区版开放虚拟机管理。V2.5.1 的数量限制为社区版 1 台、专业版 5 台、企业版不限；其他授权规则由上游决定。V1 和其他 CPU 架构不支持此功能。

宿主机需要 Linux KVM、可访问的 `/dev/kvm`，以及安装并启动的 QEMU/libvirt。先在宿主机检查：

```bash
test -c /dev/kvm
virsh --connect qemu:///system list --all
mkdir -p /var/lib/libvirt/images /var/lib/libvirt/boot
```

使用发行版提供的 libvirt 服务与 QEMU 包。采用模块化 daemon 时，需要 QEMU、网络和存储驱动对应的服务/socket；示例挂载整个 `/run/libvirt`，兼容 socket 更新。ARM64 宿主机还需要 `qemu-system-aarch64` 与 `qemu-efi-aarch64`，并让面板容器和宿主机都能访问相同路径的 UEFI 固件。镜像内已安装 `qemu-efi-aarch64`；使用其他发行版的固件时，请按原路径额外挂载对应固件目录。

将 [V2/compose.host-libvirt.yml](V2/compose.host-libvirt.yml) 的 KVM 与 libvirt 挂载合入现有配置，或从仓库根目录启动示例：

```bash
export PANEL_PASSWORD='your_secure_password'
# 国际版启动前可设置 PANEL_IMAGE=moelin/1panel:global-v2
docker compose -f V2/compose.host-libvirt.yml up -d
```

迁移已有容器时保留原来的数据挂载、`BASE_DIR`、端口、入口与账号配置，并按下文确认 `.docker_initialized` 标记。示例的 `PANEL_*` 变量仅用于 Compose 参数替换。

| 挂载/配置 | 用途 |
| --- | --- |
| `/dev/kvm` | 供面板检查 KVM 设备；虚拟机由宿主机 libvirt 使用 KVM 启动 |
| `/run/libvirt` | 连接宿主机 libvirt Unix socket |
| `/var/lib/libvirt/images` | 默认磁盘、模板和虚拟机快照文件 |
| `/var/lib/libvirt/boot` | libvirt 默认 ISO 目录 |
| `network_mode: host` | 查看宿主机网卡/桥，并访问 libvirt 的本机 VNC 控制台 |

自定义磁盘、ISO 或存储池目录必须以**宿主机与容器相同的绝对路径**读写挂载，否则面板创建或转换的文件无法被宿主机 QEMU 使用。保持宿主机原有的 QEMU 文件权限、SELinux/AppArmor 策略。虚拟机磁盘和 libvirt 配置应另外备份；面板系统快照不覆盖这些外部目录。

> [!CAUTION]
> libvirt 的系统管理 socket 可创建、修改和删除宿主机虚拟机，权限相当于宿主机管理员。`/run/libvirt` 的只读目录挂载不会限制 socket 的管理调用。仅向可信面板和管理员提供该挂载；示例无需 `privileged`、宿主机 PID namespace、宿主根目录或 `/sys/fs/cgroup` 挂载。

容器内可运行 `virsh --connect qemu:///system list --all` 检查连接。面板的环境检测会报告缺少设备、目录或 socket；页面的“启动环境”操作不能启动宿主机服务，应在宿主机上管理 libvirt。NAT、桥接、macvtap 与设备直通的实际可用范围取决于宿主机网络/硬件和上游界面，不会因添加设备映射自动获得支持。

本项目使用隔离的真实 libvirt/TCG 环境验证客户端连接、资源与生命周期操作；TCG 测试不能证明 KVM 硬件加速。部署后仍需在目标宿主机验证虚拟机启动和控制台。libvirt 的连接与权限说明见 [Connection URIs](https://libvirt.org/uri.html#qemu-qemu-and-kvm-uris) 和 [Libvirt Daemons](https://libvirt.org/daemons.html#operating-modes)。

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

## 快照

镜像启动时会配置面板的 Supervisor 服务兼容层，供 V1 快照创建和 V2 快照恢复使用。V2 的服务文件与面板二进制采用不同文件名，避免服务脚本覆盖快照中的二进制。

建议升级镜像后重新生成快照。恢复时保持相同的面板主版本、CPU 架构和 `BASE_DIR`。

定时构建会重新拉取 Ubuntu 26.04 基础镜像、更新已安装软件包并禁用构建缓存，以获取当前架构可用的安全更新。

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
  --build-arg PANELVER=v2.5.1 \
  -t 1panel:v2.5.1 \
  ./V2

docker build \
  -f ./V2/Dockerfile-Global \
  --build-arg PANELVER=v2.5.1 \
  -t 1panel:global-v2.5.1 \
  ./V2
```

多架构构建并推送：

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  --build-arg PANELVER=v2.5.1 \
  -t <your-dockerhub-username>/1panel:v2.5.1 \
  -t <your-dockerhub-username>/1panel:v2 \
  --push \
  ./V2

docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  -f ./V2/Dockerfile-Global \
  --build-arg PANELVER=v2.5.1 \
  -t <your-dockerhub-username>/1panel:global-v2.5.1 \
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
