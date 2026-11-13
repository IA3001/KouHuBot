# 口胡 OJ（astrbot_plugin_kouhu_oj）

在群里练算法：从 Codeforces 抽题，提交思路、伪代码或代码，由模型判题；也可以独立练 C++ 坑点题。CF 正题与坑点题各有自己的当前题，互不替换。题目答对或放弃后才能继续抽取该线路的新题。

## 指令

可带 `/` 或全角 `／` 前缀，英文大小写不敏感。支持 `new图论`、`sub这题用二分` 等中文参数紧贴指令；`new！`、`help？` 等尾部标点也能识别。`newbie`、`subscribe` 等普通单词不会触发命令。

| 指令 | 作用 |
|---|---|
| `new` | 按本群当前难度档抽一道 CF 正题 |
| `new 1` / `new 2` / `new 3` / `new 4` | 按 Div 预设抽题 |
| `new [1200-1600]` | 指定 rating 区间，也支持 `1200到1600` |
| `new CF123B` | 指定 CF 题号 |
| `new dp` / `new dp+trees` | 按标签抽题，支持中文，多标签同时满足 |
| `new tags` | 查看标签与题数 |
| `cur` / `problem` | 重看当前 CF 正题 |
| `sub 思路` / `submit 思路` | 提交文字，支持多行代码，保留正文末尾标点与运算符 |
| `sub retry` | 判题失败后重试当前题暂存的提交 |
| 引用一条消息再发 `sub` | 思路取自被引用的那条消息；`sub` 后面也写了字就两段一起判（引用在前）。引用别人的不认，引用机器人的题面/提示也不认 |
| 同一轮多次提交 | 本轮之前交过的作答会作为补充资料一起给判题参考，最多带两条 |
| 引用图片或附图发送 `sub` | 转写并预览作答后判题，最多 3 张；可同时附文字 |
| `hint 1` / `hint 2` / `hint 3` | 逐级查看方向、关键观察、解法框架 |
| `giveup` | 放弃当前 CF 正题并查看解析 |
| `grm new [分类] [难度]` | 抽坑点题；分类、难度各最多一个，位置可互换，难度为 1/2/3 |
| `grm list` | 查看坑点分类与题数 |
| `grm cur` / `grm problem` | 重看当前坑点题 |
| `grm sub 分析` / `grm submit 分析` | 提交坑点分析，支持多行文字与引用自己的消息；暂不支持图片 |
| `grm giveup` | 放弃坑点题并查看答案 |
| `grm growth` / `grm status` | 查看题库更新进度、后台更新间隔和最近结果 |
| `grm update` | 管理员立即联网补题，无时间冷却；同时最多运行一个采集任务 |
| `history [页码]` / `history grm` | 查看自己的 CF / 坑点历史 |
| `history problem 628B` | 回看历史题面 |
| `history show 628B [记录页]` | 按题号分页查看历史作答记录 |
| `history show 提交编号 [内容页]` | 按提交编号分页查看完整作答与判词 |
| `history grm show 提交编号 [内容页]` | 坑点历史使用同样的分页方式，在 `history` 后加 `grm` |
| `mistakes [due] [grm] [页码]` | 错题本：答错或放弃、还没 AC 的题，最久没管的排最前 |
| `mistakes due` | 只看超过复习间隔（默认 3 天）的错题 |
| `mistakes grm` | 坑点线路的错题本，与 CF 正题分开 |
| `sol` / `sol 628B` | 查看已结束题目的解析 |
| `rank [页码]` / `me` | 本群积分榜分页 / 我的战绩 |
| `tier` | 查看群难度档 |
| `ask 问题` | 自由问答，支持多行文字 |
| `help` / `rules` / `about` | 指令菜单 / 计分规则 / 功能介绍 |
| `ver` / `ver list` / `ver 0.6.0` | 当前版本 / 版本列表 / 指定版本更新日志 |
| `ping` | 检查判题模型 |

