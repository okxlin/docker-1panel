#!/bin/bash
set -e

INT_DIR="/opt"
INT_APP_DIR="${INT_DIR}/1panel"

EXT_DIR=${BASE_DIR:-"/opt"}
EXT_APP_DIR="${EXT_DIR}/1panel"

DATA=$INT_APP_DIR
TPL="/usr/share/1panel-default"
FLAG=".docker_initialized"

PORT=${PORT:-10086}
ENT=$(echo "${ENTRANCE:-entrance}" | sed 's|^/||' | sed 's|/$||')
USER=${USERNAME:-"1panel"}
PASS=${PASSWORD:-$PASSWORD}
RESET=${RESET:-"false"}

log() {
    echo "[Entrypoint] $(date '+%H:%M:%S') - $1"
}

remove_duplicates() {
    local db=$1
    if [ -f "$db" ]; then
        local sql="DELETE FROM settings WHERE rowid NOT IN (SELECT MAX(rowid) FROM settings GROUP BY key);"
        sqlite3 "$db" "$sql"
    fi
}

update_db_key() {
    local db=$1
    local key=$2
    local val=$3
    
    if [ -f "$db" ]; then
        # 获取当前时间，格式对齐 1Panel 数据库风格 (例如 2024-05-20 12:00:00)
        local now=$(date '+%Y-%m-%d %H:%M:%S')
        
        # [核心修复] 在同一个连接中执行 UPDATE 和 changes()
        local changes=$(sqlite3 "$db" "UPDATE settings SET value = '$val', updated_at = '$now' WHERE key = '$key'; SELECT changes();")
        
        # 如果 changes 返回 0，说明 Key 不存在，执行插入
        if [ "$changes" -eq 0 ]; then
            log "Key [$key] 不存在，执行插入..."
            sqlite3 "$db" "INSERT INTO settings (created_at, updated_at, key, value) VALUES ('$now', '$now', '$key', '$val');"
        fi
    fi
}

setup_symlink() {
    if [ "$EXT_DIR" != "$INT_DIR" ]; then
        log "映射路径: $EXT_APP_DIR -> $INT_APP_DIR"
        
        [ ! -d "$EXT_APP_DIR" ] && mkdir -p "$EXT_APP_DIR"

        if [ -d "$INT_APP_DIR" ] && [ ! -L "$INT_APP_DIR" ]; then
            rm -rf "$INT_APP_DIR"
        fi

        if [ ! -L "$INT_APP_DIR" ]; then
            ln -s "$EXT_APP_DIR" "$INT_APP_DIR"
        fi
    fi
}

