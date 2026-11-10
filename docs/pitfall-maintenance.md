# 坑点题库维护

正式题库位于 `plugins/astrbot_plugin_kouhu_oj/pitfalls.json`，候选区位于 `data/pitfall_candidates.json`。候选不会打包为可抽取题目。历史条目的上游来源没有记录，统一标记 unknown，不推断许可或伪造来源。既有机器验证条目属于历史保留，不代表完成来源补全。

新增或修订题目保留稳定 id，包含 revision、provenance（url、collected_at、license、rewrite）和 review。分类使用现有规范名称；正式入库前审核题干是否泄露答案、解释是否符合题设、fix 是否真的修复。行为反例只证明特定输入，不能代替编辑审核或证明所有情况正确。

```bash
python3 tools/pitfall_bank.py check --bank plugins/astrbot_plugin_kouhu_oj/pitfalls.json
python3 tools/pitfall_bank.py report --bank plugins/astrbot_plugin_kouhu_oj/pitfalls.json --output /tmp/pitfall-report.json
python3 tools/pitfall_bank.py stage --bank plugins/astrbot_plugin_kouhu_oj/pitfalls.json --candidates data/pitfall_candidates.json --id ID --output /tmp/staged.json
```

编辑暂存文件补齐来源、采集日期、许可/引用要求、改写说明、可执行 case，人工审阅题干/答案/修复后再发布：

```bash
python3 tools/pitfall_bank.py approve --candidates /tmp/staged.json --id ID --reviewer 审核者 --note 审核说明 --bank plugins/astrbot_plugin_kouhu_oj/pitfalls.json --archive /tmp/pitfall-archive
```

approve 重新编译和执行 buggy/fix；仅有 verified=true 无法绕过。来源或行为证据不足会拒绝发布。运行验证器会执行本地 C++，应只使用经过检查的维护输入。成功发布前保存原文件 SHA-256 快照，写入修订和审核记录。发生问题可恢复快照：

```bash
python3 tools/pitfall_bank.py rollback --archive /tmp/pitfall-archive/bank-SHA256.json --bank plugins/astrbot_plugin_kouhu_oj/pitfalls.json --archive-dir /tmp/pitfall-archive
```

独立验证候选或正式库：

```bash
python3 tools/validate_pitfalls.py --bank plugins/astrbot_plugin_kouhu_oj/pitfalls.json --strict --json
```

编译通过与警告不是行为证据；stdout 要求正常退出并匹配全部输出，stdout_line 显式匹配完整一行；UB 使用 sanitizer 检查，未报错不能证明没有 UB。不同编译器、库实现、浮点环境和优化会影响平台相关反例。验证失败应调查、修订或回到候选区，不把尚未验证直接称为错误。

线上管理员可先通过 tickets pause 工单号 原因 暂停本群抽取，再修订和发版，最后 tickets unpause 工单号 原因。关闭工单不等于修订了题库。候选回归正式库时保持 id，已有群出题记忆仍然有效。
