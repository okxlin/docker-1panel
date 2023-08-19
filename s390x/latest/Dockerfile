FROM debian:bullseye-slim

# 安装所需的软件包并清理
RUN apt-get update && apt-get install -y \
    wget \
    tar \
    unzip \
    curl \
    gnupg \
    git \
    #openssh-server \
    apt-transport-https \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装 Docker
RUN curl -fsSL https://get.docker.com | bash -s docker

# 清理
RUN apt-get clean && rm -rf /var/lib/apt/lists/*

# 安装 Docker Compose
RUN curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose && \
    ln -s /usr/local/bin/docker-compose /usr/bin/docker-compose  && \
    chmod +x /usr/local/bin/docker-compose

# 设置工作目录
WORKDIR /app

# 创建 Docker 套接字的卷
VOLUME /var/run/docker.sock

# 复制必要的文件
COPY ./install.sh .

# 设置安装模式和架构
ARG INSTALL_MODE="stable"
ARG architecture="s390x"

# 下载并安装 1Panel
RUN VERSION=$(curl -s https://resource.fit2cloud.com/1panel/package/${INSTALL_MODE}/latest) && \
    package_file_name="1panel-${VERSION}-linux-${architecture}.tar.gz" && \
    package_download_url="https://resource.fit2cloud.com/1panel/package/${INSTALL_MODE}/${VERSION}/release/${package_file_name}" && \
    wget "$package_download_url" && \
    tar zxvf ${package_file_name} && \
    mv "1panel-${VERSION}-linux-${architecture}" src && \
    rm /app/src/install.sh && \
    cp /app/install.sh /app/src/ && \
    chmod +x /app/src/install.sh && \
    rm ${package_file_name} && \
    rm /app/install.sh && \
    rm /app/src/README.md && \
    rm /app/src/LICENSE && \
    rm /app/src/1panel.service && \
    bash /app/src/install.sh

# 设置工作目录
WORKDIR /app/src

# 启动
CMD ["/usr/local/bin/1panel"]