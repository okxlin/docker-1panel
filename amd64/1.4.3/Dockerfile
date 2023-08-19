FROM debian:bullseye-slim

# 安装所需的软件包并清理
RUN apt-get update && apt-get install -y \
    wget \
    tar \
    unzip \
    curl \
    gnupg \
    openssh-server \
    apt-transport-https \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 设置 Docker 仓库
RUN curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian bullseye stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

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
COPY ./install.sh .

# 下载并安装 1Panel
RUN wget https://github.com/wanghe-fit2cloud/1Panel/releases/download/v1.4.3/1panel-v1.4.3-linux-amd64.tar.gz && \
    tar zxvf 1panel-v1.4.3-linux-amd64.tar.gz && \
    mv 1panel-v1.4.3-linux-amd64 src && \
    rm /app/src/install.sh && \
    cp /app/install.sh /app/src/ && \
    chmod +x /app/src/install.sh && \
    rm /app/1panel-v1.4.3-linux-amd64.tar.gz && \
    rm /app/install.sh && \
    rm /app/src/README.md && \
    rm /app/src/LICENSE && \
    rm /app/src/1panel.service && \
    bash /app/src/install.sh

# 设置工作目录
WORKDIR /app/src

# 启动
CMD ["/usr/local/bin/1panel"]