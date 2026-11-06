# 试卷可审草稿交付评审

基线：`main@fd044bc8f19ff99f087d4431944a95a2632c2304`。分支：`feat/paper-structure-section-notes`。
设计依据：[说明行与可审草稿契约](../specs/2026-11-06-paper-reviewable-draft.md)。仅本地开发，不部署 qqvps，不修改正式 JSON。

## 开工与终审

开工 advisor：**GO_WITH_FIXES**。要求严格分节说明边界、真实括号/LaTeX 标签支持及乱序留证、初始证据与当前风险分离、当前草稿资源 apply 门闸、两层资源生命周期及安全下载。主代理采纳并将 I-1 标记为 resolved 后开始 TDD。

终审过程中发现并修复：旧 A. 选项正文点(A)抢占标签、引用式图片漏检、评分说明前缀过宽，以及图片路径改写的单段/多页累计 UTF-8 字节预算。均先补失败测试，再修正实现；混排不能确认归属仍拒绝。另保持资源文件不存在的 HTTP 404 语义。I-2 已 resolved。最终 advisor 结论：**GO**；advisor 独立核验最新真实任务 `20261106-164917-5c979274`、114 项回归日志及 6 项独立终审缺陷测试，无剩余必修项。

## 验证证据

| 验证 | 结果 |
| --- | --- |
| `python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_paper_automation.py' -q` | 21 项通过 |
| 同命令，`-p 'test_ocr_mineru.py'` | 17 项通过，含两层清理、同名页段、非法/缺失/超限图片、单段及全局改写预算 |
| 同命令，`-p 'test_import_module.py'` | 57 项通过 |
| 同命令，`-p 'test_wrongbook*.py'` | 19 项通过 |
| `python3 import-module/smoke.py` | 通过，真实独立 API/CLI/worker、校验与人工门闸；OCR 部分为明确标识的 fixture |
| `python3 scripts/validate.py` | `VALIDATION PASSED` |
| `git diff --check`；正式 JSON 对比 | 通过；仓库无正式 JSON 修改，真实工作区五份正式 JSON 与基线字节一致 |

TDD 日志在本地 `.codex-run/`：`paper-red.log`、`assets-red.log`、`advisor-fixes-{red,green}.log`、`rubric-mixed-{red,green}.log`、`asset-budget-red.log`、`aggregate-budget-{red,green}.log`。最终回归日志为 `paper-green.log`、`assets-green.log`、`import-regression.log`、`wrongbook-regression.log`、`import-smoke-final.log`、`formal-validate.log`。

## 真实 PDF 与人工复核边界

原失败任务 `20261106-162108-16f66d62`：已检出 1–26 题，但说明行触发 `PAPER_STRUCTURE_FAILED`，草稿 0 题。
同一 PDF SHA-256：`fa8516656bbb21579162b26ffc05c3a2557b8882a288bb371460e9a0ec4548f3`。

首次真实复测 `20261106-163730-32878768` 及终审规则修正后复测 `20261106-164612-acec5288` 均为 `needs_review`、无任务错误，26 题全部有 `cnm_` 挂点；首次复测 26 个 `best_match`、26 个正分、0 个 `zeroEvidence`、20 个不同知识点。最终预算增量后的复测任务为 `20261106-164917-5c979274`，结果为 `needs_review`、26 题/26 topicId、26 个 `best_match`（均正分）、0 个 `zeroEvidence`、20 个不同知识点；26 答案留空，3 图已保存且逐一鉴权下载核对，缺图为 0。

26 个答案均留空；3 张引用题图已保存在私有 job，关联 Q10/Q16/Q21。首次复测已通过实际鉴权下载逐字节核对。风险为 `PAPER_ANSWER_MISSING`、`PAPER_ASSET_REVIEW_REQUIRED`，无 `PAPER_ASSET_MISSING`；`flags.requiresHumanReview=true`。原 validator 的缺答案检查按预期不通过，不能据此把待审草稿视为可入库。

说明行保存在 `structure.ignoredNotes` 第 78 行；10 道选择题保留 `choiceNormalizations` 原文，第 4 题原始顺序 A/B/D/C。OCR 的公式包裹、表格和阅读顺序仍可能失真，操作员须对照原 PDF，不能用规则得分宣称挂点准确率。现阶段图片只用于私有审核，不自动发布到正式库；须补充有依据的答案，并将图片引用转写为完整文字等正式库支持的形式后再审批/apply。合法已有 `figure` SVG 仍交原 validator。

本地详细复测记录：`.import-test/RESULT-retest.md`；完整快照与统计在 `.import-test/`；交付 SHA、远端确认及最终 advisor 结论在 `.codex-run/last-message.md`。原 PDF、token/env、工作区私有产物均不进入提交。
