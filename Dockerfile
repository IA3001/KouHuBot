# syntax=docker/dockerfile:1
#
# KouHuBot —— QQ 群口胡 OJ
# 基底直接用 AstrBot 官方镜像，插件在启动时同步进 data/plugins。
#
#   docker build -t ia3000/kouhubot:latest .
#   docker run -d --name kouhubot -p 6185:6185 -v ./data:/AstrBot/data ia3000/kouhubot:latest
#
ARG ASTRBOT_VERSION=v4.29.4
FROM soulter/astrbot:${ASTRBOT_VERSION}

# FROM 之后 ARG 要重新声明一次，否则下面的 LABEL 拿不到
ARG ASTRBOT_VERSION

LABEL org.opencontainers.image.title="KouHuBot" \
      org.opencontainers.image.description="QQ 群口胡 OJ：q 抽题、s 判题、r 排行榜、ask 自由问答" \
      org.opencontainers.image.source="https://github.com/IA3001/KouHuBot" \
      org.opencontainers.image.documentation="https://github.com/IA3001/KouHuBot#readme" \
      org.opencontainers.image.base.name="soulter/astrbot:${ASTRBOT_VERSION}"

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 插件放在 /opt/kouhu，不放 data/ ——data 目录通常挂成数据卷，放镜像里的
# data/ 会被卷盖住。启动时由 entrypoint 复制过去。
COPY plugins/astrbot_plugin_kouhu_oj /opt/kouhu/astrbot_plugin_kouhu_oj
COPY docker/entrypoint.sh /usr/local/bin/kouhu-entrypoint

RUN chmod +x /usr/local/bin/kouhu-entrypoint \
    && rm -rf /opt/kouhu/astrbot_plugin_kouhu_oj/__pycache__

WORKDIR /AstrBot
EXPOSE 6185

# 继承基底镜像本来就有 python main.py，这里只是包一层同步逻辑
ENTRYPOINT ["/usr/local/bin/kouhu-entrypoint"]
CMD ["python", "main.py"]
