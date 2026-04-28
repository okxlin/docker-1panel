# 1Panel Docker 镜像
forked from https://github.com/okxlin/docker-1panel

[![Docker Image Version (latest semver)](https://img.shields.io/docker/v/forza0310/1panel/latest?color=%2348BB78&logo=docker&label=version)](https://hub.docker.com/repository/docker/forza0310/1panel/)
[![Docker Pulls](https://img.shields.io/docker/pulls/forza0310/1panel?color=%2348BB78&logo=docker&label=pulls)](https://hub.docker.com/repository/docker/forza0310/1panel/)
[![Docker Stars](https://img.shields.io/docker/stars/forza0310/1panel?color=%2348BB78&logo=docker&label=stars)](https://hub.docker.com/repository/docker/forza0310/1panel/)
[![GitHub Repo stars](https://img.shields.io/github/stars/forza0310/docker-1panel)](https://github.com/forza0310/docker-1panel)

> [!CAUTION]
> **重要提示**: 1Panel V2 版本与 V1 版本**无法直接跨版本升级**！
> 
> 如需从 V1 迁移到 V2，请参考官方迁移文档: https://1panel.cn/docs/v2/installation/v1_migrate/
> 
> **Docker 用户迁移**: 如果您以 Docker 方式运行 V1，可通过迁移脚本先切换到宿主机运行模式，再使用官方升级工具升级到 V2，最后可切换回 Docker 运行模式。详见 [Q2: 如何从 V1 迁移到 V2?](#q2-如何从-v1-迁移到-v2)
>
> 脚本下载链接:
> [GitHub](https://raw.githubusercontent.com/okxlin/ToolScript/refs/heads/main/1Panel/1panel-execution-mode/1panel_docker_to_sys.sh) |

## 📑 目录

- [简介](#简介)
- [版本说明](#版本说明)
- [镜像标签](#镜像标签)
- [V1 版本使用](#v1-版本使用)
  - [注意事项](#v1-注意事项)
  - [Docker 安装](#v1-docker-安装)
  - [Docker Compose 安装](#v1-docker-compose-安装)
  - [环境变量配置](#v1-环境变量配置)
  - [修改面板显示版本](#修改面板显示版本)
- [V2 版本使用](#v2-版本使用)
  - [注意事项](#v2-注意事项)
  - [Docker 安装](#v2-docker-安装)
  - [Docker Compose 安装](#v2-docker-compose-安装)
  - [环境变量配置](#环境变量配置)
- [镜像编译](#镜像编译)
- [常见问题](#常见问题)
- [相关链接](#相关链接)
- [声明](#声明)

---

## 简介

本项目提供 1Panel 的 Docker 容器化部署方案，支持 V1 和 V2 两个主要版本。

**项目特点**:
- ✅ 支持多架构平台 (amd64, arm64, armv7, ppc64le, s390x)
- ✅ 自动化构建和版本更新
- ✅ 提供中国版 (CN) 和国际版 (Global) 镜像
- ✅ V1/V2 版本均支持环境变量配置 (V1 需 v1.10.34-lts+)
- ✅ 云原生架构: Supervisor 进程管理 + 动态配置

**致谢**: 本项目受 [1panel-dood](https://github.com/tangger2000/1panel-dood) 启发，采用替换二进制文件的方式实现容器化部署。

---

## 版本说明

| 版本 | 下载源 | 状态 | 推荐使用 |
|------|--------|------|----------|
| **V1** | 国内/国际 | 维护中 | 稳定用户 |
| **V2** | 国内 | 最新版 | 新用户 |

> [!WARNING]
> V1 和 V2 **无法直接跨版本升级**，迁移请参考: https://1panel.cn/docs/v2/installation/v1_migrate/

---

## 镜像标签

### V1 中国版 (CN)
```bash
moelin/1panel:v1.10.22    # 具体版本
moelin/1panel:v1          # 浮动标签 (最新 V1)
```

### V1 国际版 (Global)
```bash
moelin/1panel:global-v1.10.22    # 具体版本
moelin/1panel:global-v1          # 浮动标签 (最新 V1 Global)
```

### V2 中国版 (CN)
```bash
moelin/1panel:v2.0.6      # 具体版本
moelin/1panel:v2          # 浮动标签 (最新 V2)
moelin/1panel:latest      # 全局最新 (指向 V2)
```

> [!TIP]
> **标签选择建议**
> - 生产环境: 使用具体版本号 (如 `v1.10.22`)
> - 测试环境: 使用浮动标签 (如 `v1`, `v2`)
> - 追求最新: 使用 `latest` (目前指向 V2)

---

## V1 版本使用

### V1 注意事项

> [!IMPORTANT]
> **使用限制**
> - **禁止**点击面板右下角更新按钮
> - 应通过拉取新镜像并重新部署来更新

> [!NOTE]
> **云原生架构升级** (v1.10.34-lts+)
> - ✅ 支持环境变量动态配置 (端口、用户名、密码、入口)
> - ✅ Supervisor 进程管理，自动重启和日志管理
> - ✅ 首次启动自动配置，支持随机密码生成
> - ⚠️ **仅 v1.10.34-lts 及以后版本支持**环境变量配置功能

**默认配置**:
- 端口: `10086`
- 账户: `1panel`
- 密码: `1panel_password` (首次启动可自动生成随机密码)
- 入口: `entrance`

**支持架构**: amd64, arm64, armv7, ppc64le, s390x

### V1 Docker 安装

#### 中国版 (CN) - 基础安装
```bash
docker run -d \
    --name 1panel \
    --restart always \
    --network host \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /var/lib/docker/volumes:/var/lib/docker/volumes \
    -v /opt:/opt \
    -v /root:/root \
    -e TZ=Asia/Shanghai \
    moelin/1panel:v1
```

#### 中国版 (CN) - 自定义配置 (v1.10.34-lts+)
```bash
docker run -d \
    --name 1panel \
    --restart always \
    --network host \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /var/lib/docker/volumes:/var/lib/docker/volumes \
    -v /opt:/opt \
    -v /root:/root \
    -e TZ=Asia/Shanghai \
    -e PORT=10086 \
    -e USERNAME=admin \
    -e PASSWORD=your_secure_password \
    -e ENTRANCE=myentrance \
    moelin/1panel:v1
```

#### 国际版 (Global) - 基础安装
```bash
docker run -d \
    --name 1panel-global \
    --restart always \
    --network host \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /var/lib/docker/volumes:/var/lib/docker/volumes \
    -v /opt:/opt \
    -v /root:/root \
    -e TZ=Asia/Shanghai \
    moelin/1panel:global-v1
```

#### 国际版 (Global) - 自定义配置 (v1.10.34-lts+)
```bash
docker run -d \
    --name 1panel-global \
    --restart always \
    --network host \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /var/lib/docker/volumes:/var/lib/docker/volumes \
    -v /opt:/opt \
    -v /root:/root \
    -e TZ=Asia/Shanghai \
    -e PORT=10086 \
    -e USERNAME=admin \
    -e PASSWORD=your_secure_password \
    -e ENTRANCE=myentrance \
    moelin/1panel:global-v1
```

### V1 Docker Compose 安装

#### 基础配置

创建 `docker-compose.yml`:

```yaml
version: '3'
services:
  1panel:
    container_name: 1panel
    restart: always
    network_mode: "host"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /var/lib/docker/volumes:/var/lib/docker/volumes
      - /opt:/opt
      - /root:/root
    environment:
      - TZ=Asia/Shanghai
    image: moelin/1panel:v1
    labels:
      createdBy: "Apps"
```

#### 自定义配置 (v1.10.34-lts+)

```yaml
version: '3'
services:
  1panel:
    container_name: 1panel
    restart: always
    network_mode: "host"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /var/lib/docker/volumes:/var/lib/docker/volumes
      - /opt:/opt
      - /root:/root
    environment:
      - TZ=Asia/Shanghai
      - PORT=10086
      - USERNAME=admin
      - PASSWORD=your_secure_password
      - ENTRANCE=myentrance
      - BASE_DIR=/opt
    image: moelin/1panel:v1
    labels:
      createdBy: "Apps"
```

运行:
```bash
docker-compose up -d
```

### V1 环境变量配置

> [!WARNING]
> **版本要求**: 环境变量配置功能仅在 **v1.10.34-lts** 及以后版本生效

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `PORT` | `10086` | 面板访问端口 |
| `USERNAME` | `1panel` | 管理员用户名 |
| `PASSWORD` | `1panel_password` | 管理员密码 (首次启动自动生成随机密码) |
| `ENTRANCE` | `entrance` | 安全入口路径 |
| `BASE_DIR` | `/opt` | 数据存储目录 |
| `TZ` | `Asia/Shanghai` | 时区设置 |
| `RESET` | `false` | 设为 `true` 强制重置配置 |

> [!TIP]
> **密码安全提示**
> - 如果不设置 `PASSWORD` 或使用默认值，首次启动会自动生成随机密码
> - 随机密码会在容器日志中显示，请及时查看并保存
> - 查看日志: `docker logs 1panel`

### 修改面板显示版本

> [!NOTE]
> 自 2023-09-19 起，镜像已支持自动修改面板显示版本，**无需手动操作**

如需手动修改:

#### 1. 安装 SQLite3

```bash
# Debian/Ubuntu
apt-get update && apt-get install sqlite3 -y

# RedHat/CentOS
yum install sqlite -y
```

#### 2. 修改版本信息

```bash
# 备份数据库
cp /opt/1panel/db/1Panel.db /opt/1panel/db/1Panel.db.bak

# 打开数据库
sqlite3 /opt/1panel/db/1Panel.db

# 修改版本 (替换 v1.10.22 为实际版本)
UPDATE settings SET value = 'v1.10.22' WHERE key = 'SystemVersion';

# 退出
.exit

# 重启容器
docker restart 1panel
```

---

## V2 版本使用

### V2 注意事项

- ✅ 支持通过环境变量配置端口、用户名、密码、入口
- ✅ 支持数据目录映射 (`BASE_DIR`)
- ✅ 首次启动自动配置，无需手动初始化
- ⚠️ **无法从 V1 直接升级**，迁移请参考官方文档

### V2 Docker 安装

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

### V2 Docker Compose 安装

创建 `docker-compose.yml`:

```yaml
version: '3'
services:
  1panel-v2:
    container_name: 1panel-v2
    restart: always
    network_mode: "host"
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
    image: moelin/1panel:v2
    labels:
      createdBy: "Apps"
```

运行:
```bash
docker-compose up -d
```

### 环境变量配置

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `PORT` | `10086` | 面板访问端口 |
| `USERNAME` | `1panel` | 管理员用户名 |
| `PASSWORD` | `1panel_password` | 管理员密码 (首次启动自动生成随机密码) |
| `ENTRANCE` | `entrance` | 安全入口路径 |
| `BASE_DIR` | `/opt` | 数据存储目录 |
| `TZ` | `Asia/Shanghai` | 时区设置 |
| `RESET` | `false` | 设为 `true` 强制重置配置 |

> [!TIP]
> **密码安全提示**
> - 如果不设置 `PASSWORD` 或使用默认值，首次启动会自动生成随机密码
> - 随机密码会在容器日志中显示，请及时查看并保存
> - 查看日志: `docker logs 1panel-v2`

---

## 镜像编译

### V1 编译

```bash
# 单架构编译
docker build --build-arg PANELVER=v1.10.22 -t 1panel:v1.10.22 ./V1

# 多架构编译并推送
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  --build-arg PANELVER=v1.10.22 \
  -t <your-dockerhub-username>/1panel:v1.10.22 \
  --push \
  ./V1
```

### V1 Global 编译

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  --build-arg PANELVER=v1.10.22 \
  -t <your-dockerhub-username>/1panel:global-v1.10.22 \
  -f ./V1/Dockerfile-Global \
  --push \
  ./V1
```

### V2 编译

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  --build-arg PANELVER=v2.0.6 \
  -t <your-dockerhub-username>/1panel:v2.0.6 \
  --push \
  ./V2
```

---

## 常见问题

### Q1: V1 和 V2 如何选择?

**V1 适合**:
- 已有 V1 部署且运行稳定
- 不需要最新功能
- 追求稳定性

**V2 适合**:
- 新用户首次部署
- 需要最新功能和性能优化
- 愿意接受新架构

### Q2: 如何从 V1 迁移到 V2?

> [!CAUTION]
> **无法直接升级！**

请参考官方迁移文档: https://1panel.cn/docs/v2/installation/v1_migrate/

> [!TIP]
> **Docker 运行模式迁移方案**
>
> 如果您当前以 Docker 方式运行 1Panel V1，可以通过以下步骤迁移到 V2:
>
> **步骤 1**: 使用迁移脚本将 1Panel 从 Docker 运行模式切换到宿主机运行模式
> ```bash
> # GitHub 源
> wget -O 1panel_docker_to_sys.sh https://raw.githubusercontent.com/okxlin/ToolScript/refs/heads/main/1Panel/1panel-execution-mode/1panel_docker_to_sys.sh

> # 下载完成后，添加执行权限并运行
> chmod +x 1panel_docker_to_sys.sh && bash 1panel_docker_to_sys.sh
> ```
>
> **步骤 2**: 使用官方升级工具将 V1 升级到 V2
> - 参考官方文档: https://1panel.cn/docs/v2/installation/v1_migrate/
>
> **步骤 3**: 升级完成后，如需切换回 Docker 运行模式，可重新使用迁移脚本切换回 Docker 运行模式

### Q3: 容器内如何执行 1pctl 命令?

```bash
# 进入容器
docker exec -it 1panel bash

# V1 执行命令
1pctl version

# V2 执行命令
1pctl version
```

### Q4: 如何查看容器日志?

```bash
# V1
docker logs 1panel

# V2
docker logs 1panel-v2

# 实时查看
docker logs -f 1panel-v2
```

---

## 相关链接

- [1Panel 官网](https://1panel.cn)
- [1Panel 文档](https://1panel.cn/docs)
- [1Panel GitHub](https://github.com/1Panel-dev/1Panel)
- [源项目 GitHub](https://github.com/okxlin/docker-1panel)
- [本项目 GitHub](https://github.com/forza0310/docker-1panel)
- [Docker Hub](https://hub.docker.com/r/forza0310/1panel)

---

## 声明

本项目部分文档内容由 AI 辅助生成。
