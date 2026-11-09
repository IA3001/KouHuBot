#!/bin/sh
# 首次启动把镜像里自带的插件铺到 data 目录。
#
# 为什么要这么做：AstrBot 的插件目录是 data/plugins，而 data/ 通常挂载成
# 数据卷。如果插件只放在镜像的 data/ 里，一挂卷就被盖住了。所以镜像里
# 放在 /opt/kouhu，启动时再同步进去。
set -eu

PLUGIN_NAME=astrbot_plugin_kouhu_oj
SRC="/opt/kouhu/${PLUGIN_NAME}"
DST="/AstrBot/data/plugins/${PLUGIN_NAME}"

mkdir -p /AstrBot/data/plugins

# 升级镜像后插件版本可能变了，用 KOUHU_PLUGIN_SYNC=force 强制覆盖数据卷里的旧版
if [ "${KOUHU_PLUGIN_SYNC:-}" = "force" ] || [ ! -d "${DST}" ]; then
    rm -rf "${DST}"
    cp -a "${SRC}" "${DST}"
    echo "[kouhu] 插件已安装到 ${DST}"
else
    echo "[kouhu] 插件已存在：${DST}"
    echo "[kouhu] 要更新成镜像自带版本，设置 KOUHU_PLUGIN_SYNC=force 重启"
fi

exec "$@"
