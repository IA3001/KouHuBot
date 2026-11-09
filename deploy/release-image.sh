#!/usr/bin/env bash
# 构建并推送多架构镜像到 Docker Hub。
#
#   ./deploy/release-image.sh              # 用 metadata.yaml 里的版本号
#   ./deploy/release-image.sh --no-push    # 只构建，不推送
#
# 需要先 docker login。
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE="${IMAGE:-ia3000/kouhubot}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
PUSH=1
[ "${1:-}" = "--no-push" ] && PUSH=0

# 版本号以插件 metadata.yaml 为准，避免镜像 tag 和插件版本对不上
VERSION="$(grep -m1 '^version:' plugins/astrbot_plugin_kouhu_oj/metadata.yaml | sed 's/^version:[[:space:]]*//; s/^v//')"
[ -n "$VERSION" ] || { echo "读不到插件版本号" >&2; exit 1; }

echo "镜像    : ${IMAGE}"
echo "版本    : ${VERSION}"
echo "架构    : ${PLATFORMS}"
echo

BUILDER=kouhu-multi
if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
    echo "创建 buildx builder: ${BUILDER}"
    docker buildx create --name "$BUILDER" --driver docker-container >/dev/null
fi

ARGS=(--builder "$BUILDER" --platform "$PLATFORMS"
      -t "${IMAGE}:${VERSION}" -t "${IMAGE}:latest" .)

if [ "$PUSH" = "1" ]; then
    docker buildx build "${ARGS[@]}" --push
    echo
    echo "✅ 已推送 ${IMAGE}:${VERSION} 和 ${IMAGE}:latest"
else
    docker buildx build "${ARGS[@]}" --load 2>/dev/null || docker buildx build "${ARGS[@]}"
    echo
    echo "✅ 已构建（未推送）"
fi
