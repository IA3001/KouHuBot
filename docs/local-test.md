# 本地运行（不用 Docker）

不想构建镜像、或者只想先验证插件能不能跑，可以对着 AstrBot **源码**直接启动。

> 以下流程已在 **AstrBot v4.29.4 + Python 3.10.12** 上实测通过，插件正常加载并完成初始化。

> **懒人版**：仓库根目录的 `./run.sh` 已经把下面所有步骤自动化了，直接运行即可。
> 下面的手工步骤适合想搞清楚每一步在做什么、或需要自定义的情况。

---

## 1. 准备 AstrBot

```bash
git clone --depth 1 https://github.com/AstrBotDevs/AstrBot
cd AstrBot
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

> 依赖较多（含 faiss-cpu、pandas 等），首次安装需要几分钟。

## 2. 放入插件

```bash
# 假设 KouHuBot 仓库与 AstrBot 同级
mkdir -p data/plugins
cp -r ../KouHuBot/plugins/astrbot_plugin_kouhu_oj data/plugins/
```

## 3. 启动

```bash
python main.py
```

`data/` 目录会在首次启动时自动创建（配置、数据库、日志、插件数据都在里面）。

## 4. 确认插件已加载

启动日志里应该能看到这几行：

```
[Core] [INFO] AstrBot v4.29.4
[astrbot_plugin_kouhu_oj] [INFO] [star.star_manager:1136]: Loading plugin astrbot_plugin_kouhu_oj ...
[astrbot_plugin_kouhu_oj] [INFO] [star.star_manager:1226]: Plugin astrbot_plugin_kouhu_oj (v0.2.0) by KouHuBot: 群内 OJ ...
[astrbot_plugin_kouhu_oj] [INFO] [main:...]: [KouHuOJ] 就绪：题目源 Codeforces + 内置坑点 40 条，题面缓存 0 道，数据目录 .../data/plugin_data/astrbot_plugin_kouhu_oj
[Core] [INFO] [core.core_lifecycle:364]: AstrBot started.
[Core] [INFO] [dashboard.server:662]: Starting WebUI at http://0.0.0.0:6185
```

看到「就绪：题目源 Codeforces」就说明插件已经跑通了——它建好了两个 SQLite，题目总表也拉到了。

## 5. 打开 WebUI

浏览器访问 <http://localhost:6185>，用户名 `astrbot`。

忘了密码可以重置：

```bash
python main.py --reset-password
```

> ⚠️ 源码直跑的默认绑定是 `0.0.0.0:6185`，也就是**局域网可访问**。
> 生产环境请改成只监听 `127.0.0.1`（`deploy/docker-compose.yml` 里已经这么配了）。

## 6. 接下来

没有 QQ 凭证的话，走到这一步就结束了——插件已证明能在真实 AstrBot 里加载和初始化，但收发消息还需要接入 QQ 官方机器人（见 [deploy.md](deploy.md)）。

想在没有凭证的情况下继续验证**业务逻辑**，用模拟群聊：

```bash
python3 tests/local_harness.py
```

---

## 实测记录

| 检查项 | 结果 |
|---|---|
| Python 3.10.12 环境导入 | ✅ |
| AstrBot v4.29.4 加载插件 | ✅ `Plugin astrbot_plugin_kouhu_oj (v0.1.0)` |
| 读取题库 | ✅ 8 条 |
| 创建 SQLite | ✅ `data/plugin_data/astrbot_plugin_kouhu_oj/kouhu_oj.db` |
| 注册指令 handler | ✅ 6 个全部进入 AstrBot 注册表 |
| WebUI 响应 | ✅ HTTP 200 |
| 启动日志错误 | ✅ 无 |

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `No module named astrbot` | 没在 AstrBot 源码目录下执行，或没激活 venv |
| 插件不加载 | 确认插件放在 `data/plugins/astrbot_plugin_kouhu_oj/`，目录名与 `metadata.yaml` 的 `name` 一致 |
| `报错 metadata.yaml` | 该文件是 AstrBot 识别插件的必需文件，不能删 |
| 端口 6185 被占用 | 改 `data/cmd_config.json` 里的 `dashboard.port` |
| pip 安装很慢 | 换国内源：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
