#!/bin/sh
# Runtime image contains dependencies only; plugin source is supplied by a mount.
set -eu
PLUGIN_DIR=/AstrBot/data/plugins/astrbot_plugin_kouhu_oj
if [ ! -f "${PLUGIN_DIR}/metadata.yaml" ] || [ ! -f "${PLUGIN_DIR}/main.py" ]; then
    echo '[kouhu] 缺少挂载插件。请使用仓库deploy配置挂载plugins/astrbot_plugin_kouhu_oj。' >&2
    exit 1
fi
echo '[kouhu] 使用挂载插件；基础镜像不包含或覆盖插件代码。'
exec "$@"
