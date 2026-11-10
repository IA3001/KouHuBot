# syntax=docker/dockerfile:1
#
# KouHuBot —— QQ 群口胡 OJ
# 仅含AstrBot与运行依赖，插件由Git目录挂载；运行环境版本与插件版本独立。
#
#   docker build -t ia3000/kouhubot:runtime-1 .
#   cd deploy && docker compose up -d
#
ARG ASTRBOT_VERSION=v4.29.4
FROM soulter/astrbot:${ASTRBOT_VERSION}

# FROM 之后 ARG 要重新声明一次，否则下面的 LABEL 拿不到
ARG ASTRBOT_VERSION

LABEL org.opencontainers.image.title="KouHuBot" \
      org.opencontainers.image.description="KouHuBot运行环境：AstrBot、Chromium、C++工具链；插件通过Git目录挂载" \
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

# 不COPY插件或业务更新日志；改插件不会造成环境镜像重新打包。
COPY docker/runtime-entrypoint.sh /usr/local/bin/kouhu-entrypoint

# 基底镜像已经带了 Pillow 和 Noto Sans CJK 字体（/usr/share/fonts/opentype/noto），
# 这里只补缺的两个：curl_cffi 过 Cloudflare、matplotlib 画公式。
# 装完顺手把 matplotlib 的字体缓存烤进镜像，容器启动后第一次出图就不用等。
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/kouhu/browsers
RUN pip install --no-cache-dir "curl_cffi>=0.7" "matplotlib>=3.7,<4" "playwright==1.65.0" \
    && python -m playwright install --with-deps chromium \
    && apt-get update && apt-get install -y --no-install-recommends g++ bubblewrap libseccomp2 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p "$MPLCONFIGDIR" \
    && python -c "\
import matplotlib; matplotlib.use('Agg');\
from matplotlib import mathtext; import io;\
mathtext.math_to_image(r'\$x^2+\sqrt{y}\$', io.BytesIO(), dpi=100, format='png');\
print('matplotlib 字体缓存已就绪')" \
    && chmod +x /usr/local/bin/kouhu-entrypoint

WORKDIR /AstrBot
EXPOSE 6185

# 继承基底命令；入口仅检查挂载，不复制或覆盖代码。
ENTRYPOINT ["/usr/local/bin/kouhu-entrypoint"]
CMD ["python", "main.py"]
