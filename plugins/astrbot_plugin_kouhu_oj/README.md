# 口胡 OJ（astrbot_plugin_kouhu_oj）

KouHuBot 的 AstrBot 插件：群内抽题 → 交卷 → AI 判题锐评 → 计分。

## 安装

把本目录整体复制到 AstrBot 的 `data/plugins/` 下，然后重载插件：

```bash
cp -r astrbot_plugin_kouhu_oj /path/to/AstrBot/data/plugins/
```

依赖：无第三方包，只用 Python 标准库。要求 AstrBot ≥ 4.9.2。

## 指令

加不加 `/` 都可以。

| 指令 | 作用 |
|---|---|
| `口胡` | 随机抽题 |
| `提交 <解答>` | 交卷判题 |
| `当前题` | 重看题目 |
| `放弃` / `giveup` | 公布答案 |
| `排行榜` / `rank` | 计分榜 |
| `oj帮助` | 指令说明 |

## 配置项

在 AstrBot WebUI 的插件配置页可视化修改（对应 `_conf_schema.json`）：

| 键 | 默认 | 说明 |
|---|---|---|
| `judge_provider_id` | 空 | 判题专用模型 ID；留空用当前会话模型 |
| `judge_timeout_seconds` | 60 | 判题超时 |
| `max_submission_chars` | 2000 | 提交长度上限 |
| `issue_cooldown_seconds` | 15 | 同人换题冷却 |
| `leaderboard_size` | 10 | 榜单长度 |
| `reveal_answer_on_giveup` | true | 放弃时是否公布答案 |

## 题库

默认读插件目录下的 `problems.json`（JSON 数组）。字段：

```json
{
  "pid": "唯一ID",
  "title": "标题",
  "statement": "题面（发群里）",
  "answer": "标准答案（判题用，放弃时展示）",
  "rubric": "判题要点（决定判题质量）",
  "tags": "标签,逗号分隔",
  "difficulty": 1
}
```

`pid` 是主键：更新题库时同 `pid` 会覆盖题面，**不会丢失群友进度**。

`rubric` 建议写细，尤其对「常见错误答案」直接写明判 WA，比让模型自由发挥稳得多。

## 数据

状态存在 `data/plugin_data/astrbot_plugin_kouhu_oj/kouhu_oj.db`（SQLite）。删掉即重置所有进度和计分。

## 测试

```bash
python3 tests/smoke_test.py    # 在仓库根目录执行
```
