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

宿主机需要 Linux KVM、可访问的 `/dev/kvm`，以及安装并启动的 QEMU/libvirt。

Debian 12 的 `amd64` 宿主机使用传统 `libvirtd` 模式时，以 root 执行：

```bash
apt-get update
apt-get install -y qemu-system-x86 libvirt-daemon-system libvirt-clients
systemctl start libvirtd.socket virtlogd.socket virtlockd.socket
```

随后在宿主机检查：

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

本项目使用隔离的真实 libvirt/TCG 环境验证客户端连接、资源与生命周期操作，并在 Debian 12 `amd64` 宿主机上验证 CN/Global 镜像的原生 KVM 启动、面板发现与暂停/恢复，使用 `query-kvm` 确认硬件加速。原生 KVM 测试使用无磁盘、无网络接口的瞬态虚拟机；客体安装、网络和浏览器控制台仍需在部署宿主机验证。libvirt 的连接与权限说明见 [Connection URIs](https://libvirt.org/uri.html#qemu-qemu-and-kvm-uris) 和 [Libvirt Daemons](https://libvirt.org/daemons.html#operating-modes)。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `10086` | 面板访问端口 |
| `USERNAME` | `1panel` | 管理员用户名 |
| `PASSWORD` | `1panel_password` | 管理员密码；空值或默认值会在首次启动时生成随机密码 |
| `ENTRANCE` | `entrance` | 安全入口路径，不需要前后 `/` |
| `BASE_DIR` | `/opt` | 1Panel 基础路径；容器内面板数据位于 `${BASE_DIR}/1panel`。该目录须由数据挂载覆盖；通过宿主机 Docker socket 部署应用时，使用宿主机与容器相同的绝对路径 |
| `TZ` | `Asia/Shanghai` | 容器时区 |
| `RESET` | `false` | 设为 `true` 可在启动时重置账号、密码、端口和入口 |

新部署使用其他数据路径时，可挂载 `/srv/panel:/srv/panel` 并设置 `BASE_DIR=/srv/panel`，面板数据位于 `/srv/panel/1panel`。已有部署升级时保留原数据挂载与 `BASE_DIR`。

随机密码会输出到容器日志：

```bash
docker logs 1panel-v2
```

> [!IMPORTANT]
> V1 环境变量配置仅适用于 `v1.10.34-lts` 及之后版本。旧版本 V1 请按原有方式部署和维护。

## 旧数据与密码重置

容器启动时会用数据目录里的 `.docker_initialized` 判断是否已经完成过 Docker 初始化。默认容器路径是 `/opt/1panel/.docker_initialized`；设置 `BASE_DIR=/data` 时，容器路径是 `/data/1panel/.docker_initialized`。标记文件在宿主机上的位置由数据挂载的 `source` 与 `target` 对应关系决定。

已有旧数据时，如果数据库存在但没有这个标记文件，入口脚本会按“首次初始化”处理，并根据 `USERNAME`、`PASSWORD`、`PORT`、`ENTRANCE` 重新配置账号、密码、端口和入口。为了保留旧账号密码，请在启动新容器前确认标记文件存在：

```bash
# 在宿主机执行；以下示例要求宿主 source 与容器 target 使用相同路径
# 默认 BASE_DIR=/opt
touch /opt/1panel/.docker_initialized

# 自定义 BASE_DIR=/data 时
touch /data/1panel/.docker_initialized
```

同时不要设置 `RESET=true`。`RESET=true` 会强制重新配置账号、密码、端口和入口，即使 `.docker_initialized` 已存在。

V2 每次启动都会把 `PORT` 和 `BASE_DIR` 写入数据库，不受 `.docker_initialized` 或 `RESET` 影响；升级时须保留原值。

数据库文件位置（默认 `BASE_DIR=/opt`）。自定义 `BASE_DIR` 时，容器内的 `/opt/1panel` 链接到 `${BASE_DIR}/1panel`，持久数据位于 `${BASE_DIR}/1panel/db`：

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

### V2 容器与 Compose 备份

Docker 的挂载源属于宿主机文件系统。即使面板能够通过 Docker socket 查看业务容器，也不能直接读取未挂入面板容器的文件。V2.5.1 在源目录不可见时可能跳过数据，仍把备份任务标为成功；本镜像提供手动检查工具，**不改变官方界面的成功判定**。

备份前，在面板容器内检查选定的容器或 Compose 项目：

```bash
docker exec 1panel-v2 1panel-backup-check preflight \
  --panel-container 1panel-v2 --container my-app

# Compose 项目二选一；--container 也可重复指定
docker exec 1panel-v2 1panel-backup-check preflight \
  --panel-container 1panel-v2 --compose-project my-project
```

工具读取面板配置的同机 Unix Docker socket，核对实际 bind 与本地 named volume 的 `Source` 是否被面板的**同路径挂载**覆盖并可读。已有同名容器目录不能代替宿主机数据；检查选定源路径时，以覆盖该路径的最深挂载为准，不递归验证源目录内的所有文件或子挂载。需要补挂载时，将 [V2/compose.host-data.yml](V2/compose.host-data.yml) 的选定数据挂载合入现有配置，保留原有数据目录、账号和端口。例如：

```bash
export PANEL_BACKUP_SOURCE=/srv/my-app/data
export PANEL_PASSWORD='your_secure_password'
docker compose -f V2/compose.host-data.yml config
# 核对配置后再重建面板容器
docker compose -f V2/compose.host-data.yml up -d
```

示例只挂载明确选定、已经存在的绝对路径，默认只读；可为其他源重复添加挂载。named volume 可在宿主机用 `docker volume inspect --format '{{.Mountpoint}}' my-volume` 查询需要挂载的路径。不要为单个备份挂载整个宿主根目录。恢复写入源目录需要显式改为可写，并先单独测试恢复。

面板完成备份后，再检查生成的容器／Compose `.tar.gz`，将下面的路径换成面板容器内实际可读的归档路径：

```bash
docker exec 1panel-v2 1panel-backup-check verify \
  /opt/1panel/backup/your-backup.tar.gz
```

退出码 `0` 表示检查通过；非零退出并输出 `status: failed` 时不要依赖这份备份。`verify` 不解压，检查 manifest、inspect、持久挂载 payload、named volume 元数据和声明的 Compose 文件是否对应；tmpfs 明确排除。严格检查会拒绝链接、特殊文件和超出预算的归档，默认限制为 100,000 个 tar header 与累计 64 GiB 解压字节，可用 `--max-members`、`--max-bytes` 调整。它不能证明源文件校验和、运行中数据库的一致性、未声明的 `.env` 是否存在或恢复是否成功。重要业务仍需停写／应用原生备份和独立恢复演练。

## V2 SSH 与 Supervisor

V2 镜像包含 OpenSSH 客户端，支持面板生成 Ed25519、RSA 和 ECDSA 密钥。默认把 `/root/.ssh` 链接到持久化的面板数据目录 `ssh/`，即 `${BASE_DIR}/1panel/ssh`（默认 `/opt/1panel/ssh`），数据库中的 `/root/.ssh/...` 路径保持兼容。重建时复用原有面板数据挂载，密钥和 `authorized_keys` 就会保留；请把这个目录纳入受保护的备份范围，它包含私钥。

升级时会保留并迁移容器里已有的普通 SSH 目录，原目录留在 `/root/.ssh.before-1panel`；冲突、未知链接或嵌套挂载会停止启动，避免覆盖密钥。已有 `/root` 或 `/root/.ssh` 外部挂载保持原样。旧容器中未持久化的密钥在删除容器后无法自动找回，升级前请先保存。[OpenSSH 从 10.0 起](https://www.openssh.org/txt/release-10.0) 已移除 DSA，旧 DSA 密钥需要换用支持的算法。V2 的“本地终端”通过 SSH 连接目标主机，需要目标主机的 SSH 服务和有效凭据。

旧 SSH 目录迁移要求目标文件系统支持 [Linux 原子不覆盖重命名](https://man7.org/linux/man-pages/man2/rename.2.html)（`renameat2(RENAME_NOREPLACE)`）；不支持时会明确报错并停止启动，保留原目录与密钥，macOS/VirtioFS 上该操作的支持尚未实测。

普通 V2 部署沿用原有的数据挂载、`BASE_DIR`、端口、入口与账号配置即可，默认 SSH 密钥继续保存在面板数据目录 `ssh/`。这次权限兼容修补不要求增加 SSH volume 或修改 Compose。镜像保留所选面板数据目录的宿主 UID 和原有权限。

默认 SSH 持久化检查接受由面板运行用户（镜像内为 `root`）或所选数据目录所有者拥有的目录、文件及初始化锁文件；其他所有者、未知链接、硬链接和存储冲突仍会停止启动，SSH 目录仍拒绝组或其他用户写入。面板数据库、配置与 SSH 状态共享所选数据目录的部署信任边界；请只让可信用户写入该目录，SSH 子目录的私有权限不能替代这一要求。

[Docker Desktop 的 macOS bind mount 保留宿主目录原有权限](https://docs.docker.com/desktop/setup/install/mac-permission-requirements/#containers-running-as-root-within-the-linux-vm)。[4.80.0 的 VirtioFS 发布说明](https://docs.docker.com/desktop/release-notes/#4800) 记载：“Calls to `chown` will succeed, but `stat` will not be affected.” 因此不能把 `chown` 成功当作可见 UID 已变为容器 `root` 的依据，也无需递归改写宿主数据目录的所有权或权限。

独立的 `/root/.ssh` volume 是可选的用户管理存储方式。各面板使用独立卷，重建时复用原卷（Compose 部署需保留项目名），并单独做好受保护的备份。[Named volume 的数据独立于容器生命周期](https://docs.docker.com/engine/storage/volumes/#a-volumes-lifecycle)。不要挂载个人 macOS `~/.ssh`。空的新卷不会迁移已有 `/opt/1panel/ssh` 密钥，包括首次启动自动生成的本地密钥；切换存储前须先保存并明确迁移原密钥，不能直接改用空卷或删除旧容器。

面板 Supervisor 使用独立的 `/etc/1panel/supervisord.conf`、`/run/1panel/supervisor.sock` 与 PID 文件。工具箱的业务 Supervisor 可以初始化、管理进程及重启自身，面板进程继续运行。`1pctl` 和面板服务兼容层会明确连接面板 Supervisor。

业务 Supervisor 的配置仍在 `/etc/supervisor`，worker 配置／日志位于面板数据目录；仅保留 `/opt` 不能保证容器重建后恢复业务 daemon 的 include 配置和运行状态。需要保留业务配置时，单独持久化 `/etc/supervisor`，重建后在工具箱启动业务 Supervisor 并核对进程；没有保留配置时需重新初始化。它管理的是容器内进程，不能代替宿主机的 systemd 服务管理。

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

多架构构建并推送（先将 `<your-dockerhub-username>` 替换为自己的 Docker Hub 用户名）：

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  --build-arg PANELVER=v2.5.1 \
  -t "<your-dockerhub-username>/1panel:v2.5.1" \
  -t "<your-dockerhub-username>/1panel:v2" \
  --push \
  ./V2

docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  -f ./V2/Dockerfile-Global \
  --build-arg PANELVER=v2.5.1 \
  -t "<your-dockerhub-username>/1panel:global-v2.5.1" \
  -t "<your-dockerhub-username>/1panel:global-v2" \
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
