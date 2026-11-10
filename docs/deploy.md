# 2C2G 部署手册

从一台空的 2C2G 云服务器，到群里能用的口胡 OJ。

---

## 0. 前置条件

| 项目 | 要求 |
|---|---|
| 系统 | Debian 12 / Ubuntu 22.04+（推荐精简镜像，不要带面板） |
| 内存 | 2 GB（**必须加 swap**，见步骤 1） |
| 磁盘 | ≥ 20 GB |
| Docker | 20.10+，含 compose v2 |
| 网络 | 能访问 `api.sgroup.qq.com`（QQ 官方 API）与大模型 API |
| 账号 | QQ 开放平台开发者账号，且**你本人是目标群的群主** |

---

## 官方机器人的硬限制（先读，避免白折腾）

这些是平台约束，不是配置问题：

1. **机器人是独立账号**，不是把你的 QQ 号变成机器人。
2. **只能添加到自己当群主的群聊**。别人的群你加不进去——这是最硬的限制。
3. **创建机器人需要提交审核**，等安全校验通过。
4. **必须配置 IP 白名单**，填服务器公网 IP，否则连接被拒。
5. **主动发言有额度**，不适合高频定时推送。
6. 群内默认需要 @机器人 才触发（可在群设置里调整消息范围）。

> 如果第 2 条对你不可接受，本项目方案不适用，需要回到 NapCat 逆向路线（但会失去「零封号风险」和「2C2G 轻量」两个优势）。

---

## 1. 服务器准备

```bash
# 建 2G swap —— 2C2G 必做，否则内存峰值时容器会被 OOM Kill
sudo bash deploy/setup-swap.sh

# 确认生效
free -h
swapon --show
```

预期看到 2G swap 已挂载。

### 内存预算参考

| 组件 | 占用 |
|---|---|
| 系统（精简） | 150–250 MB |
| Docker 引擎 | 50–100 MB |
| AstrBot（含插件） | 400–600 MB |
| **合计** | **约 0.6–1.0 GB** |

2GB 内存有约 1GB 余量，加上 swap 兜底，非常安全。

---

## 2. 安装 Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
sudo usermod -aG docker $USER    # 重新登录后生效
docker compose version
```

> 中国大陆服务器拉取镜像慢，可配置镜像加速，或在 `deploy/.env` 里给容器配 HTTP 代理。

---

## 3. 启动 AstrBot

```bash
cd deploy
cp .env.example .env
# 按需编辑 .env（默认值已适配 2C2G，通常不用改）
docker compose up -d
docker compose logs -f astrbot
```

首次启动日志里会打印：

- WebUI 地址
- **随机初始密码**（用户名通常为 `astrbot`）

**立刻记下来，登录后马上改密码。**

### 访问 WebUI

默认只绑定 `127.0.0.1:6185`，从公网直接访问不到。用 SSH 隧道：

```bash
# 在你自己的电脑上执行
ssh -L 6185:127.0.0.1:6185 user@your-server
```

然后浏览器打开 `http://127.0.0.1:6185`。

> ⚠️ **不要把 6185 直接暴露到公网**。WebUI 有完整的管理权限，暴露等于把机器人控制权交出去。如果确实需要公网访问，务必配反向代理 + HTTPS + 强密码 + 来源 IP 限制。

---

## 4. 创建 QQ 官方机器人

### 4.1 拿 AppID / AppSecret

两种方式：

**方式 A（推荐，最省事）**：AstrBot WebUI 支持扫码一键创建

```
机器人 → 创建机器人 → QQ 官方机器人（WebSocket）→ 扫码
```

**方式 B（手工）**：到 QQ 开放平台手动申请

1. 创建机器人，填名称、简介、头像
2. 提交审核，等安全校验通过
3. 在机器人管理页拿到 **AppID** 和 **AppSecret**
4. **配置 IP 白名单**：填服务器公网 IP

> 查公网 IP：`curl -s ifconfig.me`

### 4.2 在 AstrBot 里填配置

```
机器人 → 创建机器人 → QQ 官方机器人（WebSocket）
  appid:  <你的 AppID>
  secret: <你的 AppSecret>
  启用消息列表单聊 (enable_group_c2c): 按需
```

保存后 AstrBot 会主动连腾讯的 WebSocket 网关，日志出现连接成功即可。

### 4.3 把机器人拉进群

手机 QQ → 联系人 → **机器人** 页签 → 找到你的机器人 → 下方「**添加到群聊**」

> 只能选**你自己当群主**的群。

### 4.4 群内权限设置（关键，漏了会「没反应」）

进入群聊设置 → 机器人设置，打开：

- ✅ **机器人可获取的群聊消息范围 → 获取群内全部消息**
  不开的话，只有 @ 机器人时才收得到，`q` 这类裸指令会失效。
- ✅ **允许机器人主动在群聊内发言**
  不开的话只能被动回复，做不了主动推送。

### 4.5 沙箱验证（可选但推荐）

正式上线前可以先配沙箱：允许测试群/私聊（群人数 ≤ 20），在里面跑通全流程再放开。

---

