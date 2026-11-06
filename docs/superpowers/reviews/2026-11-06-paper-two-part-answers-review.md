# 两段解析与本卷补答验收记录

日期：2026-11-06。基线：`065c5ab71e613acb28260bd9d99ea7a505baf828`。分支：`feat/paper-two-part-answers`。
范围与契约见[设计](../specs/2026-11-06-paper-two-part-answers.md)。

## 评审结论

开工 advisor：**GO_WITH_FIXES**。要求保持正式 schema、在原事务中保留生成来源、绑定补答快照、明确不确定性、保证报告与 flags 一致、增加安全预览。均已落实。

终审 advisor：**GO**。复核代码、测试、26题数学内容与实际草稿；期间发现 OCR 去掉 Markdown 标题导致嵌套解析，已补失败用例并修复。第26题最小链跨度经独立数学复核更正，正式回填采用最终正确结果。未发现剩余阻塞项。

## 验证

以下均在本机执行；测试与 smoke 使用隔离工作区，不修改正式数据：

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_paper_answers.py' -v
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_import_module.py' -v
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_paper_automation.py' -v
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_ocr_mineru.py' -v
python3 import-module/smoke.py
CHROME=/snap/bin/chromium .codex-run/browser-venv/bin/python import-module/browser-smoke.py
python3 scripts/validate.py
node --check review-ui/app.js
git diff --check
```

单测分别8、57、21、17项，共103项通过。新增测试已观察失败后再实施，覆盖文本/CSV/OCR输入、旧解析、内容冲突、规范标题、补答清单错配与覆盖拒绝、离线命令、修订审计、风险重算、空答案门禁、候选正式字段。进程 smoke、Chromium smoke、正式 validator 及脚本语法/空白检查通过。浏览器验证两段可读、空答案提示、HTML按文本显示、版本过期拒绝审批、移动端无横向溢出。

正式 validator 仍提示 `cnm_130`、`cnm_192` 的 standards 为空；基线 validator 与正式数据均无改动，该提示属于既有数据缺口，不影响默认校验退出码。未重新运行真实 OCR；本次复用同一 PDF 的既有规范文本与题图，避免改变原任务来源。

## 实际任务

- 工作区：`/home/zhangjun/cn-math-import.qIReBS`。
- 原任务：`20261106-164917-5c979274`；无新任务，无重 OCR。
- PDF SHA256：`fa8516656bbb21579162b26ffc05c3a2557b8882a288bb371460e9a0ec4548f3`。
- 原稿摘要：`ad4ee2a19caaf31942b69f0b931beb4c66ddb5b55a320ad36ef72e8ba4aa6c13`。
- 修订后摘要：`bc5dfaa881fe2da93f34967a180a7ce9340ef76b45d548d3e8429dc7d4481bd0`。
- 通过本地8765 API的既有 `revise` 完成，保留 before/after 审计，状态仍为 `needs_review`。本地 API 已重载新代码。
- 26题均有两段解析；15题填写最终答案，11题仅解析且保留空答案；`generated=26`、`answerUncertain=11`、`originalRequired=11`。
- 图依赖：10、16、19、21；OCR选项/表格残缺：1—5、7、23。没有根据残缺选项猜字母，也没有补造图中数值。
- `reports/parse.json` 与 meta flags 一致；当前候选校验未通过，因为11题答案仍空，属于预期门禁。无 verify mismatch 不表示已机器证明所有解答。
- 真实8765审核台：26张卡片、52个分段标题，状态待审，页面错误0，移动端无横向溢出。核对仓库及私有工作区共103个正式JSON摘要，全部未变。

## 产物与限制

私有工作区任务目录包含 `draft/questions.json`、`reports/parse.json`、`meta.json` 和 `review/revision-*.json`。本机忽略目录 `.codex-run/` 保存 `answers-before-snapshot.json`、`paper-solutions.json`、`paper-revised.json`、`answers-after-snapshot.json`、`answers-summary.json`、`answers-formal-before.json`、测试日志及 `paper-review-two-part-{desktop,answered,mobile}.png`，最终交付写入 `last-message.md`。这些私有草稿、PDF与认证环境均不提交Git。

学段限制由编辑者声明并经本次内容评审，不是程序自动证明；原有自动 topicId 挂点未更改，仍需人审。图形、OCR及11个空答案需对照原卷补齐，生成稿没有批准或 apply。未推 upstream、未 force-push、未部署 qqvps。
