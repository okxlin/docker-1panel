# 阶段1: 构建阶段
FROM ubuntu:22.04 as builder

# 安装所需的软件包并清理
RUN apt-get update && apt-get install -y \
    tar \
    curl \
    gnupg \
    apt-transport-https \
    software-properties-common \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 添加 Docker 仓库
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg && \
    echo "deb [arch="$(dpkg --print-architecture)" signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list

# 安装 Docker CLI
RUN apt-get update && apt-get install -y docker-ce-cli && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 安装 Docker Compose
RUN curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose && \
    ln -s /usr/local/bin/docker-compose /usr/bin/docker-compose  && \
    chmod +x /usr/local/bin/docker-compose

# 设置工作目录
WORKDIR /app

# 创建 Docker 套接字的卷
VOLUME /var/run/docker.sock

# 复制必要的文件
COPY ./install.override.sh .

ARG PANELVER=$PANELVER

# 下载并安装 1Panel
RUN INSTALL_MODE="stable" && \
    ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "armhf" ]; then ARCH="armv7"; fi && \
    if [ "$ARCH" = "ppc64el" ]; then ARCH="ppc64le"; fi && \
    package_file_name="1panel-${PANELVER}-linux-${ARCH}.tar.gz" && \
    package_download_url="https://resource.fit2cloud.com/1panel/package/${INSTALL_MODE}/${PANELVER}/release/${package_file_name}" && \
    echo "Downloading ${package_download_url}" && \
    curl -sSL -o ${package_file_name} "$package_download_url" && \
    tar zxvf ${package_file_name} --strip-components 1 && \
    rm /app/install.sh && \
    mv -f /app/install.override.sh /app/install.sh && \
    chmod +x /app/install.sh && \
    rm ${package_file_name} && \
    mv /app/1panel.service /app/1panel.service.bak && \
    bash /app/install.sh

# 阶段2: 运行阶段
FROM ubuntu:22.04

# 复制已安装的可执行文件和设置
COPY --from=builder /usr/local/bin/docker-compose /usr/local/bin/docker-compose
COPY --from=builder /usr/bin/docker-compose /usr/bin/docker-compose
COPY --from=builder /usr/bin/docker /usr/bin/docker
COPY --from=builder /usr/local/bin/1panel /usr/local/bin/1panel
COPY --from=builder /usr/local/bin/1pctl /usr/local/bin/1pctl
COPY --from=builder /usr/bin/1pctl /usr/bin/1pctl

# 设置时区
ENV TZ=Asia/Shanghai

# 安装所需的软件包并清理
RUN apt-get update && apt-get install -y \
    wget \
    tar \
    unzip \
    curl \
    git \
    sudo \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 设置工作目录
WORKDIR /

# 暴露端口
EXPOSE 10086

# 创建 Docker 套接字的卷
VOLUME /var/run/docker.sock

# 启动
CMD ["/usr/local/bin/1panel"]