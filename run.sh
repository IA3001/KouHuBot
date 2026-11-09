#!/usr/bin/env bash
# ============================================================
# KouHuBot 本地快速启动入口
#
#   ./run.sh              一键启动（首次会自动下载 AstrBot、装依赖、同步插件）
#   ./run.sh start        同上
#   ./run.sh stop         停止
#   ./run.sh restart      重启
#   ./run.sh status       查看运行状态
#   ./run.sh password     重置并显示 WebUI 登录密码
#   ./run.sh logs         跟踪日志
#   ./run.sh setup        只准备环境，不启动
#
# 说明：这是「本地调试」入口，直接跑 AstrBot 源码，不构建 Docker 镜像。
#       生产部署请用 deploy/docker-compose.yml。
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DIR="$REPO_ROOT/.local"
ASTRBOT_DIR="$LOCAL_DIR/astrbot"
VENV_DIR="$LOCAL_DIR/venv"
PY="$VENV_DIR/bin/python"
LOG_FILE="$LOCAL_DIR/astrbot.log"
PID_FILE="$LOCAL_DIR/astrbot.pid"
PLUGIN_SRC="$REPO_ROOT/plugins/astrbot_plugin_kouhu_oj"
PLUGIN_DST="$ASTRBOT_DIR/data/plugins/astrbot_plugin_kouhu_oj"
ASTRBOT_REPO="https://github.com/AstrBotDevs/AstrBot"
PORT="${KOUHU_PORT:-6185}"

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_DIM=$'\033[2m'; C_END=$'\033[0m'
info()  { printf '%s[·]%s %s\n' "$C_DIM" "$C_END" "$*"; }
ok()    { printf '%s[✓]%s %s\n' "$C_OK" "$C_END" "$*"; }
warn()  { printf '%s[!]%s %s\n' "$C_WARN" "$C_END" "$*"; }
die()   { printf '%s[✗]%s %s\n' "$C_ERR" "$C_END" "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 环境准备
ensure_source() {
  if [[ -f "$ASTRBOT_DIR/main.py" ]]; then
    info "AstrBot 源码已存在"
    return
  fi
  info "下载 AstrBot 源码（首次约 37MB）..."
  mkdir -p "$LOCAL_DIR"
  git clone --depth 1 --quiet "$ASTRBOT_REPO" "$ASTRBOT_DIR" \
    || die "AstrBot 克隆失败，检查网络"
  ok "AstrBot 源码就绪"
}

ensure_venv() {
  if [[ -x "$PY" ]]; then
    info "Python 虚拟环境已存在"
    return
  fi
  info "创建虚拟环境..."
  python3 -m venv "$VENV_DIR" || die "创建 venv 失败（需要 python3-venv）"
  "$VENV_DIR/bin/pip" install -q -U pip 2>/dev/null || true
  ok "虚拟环境就绪"
}

ensure_deps() {
  if PYTHONPATH="$ASTRBOT_DIR" "$PY" -c "import astrbot" >/dev/null 2>&1; then
    info "AstrBot 依赖已安装"
    return
  fi
  info "安装 AstrBot 依赖（首次较慢，含 faiss/pandas，约 3-8 分钟）..."
  "$VENV_DIR/bin/pip" install -q -r "$ASTRBOT_DIR/requirements.txt" \
    || die "依赖安装失败"
  PYTHONPATH="$ASTRBOT_DIR" "$PY" -c "import astrbot" >/dev/null 2>&1 \
    || die "依赖装完了但 astrbot 仍无法导入"
  ok "依赖安装完成"
}

sync_plugin() {
  mkdir -p "$ASTRBOT_DIR/data/plugins"
  rm -rf "$PLUGIN_DST"
  cp -r "$PLUGIN_SRC" "$PLUGIN_DST"
  rm -rf "$PLUGIN_DST/__pycache__"
  ok "插件已同步到 data/plugins/"
}

# ---------------------------------------------------------------- 进程控制
running_pid() {
  if [[ -f "$PID_FILE" ]]; then
    local pid; pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "$pid"; return 0
    fi
  fi
  # 兜底：按命令行查找（模式只匹配 AstrBot 的 python 进程，不会匹配本脚本）
  local pid; pid="$(pgrep -f "$VENV_DIR/bin/python main.py" 2>/dev/null | head -1 || true)"
  [[ -n "$pid" ]] && { echo "$pid"; return 0; }
  return 1
}

port_open() { timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/$PORT" 2>/dev/null; }

do_status() {
  local pid
  if pid="$(running_pid)"; then
    ok "AstrBot 运行中（PID $pid）"
    if port_open; then
      echo "    WebUI : http://localhost:$PORT"
    else
      warn "进程在，但端口 $PORT 还没就绪（可能仍在启动）"
    fi
  else
    warn "AstrBot 未运行"
    return 1
  fi
}

do_start() {
  ensure_source
  ensure_venv
  ensure_deps
  sync_plugin

  if running_pid >/dev/null; then
    ok "AstrBot 已在运行，无需重复启动"
    do_status || true
    return
  fi

  info "启动 AstrBot ..."
  mkdir -p "$LOCAL_DIR"
  # setsid -f：fork 到独立会话后立即返回，父进程退出不影响 AstrBot
  ( cd "$ASTRBOT_DIR" && setsid -f "$PY" main.py >"$LOG_FILE" 2>&1 </dev/null )
  sleep 1

  local i
  for i in $(seq 1 60); do
    if port_open; then
      # 端口就绪后回填真实 PID（setsid -f 拿不到 $!）
      local real_pid
      real_pid="$(pgrep -f "$VENV_DIR/bin/python main.py" 2>/dev/null | head -1 || true)"
      [[ -n "$real_pid" ]] && echo "$real_pid" >"$PID_FILE"
      ok "启动成功"
      echo
      echo "    WebUI : http://localhost:$PORT"
      echo "    用户名: astrbot"
      echo "    日志  : $LOG_FILE"
      echo "    停止  : ./run.sh stop"
      echo
      if grep -q "KouHuOJ" "$LOG_FILE" 2>/dev/null; then
        ok "插件已加载：$(grep -o '题库 [0-9]* 条' "$LOG_FILE" | tail -1)"
      else
        warn "暂未看到插件加载日志，稍等几秒或看 ./run.sh logs"
      fi
      return
    fi
    sleep 1
  done

  warn "60 秒内端口未就绪，最近日志："
  tail -20 "$LOG_FILE" || true
  die "启动失败"
}

do_stop() {
  local pid
  if ! pid="$(running_pid)"; then
    warn "AstrBot 未运行"
    rm -f "$PID_FILE"
    return
  fi
  info "停止 AstrBot（PID $pid）..."
  kill "$pid" 2>/dev/null || true
  local i
  for i in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    warn "进程未响应，强制结束"
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  if port_open; then
    warn "端口 $PORT 仍被占用（可能有残留进程）"
    pgrep -af "$VENV_DIR/bin/python main.py" || true
  else
    ok "已停止"
  fi
}

do_password() {
  do_stop >/dev/null 2>&1 || true
  info "重置 WebUI 密码 ..."
  ( cd "$ASTRBOT_DIR" && setsid -f "$PY" main.py --reset-password >"$LOG_FILE" 2>&1 </dev/null )
  local i
  for i in $(seq 1 40); do
    if grep -qiE "密码|password" "$LOG_FILE" 2>/dev/null; then break; fi
    sleep 1
  done
  echo
  grep -iE "密码|password|username|用户名" "$LOG_FILE" | tail -8 || true
  echo
  ok "AstrBot 已用新密码启动：http://localhost:$PORT"
}

case "${1:-start}" in
  start)    do_start ;;
  stop)     do_stop ;;
  restart)  do_stop; do_start ;;
  status)   do_status ;;
  password) do_password ;;
  setup)    ensure_source; ensure_venv; ensure_deps; sync_plugin; ok "环境准备完成，运行 ./run.sh start 启动" ;;
  logs)     tail -f "$LOG_FILE" ;;
  *)
    cat <<EOF
KouHuBot 本地启动入口

  ./run.sh            一键启动（首次自动准备环境）
  ./run.sh stop       停止
  ./run.sh restart    重启
  ./run.sh status     查看状态
  ./run.sh password   重置并显示 WebUI 密码
  ./run.sh logs       跟踪日志
  ./run.sh setup      只准备环境

环境变量：KOUHU_PORT=6185  可改端口
EOF
    ;;
esac
