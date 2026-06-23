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
PASS=${PASSWORD:-}
RESET=${RESET:-"false"}

log() {
    echo "[Entrypoint] $(date '+%H:%M:%S') - $1"
}

die() {
    log "错误: $1"
    exit 1
}

has_control_chars() {
    printf '%s' "$1" | LC_ALL=C grep -q '[[:cntrl:]]'
}

validate_env() {
    if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
        die "PORT 必须是 1-65535 之间的数字"
    fi

    if [ -z "$ENT" ] || ! [[ "$ENT" =~ ^[A-Za-z0-9._-]+$ ]]; then
        die "ENTRANCE 只能包含字母、数字、点、下划线和连字符"
    fi

    if [ -z "$USER" ] || ! [[ "$USER" =~ ^[A-Za-z0-9._@-]+$ ]]; then
        die "USERNAME 只能包含字母、数字、点、下划线、@ 和连字符"
    fi

    if [ -z "$EXT_DIR" ] || [[ "$EXT_DIR" != /* ]] || ! [[ "$EXT_DIR" =~ ^/[A-Za-z0-9._/@+-]*$ ]]; then
        die "BASE_DIR 必须是绝对路径，且只能包含常见路径字符"
    fi

    if has_control_chars "$PASS"; then
        die "PASSWORD 不能包含控制字符"
    fi
}

sql_escape() {
    printf '%s' "$1" | sed "s/'/''/g"
}

sed_replacement_escape() {
    printf '%s' "$1" | sed 's/[|&\\]/\\&/g'
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
        local now
        local key_sql val_sql now_sql
        now=$(date '+%Y-%m-%d %H:%M:%S')
        key_sql=$(sql_escape "$key")
        val_sql=$(sql_escape "$val")
        now_sql=$(sql_escape "$now")
        
        # 在同一个连接中执行 UPDATE 和 changes()
        local changes
        changes=$(sqlite3 "$db" "UPDATE settings SET value = '$val_sql', updated_at = '$now_sql' WHERE key = '$key_sql'; SELECT changes();")
        
        # 如果 changes 返回 0，说明 Key 不存在，执行插入
        if [ "$changes" -eq 0 ]; then
            log "Key [$key] 不存在，执行插入..."
            sqlite3 "$db" "INSERT INTO settings (created_at, updated_at, key, value) VALUES ('$now_sql', '$now_sql', '$key_sql', '$val_sql');"
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
        EXT_DIR_ESC=$(sed_replacement_escape "$EXT_DIR")
        USER_ESC=$(sed_replacement_escape "$USER")
        sed -i "s|BASE_DIR=.*|BASE_DIR=${EXT_DIR_ESC}|g" /usr/local/bin/1pctl
        sed -i "s|ORIGINAL_PORT=.*|ORIGINAL_PORT=${PORT}|g" /usr/local/bin/1pctl
        sed -i "s|ORIGINAL_ENTRANCE=.*|ORIGINAL_ENTRANCE=/${ENT}|g" /usr/local/bin/1pctl
        sed -i "s|ORIGINAL_USERNAME=.*|ORIGINAL_USERNAME=${USER_ESC}|g" /usr/local/bin/1pctl
    fi

    if [ ! -f "${DATA}/db/1Panel.db" ]; then
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
                head /dev/urandom | tr -dc A-Za-z0-9 | head -c 16 > "${DATA}/tmp/.secret"
            fi
        fi
    fi
    
    if [ -f "${DATA}/db/1Panel.db" ]; then
        log "清理数据库重复项..."
        remove_duplicates "${DATA}/db/1Panel.db"
    fi
    
    if [ -f "/usr/local/bin/1pctl" ] && [ -f "${DATA}/db/1Panel.db" ]; then
        CURRENT_VER=$(grep "^ORIGINAL_VERSION=" /usr/local/bin/1pctl | cut -d'=' -f2)
        if [ -n "$CURRENT_VER" ]; then
            log "同步系统版本: $CURRENT_VER"
            update_db_key "${DATA}/db/1Panel.db" "SystemVersion" "$CURRENT_VER"
        fi
    fi
}

config_online() {
    CURRENT_PORT=$(sqlite3 "${DATA}/db/1Panel.db" "SELECT value FROM settings WHERE key='ServerPort';")
    CURRENT_PORT=${CURRENT_PORT:-10086}
    
    log "等待服务启动 (端口: $CURRENT_PORT)..."
    
    MAX=60
    cnt=0
    while [ $cnt -lt $MAX ]; do
        if curl -s -o /dev/null "http://127.0.0.1:${CURRENT_PORT}"; then
            log "服务已就绪，开始配置..."
            sleep 3
            break
        fi
        sleep 1
        cnt=$((cnt + 1))
    done

    if [ ! -f "${DATA}/${FLAG}" ] || [ "${RESET}" == "true" ]; then
        
        if [ "${RESET}" == "true" ]; then
            log "执行强制重置..."
        else
            log "首次初始化..."
        fi
        
        GEN_RAND=false
        if [ -z "$PASS" ] || [ "$PASS" == "1panel_password" ]; then
            PASS=$(head /dev/urandom | tr -dc A-Za-z0-9 | head -c 16)
            GEN_RAND=true
        fi

        export PANEL_INIT_USER="$USER"
        export PANEL_INIT_PASS="$PASS"
        export PANEL_INIT_PORT="$PORT"

        /usr/bin/expect <<'EOF' >/dev/stdout 2>&1
set timeout 30
spawn /usr/local/bin/1pctl update username
expect {
    "user:" { send -- "$env(PANEL_INIT_USER)\r" }
    timeout { exit 1 }
}
expect eof
spawn /usr/local/bin/1pctl update password
expect {
    "password:" { send -- "$env(PANEL_INIT_PASS)\r" }
    timeout { exit 1 }
}
expect {
    "password:" { send -- "$env(PANEL_INIT_PASS)\r" }
    timeout { exit 1 }
}
expect eof
spawn /usr/local/bin/1pctl update port
expect {
    "port:" { send -- "$env(PANEL_INIT_PORT)\r" }
    timeout { exit 1 }
}
expect eof
EOF
        RET=$?

        if [ $RET -eq 0 ]; then
            log "基础参数配置成功。"
            
            log "正在设置安全入口: $ENT"
            update_db_key "${DATA}/db/1Panel.db" "SecurityEntrance" "$ENT"
            
            log "重启面板服务以应用新配置..."
            /usr/local/bin/1pctl restart
            
            sleep 5
            log "验证新端口: $PORT"
            if curl -s -o /dev/null "http://127.0.0.1:${PORT}"; then
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
        unset PASS PANEL_INIT_USER PANEL_INIT_PASS PANEL_INIT_PORT
    fi
}

main() {
    validate_env
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