init_offline() {
    if [ -f "/usr/local/bin/1pctl" ]; then
        # 1Panel 通过宿主机 docker.sock 创建业务容器，挂载源路径必须写成宿主机真实路径。
        # 这里要用 BASE_DIR(外部路径)，不能写死成容器内的 /opt。
        sed -i "s|BASE_DIR=.*|BASE_DIR=${EXT_DIR}|g" /usr/local/bin/1pctl
        sed -i "s|ORIGINAL_PORT=.*|ORIGINAL_PORT=${PORT}|g" /usr/local/bin/1pctl
        sed -i "s|ORIGINAL_ENTRANCE=.*|ORIGINAL_ENTRANCE=/${ENT}|g" /usr/local/bin/1pctl
        sed -i "s|ORIGINAL_USERNAME=.*|ORIGINAL_USERNAME=${USER}|g" /usr/local/bin/1pctl
    fi

    if [ ! -f "${DATA}/db/core.db" ]; then
        log "初始化数据文件..."
        mkdir -p "${DATA}"
        cp -rn ${TPL}/* "${DATA}/" || true
    else
        if [ ! -f "${DATA}/tmp/.secret" ]; then
            log "修复密钥文件..."
            mkdir -p "${DATA}/tmp"
            if [ -f "${TPL}/tmp/.secret" ]; then
                cp "${TPL}/tmp/.secret" "${DATA}/tmp/.secret"
            else
                echo "$(head /dev/urandom | tr -dc A-Za-z0-9 | head -c 16)" > "${DATA}/tmp/.secret"
            fi
        fi
    fi
    
    if [ -f "${DATA}/db/core.db" ]; then
        log "清理 Core 数据库重复项..."
        remove_duplicates "${DATA}/db/core.db"
    fi
    if [ -f "${DATA}/db/agent.db" ]; then
        log "清理 Agent 数据库重复项..."
        remove_duplicates "${DATA}/db/agent.db"
    fi
    
    log "同步 Core 配置..."
    if [ -f "${DATA}/db/core.db" ]; then
        update_db_key "${DATA}/db/core.db" "SystemPort" "$PORT"
    fi
    
    log "同步 Agent 配置..."
    if [ -f "${DATA}/db/agent.db" ]; then
        update_db_key "${DATA}/db/agent.db" "BaseDir" "${EXT_DIR}"
        update_db_key "${DATA}/db/agent.db" "NodePort" "$PORT"
    fi

    if [ -f "/usr/local/bin/1pctl" ]; then
        CURRENT_VER=$(grep "^ORIGINAL_VERSION=" /usr/local/bin/1pctl | cut -d'=' -f2)
        if [ -n "$CURRENT_VER" ]; then
            log "同步系统版本: $CURRENT_VER"
            if [ -f "${DATA}/db/core.db" ]; then
                update_db_key "${DATA}/db/core.db" "SystemVersion" "$CURRENT_VER"
            fi
            if [ -f "${DATA}/db/agent.db" ]; then
                update_db_key "${DATA}/db/agent.db" "SystemVersion" "$CURRENT_VER"
            fi
        fi
    fi
}

config_online() {
    CURRENT_PORT=$(sqlite3 "${DATA}/db/core.db" "SELECT value FROM settings WHERE key='SystemPort';")
    CURRENT_PORT=${CURRENT_PORT:-10086}
    
    log "等待服务启动 (端口: $CURRENT_PORT)..."
    
    MAX=60
    cnt=0
    while [ $cnt -lt $MAX ]; do
        if curl -s -o /dev/null http://127.0.0.1:${CURRENT_PORT}; then
            log "服务已就绪，开始配置..."
            sleep 3
            break
        fi
        sleep 1
        cnt=$((cnt + 1))
    done

    if [ ! -f "${DATA}/${FLAG}" ] || [ "${RESET}" == "true" ]; then
        
        [ "${RESET}" == "true" ] && log "执行强制重置..." || log "首次初始化..."
        
        GEN_RAND=false
        if [ -z "$PASS" ] || [ "$PASS" == "1panel_password" ]; then
            PASS=$(head /dev/urandom | tr -dc A-Za-z0-9 | head -c 16)
            GEN_RAND=true
        fi

        /usr/bin/expect <<EOF >/dev/stdout 2>&1
set timeout 30
spawn /usr/local/bin/1pctl update username
expect {
    "user:" { send "$USER\r" }
    timeout { exit 1 }
}
expect eof
spawn /usr/local/bin/1pctl update password
expect {
    "password:" { send "$PASS\r" }
    timeout { exit 1 }
}
expect {
    "password:" { send "$PASS\r" }
    timeout { exit 1 }
}
expect eof
spawn /usr/local/bin/1pctl update port
expect {
    "port:" { send "$PORT\r" }
    timeout { exit 1 }
}
expect eof
EOF
        RET=$?

        if [ $RET -eq 0 ]; then
            log "基础参数配置成功。"
            
            log "正在设置安全入口: $ENT"
            update_db_key "${DATA}/db/core.db" "SecurityEntrance" "$ENT"
            
            log "重启面板服务以应用新配置..."
            /usr/local/bin/1pctl restart
            
            sleep 5
            log "验证新端口: $PORT"
            if curl -s -o /dev/null http://127.0.0.1:${PORT}; then
                log "✅ 所有配置已生效。"
                touch "${DATA}/${FLAG}"
                if [ "$GEN_RAND" == "true" ]; then
                    echo "----------------------------------------"
                    echo " [随机密码] $PASS"
                    echo "----------------------------------------"
                fi
            else
                log "⚠️ 警告：服务重启后未在新端口响应，请检查日志。"
            fi
        else
            log "❌ 配置修改失败。"
        fi
        unset PASS
    fi
}

main() {
    setup_symlink
    init_offline
    config_online &
    
    unset PASSWORD USERNAME RESET
    
    if [ -f "${DATA}/tmp/.secret" ]; then
        chmod 600 "${DATA}/tmp/.secret"
    fi

    log "启动 Supervisor..."
    exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
}

main