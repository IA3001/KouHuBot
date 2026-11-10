#!/usr/bin/env bash
# Cloud plugin updater. Never pulls/builds/pushes a Docker image.
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  printf '%s\n' '用法：bash update.sh [仓库目录]' '默认仓库：脚本所在目录；下载到临时位置时使用 ~/KouHuBot。' '仅 Git 快进更新 + 固定当前运行镜像 + 挂载插件 + 重启；不拉镜像、不删除数据。' '私有仓库沿用服务器已有 Git 凭证。不支持含未提交改动的工作区。'
  exit 0
fi

# Execute a snapshot: git pull may replace this script while Bash reads it.
if [[ "${KOUHU_UPDATE_SNAPSHOT:-}" != "1" ]]; then
  origin_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  repo="${1:-}"
  if [[ -z "$repo" ]]; then
    if [[ -d "$origin_dir/.git" ]]; then repo="$origin_dir"; else repo="$HOME/KouHuBot"; fi
  fi
  snapshot="$(mktemp /tmp/kouhu-update.XXXXXXXX.sh)"
  chmod 700 "$snapshot"
  cp -- "${BASH_SOURCE[0]}" "$snapshot"
  # Snapshot path is created by mktemp and fixed for the lifetime of this call.
  trap 'rm -f -- "$snapshot"' EXIT
  KOUHU_UPDATE_SNAPSHOT=1 bash "$snapshot" "$repo"
  exit $?
fi

repo="$(cd -- "${1:?缺少仓库目录}" && pwd -P)"
cd -- "$repo"
[[ "$(git rev-parse --show-toplevel)" == "$repo" ]] || { echo '错误：请指定KouHuBot仓库根目录。' >&2; exit 1; }
[[ -f deploy/docker-compose.yml && -d plugins/astrbot_plugin_kouhu_oj ]] || { echo '错误：目录不是KouHuBot仓库。' >&2; exit 1; }
command -v flock >/dev/null || { echo '错误：需要flock（util-linux）。' >&2; exit 1; }
exec 9>"$(git rev-parse --git-path kouhu-update.lock)"
flock -n 9 || { echo '已有更新正在进行。' >&2; exit 1; }
trap 'printf "更新失败（第%s行），已停止。不会继续重启、清理数据或拉取镜像。请保留以上错误信息。\n" "$LINENO" >&2' ERR

if [[ -n "$(git status --porcelain)" ]]; then
  echo '错误：工作区存在本地改动，更新已停止。先运行 git status 检查；脚本不会强制覆盖。' >&2
  exit 1
fi
branch="$(git branch --show-current)"
[[ -n "$branch" ]] || { echo '错误：当前处于分离HEAD状态。' >&2; exit 1; }
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}')"
printf '仓库：%s\n分支：%s → %s\n' "$repo" "$branch" "$upstream"

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  echo '当前用户无法访问Docker，使用sudo（可能需要输入密码）。'
  sudo -v
  DOCKER=(sudo docker)
  "${DOCKER[@]}" info >/dev/null
fi
"${DOCKER[@]}" compose version >/dev/null
image_id="$("${DOCKER[@]}" inspect --format '{{.Image}}' astrbot)"
[[ "$image_id" == sha256:* ]] || { echo '错误：无法确定现有astrbot镜像。' >&2; exit 1; }

before="$(git rev-parse HEAD)"
echo '拉取Git增量更新（不下载Docker镜像）……'
if ! git pull --ff-only; then
  echo 'Git更新失败，尚未修改容器。若提示data目录Permission denied，先修复目录权限，再重新执行本脚本；不要忽略错误。' >&2
  exit 1
fi
after="$(git rev-parse HEAD)"
[[ -f deploy/compose.plugin.yml ]] || { echo '缺少插件挂载配置，已停止。' >&2; exit 1; }
compose=("${DOCKER[@]}" compose --project-directory "$repo/deploy" -f "$repo/deploy/docker-compose.yml" -f "$repo/deploy/compose.plugin.yml")
"${compose[@]}" config --quiet

# A local tag adds no copy of image layers. Never pull or build an image here.
"${DOCKER[@]}" tag "$image_id" kouhubot-runtime:local
expected="$(sha256sum plugins/astrbot_plugin_kouhu_oj/main.py | cut -d ' ' -f 1)"
echo '使用现有镜像，切换/保持插件只读挂载……'
container_before="$("${DOCKER[@]}" inspect --format '{{.Id}}' astrbot)"
"${compose[@]}" up -d --pull never --no-build astrbot
container_after="$("${DOCKER[@]}" inspect --format '{{.Id}}' astrbot)"
# up doesn't restart a container when only bind-mounted source bytes changed.
if [[ "$container_before" == "$container_after" ]]; then
  "${compose[@]}" restart astrbot
fi
actual="$("${DOCKER[@]}" exec astrbot python -c 'import hashlib;from pathlib import Path;print(hashlib.sha256(Path("/AstrBot/data/plugins/astrbot_plugin_kouhu_oj/main.py").read_bytes()).hexdigest())')"
[[ "$actual" == "$expected" ]] || { echo '错误：容器加载目录与Git源码不一致。' >&2; exit 1; }
state="$("${DOCKER[@]}" inspect --format '{{.State.Status}}' astrbot)"
[[ "$state" == running ]] || { echo "错误：容器状态为 $state，请检查docker logs astrbot。" >&2; exit 1; }
printf '\n更新完成：%s → %s\n运行镜像：%s（沿用本地，无拉取）\n容器加载目录源码哈希一致；QQ连接和模型可用性请通过实际指令验证。\n' "${before:0:8}" "${after:0:8}" "$image_id"
echo '最近启动日志：'
"${DOCKER[@]}" logs --since 30s --tail 35 astrbot
