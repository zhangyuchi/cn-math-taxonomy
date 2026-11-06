# PDF 自动结构化与大小限制验收

- 范围：`feat/import-module`，基线 `fd6e4e7`，开工工作区干净。
- 对应设计：[自动结构化规格](../specs/2026-11-06-pdf-paper-automation.md)。实施模式：`design-build` / `code-quality-gate` implementation。
- 最终结论：**advisor 开工 GO → 实现与自测 → advisor 终审 GO**；无剩余阻塞，可按用户授权提交并普通推送 `origin/feat/import-module`。本轮不要求用户复验。

## 交付行为

统一 `import_module/limits.py`：文本源 16 MiB、PDF 源 64 MiB、OCR 单段与合并输出 64 MiB、入站修订草稿 64 MiB、HTTP 请求 65 MiB。CLI 上传、CLI 修订、worker 源回读与 OCR 输出均有界读取；源仍校验不可变摘要。现有单 worker、超时终止进程组、临时文件清理和恢复事务保留。

所有 PDF paper（包括已带英文结构字段的插件输出）统一经过自动结构解析。自动切题、保留子问和公式，提取正文/卷末答案及 Markdown/简单 HTML 答案表，推断类型、默认难度并提供挂点建议。题目写现有草稿 schema，未知挂点为 null，等待业务审核批准。PDF 残缺时整卷失败，不要求用户整理 Markdown。

新增失败码：`PAPER_STRUCTURE_FAILED`、`PAPER_ANSWER_MISSING`、`PAPER_ASSET_MISSING`；失败保留 OCR 原文与结构诊断。新题仍无法绕过审批或原 validator 直接 apply。qqvps 重 OCR 默认配置未变，本次只推送代码，不部署线上服务。

## advisor 复核与修复闭环

| 项目 | 验证事实与修复 |
|---|---|
| 开工 | advisor GO；采纳分档上限、同步修订大小、PDF 整卷失败关闭及保留诊断 |
| 分节漏题 | 原实现将首题之后未识别的分节内容放入 preamble；红测复现后改为明确未归属题块失败 |
| 无选项选择题 | 原实现允许显式 choice 不含选项；红测复现后补最终选择题完整性检查 |
| 英文兼容分支绕过 | 原实现重复 Q1 可通过，混合题号可吞入解析；删除独立兼容分支，统一题号与字段提取。重复题号失败，支持的混合题号完整生成两题 |
| 空白/冲突选项 | 空 choices 可降级 short，空白选项及正文与显式 choices 冲突未拒绝；红测复现后按字段存在性校验，归一化空白并拒绝冲突 |
| 终审 | **GO**。advisor 独立运行自动化及适配器共 22 项测试，核阅全量 79 项日志和独立进程 smoke，确认所有 finding 已修复且无剩余阻塞；README/指南一致，qqvps 重 OCR 默认关闭 |

## 测试证据

红测已复现基线普通中文 OCR 返回 `EMPTY_PAPER`、>2 MiB 文件被 HTTP 拒绝、>1 MiB MinerU 输出被拒绝；对应新实现聚焦测试已通过。advisor 发现的结构完整性问题也分别保留红绿回归。

- 大文件测试真实运行 CLI → HTTP → Service，提交超过 2 MiB 的文本和 PDF 并核对源字节；文本 16 MiB/PDF 64 MiB 边界接受，越界拒绝；CLI 与 worker 回读越界明确拒绝。HTTP 在声明长度超过 65 MiB 时无需读正文即返回 413。
- 真实适配器使用临时 HOME 内假 MinerU 可执行入口；假引擎仅输出 `tests/fixtures/paper-mineru.md`，内容为中文原始排版、不含英文导入字段。真实 adapter → `normalize_pdf` → `structure_paper` → worker 产生 **3 题、`needs_review`**，未修改 MD。
- 夹具断言题干、公式、子问、答案 `B/2/两数之和为零。`、选项及 `choice/numeric/short`、默认 `standard`、`topicId:null`、挂点建议及正式库不变。另测 >1 MiB OCR 输出和 >1 MiB 草稿业务修订成功。
- MinerU 单段和合并输出 >1 MiB 成功，64 MiB 单段成功；超过 64 MiB 或合并越界拒绝。既有超时整组清理、不可变源、审批/apply、修订与正式事务恢复回归继续执行。

最终代码已完成以下验证（选项完整性最后补丁之后运行）：

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -q
python3 import-module/smoke.py
python3 scripts/validate.py
node tests/test_engine.js
node tests/test_blindspots.js
node tests/test_children.js
python3 -m compileall -q import_module import-module/ocr_mineru.py
git diff --check
```

- 全部 Python 回归 **79/79 通过**（133.858 秒）；advisor 独立聚焦自动结构化 **11/11**、适配器 **11/11** 通过。
- 独立进程 smoke 返回 `ok:true`：paper/mappings 完成 apply，教材完整修订再审核后 apply，禁用 OCR 为 `OCR_REQUIRED`，fixture 为 `needs_review`，候选 validator 通过。
- 正式 validator 为 `VALIDATION PASSED`；三组 Node 回归退出码均为 0；compileall 和 diff 空白检查通过。
- README、模块说明、指南、设计及验收记录的 **45 个本地链接目标**存在。

## 验证边界

本轮使用协议及 OCR 输出夹具，无真实重 OCR 性能或任意扫描版式准确率结论；用户已允许夹具验收，不需要用户复验。缺少原卷答案、无法对齐的版式和未持久保存的图片引用明确失败，不生成猜测答案或坏题图。审核仍是业务批准，不是格式整理。多栏 OCR 漏行及错字仍受引擎质量影响。

正式 `data/`、`scripts/validate.py` 与 `deploy/` 均无改动。原 validator 输出两项空课标警告：`cnm_130`、`cnm_192`，属于未修改的基线数据，不是本轮新增。