## 5. 配置大模型

WebUI →「模型提供商」→ 新增：

| 字段 | 说明 |
|---|---|
| 类型 | OpenAI 兼容 / DeepSeek / 硅基流动 / 通义 等 |
| API Key | 各自平台申请 |
| Base URL | 按提供商文档填 |
| 模型名 | 建议用便宜且稳定的中等模型 |

**选型建议**：判题需要「听话地输出 JSON」，不需要顶级推理能力。用便宜模型 + 写细 `rubric` 的性价比远高于用贵模型。可以在插件配置里用 `judge_provider_id` 单独指定判题模型，和陪聊模型分开。

---

## 6. 安装插件

```bash
# 在仓库根目录
cp -r plugins/astrbot_plugin_kouhu_oj deploy/../data/plugins/
# 或者直接复制到宿主机上 .env 里 DATA_DIR 指向的目录的 plugins/ 下

docker compose restart astrbot
docker compose logs -f astrbot | grep KouHuOJ
```

看到类似日志即成功：

```
[KouHuOJ] 就绪：题目源 Codeforces + 内置坑点 90 条，题面缓存 0 道，数据目录 /AstrBot/data/plugin_data/astrbot_plugin_kouhu_oj
```

然后在 WebUI 插件页确认「口胡 OJ」已启用。

---

## 7. 验收清单

按顺序在群里发：

| 步骤 | 发送 | 预期 |
|---|---|---|
| 1 | `h` | 返回指令列表 |
| 2 | `q` | 先秒回「正在抽题」，几秒后**收到一张题目图片**（标题 + 难度 + 标签 + 题面 + 原题链接） |
| 3 | `s 随便写点什么` | 收到判题结果（大概率 WA，带锐评） |
| 4 | `s <认真作答>` | 可能 AC，得分 85+ |
| 5 | `r` | 看到自己的得分和 AC 题数 |
| 6 | `g` | 收到原题链接 |

全过即部署完成。

> 第 2 步是最容易出问题的一步：如果收到的不是图片而是报错，说明服务器**没装中文字体**。
> Debian/Ubuntu 装一下即可：`apt install -y fonts-noto-cjk`

---

## 8. 运维

### 常用命令

```bash
docker compose logs -f astrbot          # 看日志
docker compose restart astrbot          # 重启
bash ~/KouHuBot/update.sh                # 更新Git插件，不拉取镜像
# 只有运行依赖变化、明确升级基础环境时才拉取新的runtime-N标签。
docker stats                            # 实时资源占用
```

### 数据备份

所有状态都在 `data/` 目录（容器内 `/AstrBot/data`）：

```bash
tar czf kouhobot-$(date +%F).tar.gz data/
```

**备份时先停容器**，避免 SQLite 写入中途拷贝导致文件不一致：

```bash
docker compose stop astrbot
tar czf kouhobot-$(date +%F).tar.gz data/
docker compose start astrbot
```

### 换题目源

题目预置在本地 `cf_cache.db` 里：派题时优先从缓存取，缓存不够才去 Codeforces 抓。
抓到的题面永久保留，所以 CF 那边反爬再升级也不会立刻断供。
想换别的站点，在 `plugins/astrbot_plugin_kouhu_oj/sources/` 下新增一个实现类，
然后改 `main.py` 里 `initialize()` 中实例化题目源的那一行。

注意很多 OJ 站点有反爬（Codeforces 的题面页返回 403、洛谷会 302 死循环），
优先找官方 API 而不是爬 HTML。

---

## 9. 排障

| 现象 | 排查方向 |
|---|---|
| 群里发指令没反应 | ① 群设置里「获取群内全部消息」是否打开 ② 机器人是否真在群里 ③ `docker compose logs` 有无收到事件 |
| 日志报连接失败 / 401 | AppID / AppSecret 错误，或 **IP 白名单没配** |
| 判题一直失败 | 模型 API 不通 / Key 失效 / 模型不支持该调用方式；看日志里的 `判题调用异常` |
| 判题总说「格式异常」 | 模型太弱或不听话，换个模型，或在 `judge_provider_id` 里指定更强的模型 |
| 容器反复重启 | `docker compose logs` 看 OOM；确认 swap 已挂载、`mem_limit` 没设太小 |
| WebUI 打不开 | 确认 SSH 隧道建立、端口没被占用；`ss -lntp \| grep 6185` |
| 内存吃紧 | `docker stats` 看实际占用；关掉文转图等重功能；必要时升 2C4G |

### 查看实时资源

```bash
docker stats --no-stream
free -h
```

---

## 10. 安全加固清单

- [ ] WebUI 只绑 `127.0.0.1`，走 SSH 隧道
- [ ] 改掉 WebUI 默认随机密码
- [ ] 云厂商安全组只放行 SSH（22），其余全关
- [ ] QQ 开放平台 IP 白名单填服务器固定公网 IP
- [ ] `.env` 不入库（已在 `.gitignore`）
- [ ] 定期备份 `data/`
- [ ] 日志轮转已开（compose 里配了 `max-size: 10m`, `max-file: 3`）
