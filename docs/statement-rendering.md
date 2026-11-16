# 题面渲染（0.11.0）

正题与样例使用本地 Chromium 截图，KaTeX 0.16.22 脚本、样式、字体和许可随插件保存。模板暖白底、绿色分区标题，顶部显示时间限制（秒）与内存限制（MB）；缺失明确写未提供。隐藏题名、题号、评级和分值。排行榜、坑点题仍沿用原绘图组件。

HTML 提取保留段落、输入输出分区及 pre 格式块；翻译公式使用占位符隔离，逐段翻译并检查占位符唯一顺序、公式原文与长英文残留，最多两轮。失败返回原始英文但不写入中文缓存；公式不符或明显未译的旧缓存重新尝试。未连接真实模型服务的测试不能证明所有翻译语义准确。

浏览器模板对内容做 HTML 转义，只允许本地资源；KaTeX 禁用信任模式，坏公式局部恢复原始表达式。公式解析支持 CF 三美元、双美元、单美元及反斜杠括号，不匹配的分隔符保持原文。样例全部输出，不再截前三组。

渲染任务由单工作线程串行执行，每次关闭浏览器，不常驻额外服务。单边超过30000像素时报错，由现有消息处理回退文本；本版长题输出单张图片，尚未实现自动分页。900MiB限制容器已验证题面与样例截图，但不是整个 AstrBot 高并发时的峰值保证。

手工安装插件需安装 requirements 并执行 `python -m playwright install --with-deps chromium`；官方镜像已包含依赖。运行 `python tests/render_preview.py` 可生成包含按位或、分式、求和、下标及错误公式降级的实际图片。测试包括46项独立工具/存储/渲染检查、181项单元检查和272项模拟群聊检查。

# 记录卡片（0.22.0）

`history`（按题汇总 / 每次作答 / 提交详情）、`mistakes`、`me`、`sol` 改成图片，与题面卡共用同一套 Chromium 截图链路，但**不带公式**：

- 入口是 `browser_render.render_html(document, out_dir, width, math=False)`。它把「写盘 → 起浏览器 → 路由白名单 → 截图 → 关浏览器」抽成 `_render_document`，题面路径仍然走 `_render`（保持 `build_html` + KaTeX 就绪等待 `window.__kouhuRendered`）。
- **公式是按需的**：`record_cards.document()` 扫描正文，发现 `data-tex` 占位就注入 `browser_render.math_assets()` 给的本地 KaTeX（样式 + 脚本）并放开 `script-src file: 'unsafe-inline'`；`render()` 据此把 `math=True` 传下去，截图像题面一样等 `window.__kouhuRendered`。没有公式的卡片（history / mistakes / me）仍是纯静态文档，等待条件为 `None`，CSP 不含 `script-src`。KaTeX 资源缺失时退回纯文本卡片。
- 解析与判词的正文走 `math_prose()`：先用 `tokenize_math` 把 `$$…$$` / `\[…\]` / `\(…\)` 挖成占位串，再交给 `prose()` 排 Markdown（标题、列表、粗体），最后把占位串回填成 `[data-tex]`。行内片段（作答预览、错题本建议）走 `math_inline()`，不拆段落。作答正文与代码块不解析公式——那里的 `$` 是代码或金额。
- 卡片 HTML 由 `record_cards.py` 构建：`history_card` / `entries_card` / `entry_detail_card` / `mistakes_card` / `profile_card` / `solution_card`，都是纯函数，可离线单测。可见性口径（进行中隐藏题号、题名）**在调用方**先落成文案再交进来，渲染器不认识 `status`，避免隐藏逻辑写两份。
- 容器里没有任何 emoji 字体（`fc-list | grep -i emoji` 为空），emoji 会渲染成 .notdef 方框，所以卡片内状态一律用 CSS 色块与文字徽章；用户昵称、作答、判词里的 emoji 由 `clean_text` 换成占位点。用户内容一律 HTML 转义。
- 单张耗时约 0.6–0.7 秒、进程树峰值内存约 +460MB；加载 KaTeX 的解析卡约 4.4 秒、cgroup 峰值 +70~135MiB（0.23.1 实测）。渲染串行（`_RENDERER` 单工作线程 + `_RENDER_LOCK`），一次命令只出一到两张卡，渲染放在 `asyncio.to_thread` 里，不阻塞事件循环。
- 失败（没有浏览器、截图超时、`RenderError`、内容异常）只记一条 warning，然后**回退到原文字**；文字构建函数（`mistakes_text`、`history` 的拼装等）原样保留，所以关掉 `record_cards_enabled` 或渲染不可用时行为与 0.21.x 完全一致。
- 单张仍受 30000px 上限约束：提交详情按 `split_code`（5000 字/张）切块并标注「作答 i/n」，正常提交（默认 2000 字）永远是一张。
- 每页条数按图片容量定（`record_cards.CARD_LIST_PAGE=30` / `CARD_ENTRY_PAGE=20` / `CARD_MISTAKE_PAGE=20`），30 行列表约 4569px 高；文字回退沿用消息长度的 5 / 3 / 5，渲染失败时按文字口径重新取数，页码自洽。

# 个人主页（0.23.0）：`me` 变成 CF 风格页面

`me` 的数据全部来自 `review_store.solve_history(scope, user)` 一条查询（AC 记录 + 该题最近一轮题名），
再由 `profile.py` 的纯函数算成展示用的一组数字，`record_cards.profile_card` 只负责排版。口径集中在一个文件里：

- **rating**：已 AC 题目的平均档位，四舍五入到 50 分，夹在两个端点档位之间；AC 少于 3 道返回 `None`，
  卡片显示「未定级」并写明还差几道。段位配色沿用 CF 的分档（`RANKS`：新手 1200 以下、入门、熟练、专家、
  候选大师、大师、宗师 2400 以上），颜色只写语义名，色值在卡片 CSS 里。
- **今日 / 本周 / 本月**：按本地日历切窗口，周一为一周起点；同时给题数与得分。与群里显示时间用同一套
  `time.localtime`，不另立时区口径。
- **热力图**：26 列（周）× 7 行（周一到周日，共 182 天），一列一周、从上到下是周一到周日；按当天 AC 数
  分 4 档深浅，今天之后的格子标 `future` 画成虚线框，不假装有数据。右侧给活跃天数、最长连续、做题日均、
  本次连击。
- **积分曲线**：窗口内每日累计积分的内联 SVG（`<polyline>` + 面积多边形），无脚本、无外链，符合 CSP；
  只画到今天。
- **最近做过的题**：时间、题号、题名、难度档位（带段位配色）、得分，最多 8 条。

`python tests/render_preview.py [输出目录]` 现在一次生成 7 张图：题面正文、样例，以及 history / mistakes / me / 提交详情 / 解析五张记录卡片，用来肉眼检查排版与品牌一致性。
