#!/usr/bin/env bash
# ============================================================
# 2C2G 服务器预备脚本
#
# 作用：
#   1. 创建 2GB swap（云服务器默认通常没有 swap，2G 内存极易 OOM Kill）
#   2. 调低 swappiness，让 swap 只作为"应急垫"而不是常态
#   3. 写入 /etc/fstab 保证重启后仍生效
#
# 用法：
#   sudo bash setup-swap.sh
#
# 幂等：重复执行不会重复创建。
# ============================================================
set -euo pipefail

SWAP_FILE="/swapfile"
SWAP_SIZE="2G"
SWAPPINESS=10

if [[ $EUID -ne 0 ]]; then
  echo "请用 sudo 运行：sudo bash $0" >&2
  exit 1
fi

if swapon --show | grep -q "$SWAP_FILE"; then
  echo "[跳过] $SWAP_FILE 已启用"
else
  if [[ ! -f $SWAP_FILE ]]; then
    echo "[1/4] 创建 ${SWAP_SIZE} swap 文件..."
    # fallocate 在部分文件系统（如 btrfs）上不可用，用 dd 兜底
    fallocate -l "$SWAP_SIZE" "$SWAP_FILE" 2>/dev/null || \
      dd if=/dev/zero of="$SWAP_FILE" bs=1M count=2048 status=progress
    chmod 600 "$SWAP_FILE"
    mkswap "$SWAP_FILE"
  else
    echo "[1/4] $SWAP_FILE 已存在，跳过创建"
  fi

  echo "[2/4] 启用 swap..."
  swapon "$SWAP_FILE"

  echo "[3/4] 写入 /etc/fstab..."
  if ! grep -qE "^\s*${SWAP_FILE}\s" /etc/fstab; then
    echo "${SWAP_FILE} none swap sw 0 0" >> /etc/fstab
  fi
fi

echo "[4/4] 设置 vm.swappiness=${SWAPPINESS}..."
if grep -qE '^\s*vm\.swappiness' /etc/sysctl.conf; then
  sed -i -E "s/^\s*vm\.swappiness.*/vm.swappiness=${SWAPPINESS}/" /etc/sysctl.conf
else
  echo "vm.swappiness=${SWAPPINESS}" >> /etc/sysctl.conf
fi
sysctl -p >/dev/null

echo
echo "===== 完成 ====="
free -h
echo
swapon --show
