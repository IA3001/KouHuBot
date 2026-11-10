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
      org.opencontainers.image.description="QQ 群口胡 OJ：new 派题、submit 判题、rank 积分榜、ask 自由问答" \
      org.opencontainers.image.source="https://github.com/IA3001/KouHuBot" \
      org.opencontainers.image.documentation="https://github.com/IA3001/KouHuBot#readme" \
      org.opencontainers.image.base.name="soulter/astrbot:${ASTRBOT_VERSION}"

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# matplotlib 第一次导入会在 MPLCONFIGDIR 里建字体缓存（约 1~2 秒）。
# 不固定这个路径的话，它默认往 $HOME/.config 写，而容器里 $HOME 可能不可写，
# 结果就是每次导入都重新扫一遍字体并刷一堆警告。
# 放 /var/cache 而不是 /AstrBot/data，是因为 data 通常被挂成数据卷，
# 镜像里烤好的缓存会被卷盖掉，等于白烤。
ENV MPLCONFIGDIR=/var/cache/matplotlib

# 插件放在 /opt/kouhu，不放 data/ ——data 目录通常挂成数据卷，放镜像里的
# data/ 会被卷盖住。启动时由 entrypoint 复制过去。
COPY plugins/astrbot_plugin_kouhu_oj /opt/kouhu/astrbot_plugin_kouhu_oj
# 更新日志放插件目录里：ask 要读它，而容器里根目录那份不进镜像
COPY CHANGELOG.md /opt/kouhu/astrbot_plugin_kouhu_oj/CHANGELOG.md
COPY docker/entrypoint.sh /usr/local/bin/kouhu-entrypoint

# 基底镜像已经带了 Pillow 和 Noto Sans CJK 字体（/usr/share/fonts/opentype/noto），
# 这里只补缺的两个：curl_cffi 过 Cloudflare、matplotlib 画公式。
# 装完顺手把 matplotlib 的字体缓存烤进镜像，容器启动后第一次出图就不用等。
RUN pip install --no-cache-dir "curl_cffi>=0.7" "matplotlib>=3.7,<4" \
    && mkdir -p "$MPLCONFIGDIR" \
    && python -c "\
import matplotlib; matplotlib.use('Agg');\
from matplotlib import mathtext; import io;\
mathtext.math_to_image(r'\$x^2+\sqrt{y}\$', io.BytesIO(), dpi=100, format='png');\
print('matplotlib 字体缓存已就绪')" \
    && chmod +x /usr/local/bin/kouhu-entrypoint \
    && rm -rf /opt/kouhu/astrbot_plugin_kouhu_oj/__pycache__

WORKDIR /AstrBot
EXPOSE 6185

# 继承基底镜像本来就有 python main.py，这里只是包一层同步逻辑
ENTRYPOINT ["/usr/local/bin/kouhu-entrypoint"]
CMD ["python", "main.py"]
