# 发版与热更新检查单

这份文档是**流程**，不是介绍。0.22.0 发版时踩过的坑写在每条后面，照着做就不会再踩。

## 1. 版本号只改一处

**只改 `plugins/astrbot_plugin_kouhu_oj/metadata.yaml` 里的 `version:`。**

`main.py` 的 `PLUGIN_VERSION` 在启动时从这个文件读回（`_read_plugin_version`），
读不到才用兜底常量。所以：

- 不要再去 main.py 里找版本号改——那里没有第二份了。
- 不要在任何测试里写死版本号，用 `metadata_version()`（smoke_test）或
  `tests/test_release_consistency.py` 里的断言。

> 踩过的坑：0.22.0 只在 metadata.yaml 改成 v0.22.0，main.py 的常量还是 0.21.29。
> AstrBot 日志打印 v0.22.0，群里 `ver` 却一直说 0.21.29；而唯一那条版本断言
> 又把 0.21.29 写死在测试里，于是「测试全绿」纯属两边一起过期。

## 2. 更新日志写三条口径，必须一致

| 口径 | 文件 | 谁在看 |
|---|---|---|
| 仓库日志 | `CHANGELOG.md` | 本地、GitHub、`ask 更新了什么` 的本地运行 |
| 内嵌副本 | `plugins/astrbot_plugin_kouhu_oj/_changelog_text.py` | **线上 `ver` 实际读的就是它** |
| 群内渲染 | `ver` / `ver list` / `ver 0.22.0` | 群友 |

操作顺序：

```bash
# 1) 在 CHANGELOG.md 顶部加一节，标题可以是 ## 0.22.0 或 ## 0.22.0 · 小标题
# 2) 重新生成内嵌副本（别手改那个 .py）
python3 tools/sync_changelog.py
```

注意：

- **容器里读不到 `CHANGELOG.md`**：只挂了插件目录，`_changelog_source()` 会退回
  「内嵌副本」。所以忘了跑 `sync_changelog.py` 就等于线上 `ver` 看不到新版。
- 标题里版本号后面不要再跟数字/点（`## 0.22.01` 不会被当成 `0.22.0`），但可以带
  `· 小标题`——`ver` 的正则已经容忍这种写法。
- 校验：`tests/test_release_consistency.py` 会同时检查 metadata 版本、
  `PLUGIN_VERSION`、`CHANGELOG.md`、内嵌副本、`ver` 与 `ver list` 的真实输出。

## 3. 跑测试（顺序固定）

```bash
cd tests
PYTHONDONTWRITEBYTECODE=1 ../.local/venv/bin/python3 -m unittest discover -p "test_*.py" -q
PYTHONDONTWRITEBYTECODE=1 ../.local/venv/bin/python3 smoke_test.py      # 纯函数/存储/渲染
cd .. && PYTHONDONTWRITEBYTECODE=1 .local/venv/bin/python3 tests/local_harness.py  # 模拟群聊
```

全绿再往下走。`tests/integration/run.py` 需要真实 QQ 与真实模型，只有部署到
服务器后才可能跑通，不能代替上面三条。

## 4. 提交

```bash
git add <本功能相关文件>        # 别把别人的 WIP 一起 add
git commit -F -                 # 标题 follow：feat(0.22.0): 一句话
```

提交信息里写清楚**群里能看到什么变化**，以及改动了哪条口径（版本号/日志/配置项）。

## 5. 热更新到服务器（不重启容器）

```bash
# 只同步插件目录 + 变更的文档；CHANGELOG.md 在仓库根，也一起同步
scp plugins/astrbot_plugin_kouhu_oj/{main.py,metadata.yaml,_changelog_text.py,_conf_schema.json} \
    root@<server>:/home/admin/KouHuBot/plugins/astrbot_plugin_kouhu_oj/
ssh root@<server> 'chown admin:admin /home/admin/KouHuBot/plugins/astrbot_plugin_kouhu_oj/*'
```

然后在容器里调 AstrBot 自己的重载接口（用它的 `jwt_secret` 签一个短期 token）：

```python
# docker exec astrbot python3 /tmp/reload.py
import json, time, urllib.request, jwt
cfg = json.load(open("/AstrBot/data/cmd_config.json", encoding="utf-8-sig"))
dash = cfg["dashboard"]; now = int(time.time())
tok = jwt.encode({"username": dash["username"], "iat": now, "exp": now + 600},
                 dash["jwt_secret"], algorithm="HS256")
req = urllib.request.Request("http://127.0.0.1:6185/api/plugin/reload",
      data=json.dumps({"name": "astrbot_plugin_kouhu_oj"}).encode(), method="POST",
      headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
print(urllib.request.urlopen(req, timeout=180).read().decode())
```

期望返回 `{"status":"ok","message":"重载成功。"}`。**不要 `docker restart`**：
重载只重新 import，SQLite（积分、轮次、作答）和进行中的题都在。

### 验证（必做，别只看重载成功）

```bash
docker logs --since 3m astrbot | grep -E "Loading plugin|\(v0\.|就绪"   # 版本号对不对
docker logs --since 3m astrbot | grep -icE "traceback|exception"        # 应该是 0
```

再核对两条用户可见口径：

- 配置项是否落地：`/AstrBot/data/config/astrbot_plugin_kouhu_oj_config.json` 里能看到新键；
- 群内 `ver` 是否会报新版本（可以让群友发一次，或用 main.py 自己的函数跑一遍：
  `PLUGIN_VERSION` + `_changelog_section(_load_changelog(), PLUGIN_VERSION)`）。

## 6. 回滚

热更新前先备份插件目录：

```bash
tar czf /root/kouhu-backups/plugin-<旧版本>-$(date +%Y%m%d-%H%M%S).tgz \
    -C /home/admin/KouHuBot/plugins astrbot_plugin_kouhu_oj
```

回滚 = 解开备份 + 再调一次重载接口。

## 7. 已知环境限制

- 容器内存上限 900MiB、宿主机**没有 swap**（`free` 显示 Swap: 0）。同一条渲染线程串行执行，不会叠。
  实测（0.23.1，`/sys/fs/cgroup/memory.stat`）：
  - 常驻进程 RSS 约 405MiB（AstrBot 主进程，含 `anon_thp` 318MiB 大页）；
  - 纯静态记录卡片：内存基本不动；
  - **带公式的解析卡**（加载 KaTeX）：单次 +70~135MiB，耗时约 4.4s；
  - 题面渲染是大头，单次峰值可到 ~820MiB。
  `memory.events` 里 `max` 计数会涨（触到上限会先回收 page cache 再重试），但 `oom` / `oom_kill` 一直是 0，
  渲染完 `current` 会回落到 ~530MiB。建议仍按 `deploy/setup-swap.sh` 补 2G swap，给题面渲染留余量。
- 卡片的公式是**按需**加载的：正文出现 `data-tex` 才注入 KaTeX 并等 `__kouhuRendered`，否则仍是纯静态文档。
- 容器内没有 emoji 字体：卡片里不要画 emoji（会是豆腐块），状态用色块和文字。
- 卡片是静态 HTML（无 `<script>`），所以 CSP 不需要放开脚本；截图等待条件为 `None`。
