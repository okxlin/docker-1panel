- [简介](#简介)
- [1. 注意事项](#1-注意事项)
- [2. docker方式安装](#2-docker方式安装)
- [3. docker-compose方式安装](#3-docker-compose方式安装)
- [4. 修改面板显示版本](#4-修改面板显示版本)
  - [4.1 安装`SQLite3`](#41-安装sqlite3)
  - [4.2 修改面板显示版本](#42-修改面板显示版本)
- [5. 镜像编译](#5-镜像编译)

***

## 简介

偶然看到[**1panel-dood**](https://github.com/tangger2000/1panel-dood)的关于`docker`部署`1panel`的方法，确实好想法，点赞。

受到启发编写了一下相关文件，把`1panel`套娃一下。

与[**1panel-dood**](https://github.com/tangger2000/1panel-dood)有所不同的是，我是以替换二进制文件的形式来的，

因为如果使用原始安装脚本作为启动命令，当更换容器时，需要事先备份数据库文件，否则会出现数据库覆盖问题。

单主程序的好处了，正好是和之前适配[1Panel 应用商店的非官方应用适配库](https://github.com/okxlin/appstore)写的`GO`语言的应用的`Dockerfile`异曲同工。

## 1. 注意事项

由于容器内部`systemd`限制，部分功能目前尚不完整，等待后面找一个好使的`systemctl`镜像来运行。

如果更新了更高版本的镜像，实际是更新了对应版本的二进制程序，面板显示的相关版本还需要手动更新。

相关操作查看下文。
***
- 默认端口：`10086`
- 默认账户：`1panel`
- 默认密码：`1panel_password`
- 默认入口：`entrance`
***
- 不可调整参数
  - `/var/run/docker.sock`的相关映射
 ***
- 可调整参数
> **推荐使用/opt路径，否则有些调用本地文件的应用可能出现异常**
  - `/opt:/opt`                        文件存储映射
  - `TZ=Asia/Shanghai`                        时区设置
  - `1panel`                          容器名
  - `/var/lib/docker/volumes:/var/lib/docker/volumes` 存储卷映射
***
**架构平台对应镜像**
- amd64
- arm64
- armv7
- ppc64le
- s390x
> 2023年9月3日已经更新单标签多镜像
```
docker pull moelin/1panel:latest
```

## 2. docker方式安装
```
docker run -d \
    --name 1panel \
    --restart always \
    --network host \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /var/lib/docker/volumes:/var/lib/docker/volumes \
    -v /opt:/opt \
    -e TZ=Asia/Shanghai \
    moelin/1panel:latest
```

## 3. docker-compose方式安装

创建一个`docker-compose.yml`文件，内容类似如下
```
version: '3'
services:
  1panel:
    container_name: 1panel # 容器名
    restart: always
    network_mode: "host"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /var/lib/docker/volumes:/var/lib/docker/volumes
      - /opt:/opt  # 文件存储映射
    environment:
      - TZ=Asia/Shanghai  # 时区设置
    image: moelin/1panel:latest
    labels:  
      createdBy: "Apps"
```

然后`docker-compose up -d`运行

## 4. 修改面板显示版本
### 4.1 安装`SQLite3`

以`Debian`系统为例，其他系统对应更改包管理器命令。
- Debian/Ubuntu: apt-get
- RedHat/CentOS: yum

```
# 更新软件包列表
apt-get update

# 安装 SQLite3，并自动回答所有提示为“是”
apt-get install sqlite3 -y
```
### 4.2 修改面板显示版本
- 获取文件存储实际路径

在宿主机上的实际路径，假设如下
```
/opt
```

- 备份旧数据库
```
# 将原始数据库文件备份为 .bak 文件
cp /opt/1panel/db/1Panel.db /opt/1panel/db/1Panel.db.bak
```

- 打开数据库文件
```
# 打开 SQLite3 数据库
sqlite3 /opt/1panel/db/1Panel.db
```

- 修改版本信息，按需修改`v1.5.2`
```
UPDATE settings
SET value = 'v1.5.2'
WHERE key = 'SystemVersion';
```

- 退出修改
```
.exit
```
- 重启面板应用更改
```
# 重新启动 1panel 容器
docker restart 1panel
```

## 5. 镜像编译

```
docker build --build-arg PANELVER=your_desired_version -t your_image_name:tag .

```
例子：
```
docker build --build-arg PANELVER=v1.4.3 -t 1panel:1.4.3 .

```
