#!/bin/sh
# 把镜像里自带的插件同步到 data/plugins。
#
# 为什么要这么做：AstrBot 的插件目录是 data/plugins，而 data/ 通常挂载成
# 数据卷。如果插件只放在镜像的 data/ 里，一挂卷就被盖住了。所以镜像里
# 放在 /opt/kouhu，启动时再同步进去。
#
# 什么时候同步：
#   1. 数据卷里还没有插件           → 首次安装
#   2. 数据卷里的版本和镜像不一致   → 升级（自动，不用手动干预）
#   3. KOUHU_PLUGIN_SYNC=force     → 强制覆盖
#
# 第 2 条是踩坑换来的：以前只认「目录不存在」，结果升级镜像后插件还是旧的，
# 群里表现是「新功能没有、老 bug 还在」，而日志一切正常，很难往这上面想。
set -eu

PLUGIN_NAME=astrbot_plugin_kouhu_oj
SRC="/opt/kouhu/${PLUGIN_NAME}"
DST="/AstrBot/data/plugins/${PLUGIN_NAME}"

mkdir -p /AstrBot/data/plugins

src_ver=$(sed -n 's/^version:[[:space:]]*//p' "${SRC}/metadata.yaml" 2>/dev/null | head -n1)
dst_ver=$(sed -n 's/^version:[[:space:]]*//p' "${DST}/metadata.yaml" 2>/dev/null | head -n1)

if [ "${KOUHU_PLUGIN_SYNC:-}" = "force" ] || [ ! -d "${DST}" ] || [ "${src_ver}" != "${dst_ver}" ]; then
    rm -rf "${DST}"
    cp -a "${SRC}" "${DST}"
    echo "[kouhu] 插件已同步到 ${DST}（${dst_ver:-无} → ${src_ver:-未知}）"
else
    echo "[kouhu] 插件已是最新版本 ${src_ver}，跳过同步"
fi

exec "$@"