管理员可用 `skip` 关闭 CF 当前题、`tier 数字` 调整群难度、`status` 查看运行信息，以及 `grm update` 立即更新坑点题库。手动更新无时间冷却，失败后直接再次发送 `grm update` 即可；全局同时最多运行一个采集任务，已有任务时可用 `grm status` 查看进度。旧指令 `grm grow`、`grm grow retry` 仍兼容，行为与 `grm update` 相同。错误的档位或坑点参数会给出说明，不会静默忽略重复或多余参数。单字母 `q`、`s`、`p`、`g`、`r`、`h` 不是顶层命令。

首个 AC 后全群该题结束，答错不扣分；坑点题不计 CF 积分。提示在群内可见，查看历史不会切换当前判题线路。模型判题和解析可能出错。

## 题目来源与判题

CF 正题来自 Codeforces，题面抓取、翻译成功后缓存到本地。正题和样例使用 Chromium 与 KaTeX 渲染；排行榜、坑点等图片使用 Pillow。题面无法准备完整时会提示失败，不会用不完整内容替换当前题。

坑点题使用随插件提供的 `pitfalls.json`，包含代码、参考答案和修复方式。可用分类以 `grm list` 实际显示为准，例如 `grm new 浮点数 1`。后台默认每 6 小时尝试补充一道候选，经过资料采集、模型生成、模型复核和隔离代码验证后才进入可抽题库。失败保留原题库，可用 `grm growth` 查看结果。部署环境需支持隔离验证；无法验证的候选不会进入可抽题库。

判题要求模型输出结构化结果，并处理 JSON 围栏、格式异常与超时。提交内容作为不可信数据传给模型。图片交题先转写，再走文字判题；转写失败或超限会说明原因。

## 配置

完整配置与默认值见 [_conf_schema.json](_conf_schema.json)，常用项如下：

| 配置 | 默认 | 说明 |
|---|---|---|
| `judge_provider_id` | 空 | 判题与翻译模型，留空使用会话默认模型 |
| `max_submission_chars` | 2000 | 纯文字提交字数上限 |
| `new_cooldown_seconds` | 5 | 同群派题冷却秒数 |
| `tags_cooldown_seconds` | 10 | 标签列表冷却秒数 |
| `leaderboard_size` | 10 | 排行榜显示人数 |
| `mistake_due_days` | 3 | 错题本多久算「该复习」，0 表示随到随复习 |
| `image_width` | 900 | 图片宽度 |
| `fetch_timeout_seconds` | 40 | CF 抓取超时秒数 |
| `prepare_timeout_seconds` | 60 | 题面准备总时限 |
| `translate_timeout_seconds` | 90 | 单次翻译超时，仍受准备总时限限制 |
| `judge_timeout_seconds` | 90 | 判题超时秒数 |
| `judge_ack_delay_seconds` | 5 | 判题等待多久后发进度回复 |
| `image_submission_enabled` | true | 开启正题图片交题 |
| `vision_provider_id` | 空 | 转写模型，留空依次使用判题或会话模型；需支持图片 |
| `max_submission_images` | 3 | 每次最多图片数，上限 3 |
| `image_submission_max_chars` | 6000 | 图片转写与附带文字总字数上限 |
| `image_submission_timeout_seconds` | 60 | 图片加载与转写总时限 |
| `grm_auto_growth` | true | 开启后台坑点题库补充 |
| `grm_growth_interval_seconds` | 21600 | 后台自动补题间隔，最小 300 秒；管理员手动更新不受此限制 |
| `grm_growth_timeout_seconds` | 180 | 生成与模型复核各自的时限，范围 30–240 秒 |
| `ask_cooldown_seconds` | 8 | 同群问答冷却秒数 |
| `ask_max_chars` | 500 | 问答字数上限 |
| `ask_timeout_seconds` | 60 | 问答超时秒数 |
| `ask_ack_message` | 让我看看…… | 问答即时回复，留空关闭 |

## 数据与安装

插件数据位于 AstrBot 数据目录下的 `plugin_data/astrbot_plugin_kouhu_oj/`，包括 SQLite 计分、题面缓存、图片和 `pitfall_growth/` 下的题库生长记录。备份时应保留整个目录。

依赖见 [requirements.txt](requirements.txt)。手动安装时在 AstrBot 的 Python 环境安装依赖，并执行 `python -m playwright install --with-deps chromium` 准备题面渲染浏览器；还需安装可用的中文字体。隔离代码验证的部署要求以项目根目录文档为准。
