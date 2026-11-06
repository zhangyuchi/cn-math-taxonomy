# 试卷导入强制挂点设计

- 日期：2026-11-06；Task Mode：`design-build`；Outcome Status：`complete`（实现与验证）。
- 负责人：主代理；advisor 开工、设计核对及终审均 GO。提交与远端 SHA 在推送后写入 `.codex-run/last-message.md`。
- 用户目标：每道成功导入题自动具有同 `--prefix` 学科的现有 `topicId`；人工仅复核、可改挂点，无可用点明确失败。

## 证据、约束与概念

基线 `d23e31f`，分支 `feat/import-module`，开工工作区干净。`paper.py` 为文本与 OCR 共用挂点入口，原 `_suggest` 按名称及二字片段排序，原缺点路径留下 null。`worker.py` 已在全库→job 锁内调用解析；`validation.py` 在 apply 时依据最终挂点分配正式题号。`revision.py` 原先重建 parse 报告会丢失 matches。`topics.json` 当前数学点缺省 subject，validator 允许数学缺省、null 或 math；其他学科字段与配置一致。

“候选”指 ID 前缀匹配、非 external 且 subject 与配置兼容的现有知识点。“得分”指名称规则分，不是概率或教师背书。“初始匹配”指本轮成功解析时的机器选择，供复核追溯，不代表修订后当前挂点的评分。已知评分可确定复现；未知的是任意真实卷的教学归属准确率，保留业务审批处理此不确定性。

工作模型：worker 负责自动选点和证据，审核者核对或改挂，apply 延用原校验与事务。复用现有同步锁与题 schema，运行成本由 worker 承担。非目标：新建知识点、跨科兜底、猜测答案、自动批准、部署或重跑真实 OCR。输入为已切题文本及现库快照，输出为每题有挂点的草稿和可追溯报告，或明确失败；原有不完整文本块与 PDF 全卷完整性规则继续生效。

## 逻辑链与决策

| ID | 因为 | 所以 |
| --- | --- | --- |
| LC-1 | 人工补点不满足用户要求 | 未填或同前缀未知 ID 必须自动选择最佳候选，无最低分门槛 |
| LC-2 | 跨学科和虚构 ID 不能表示有效归属 | 限定同科非 external；无候选整卷失败，不发布本轮草稿 |
| LC-3 | 同分、零分和人工改挂仍须可解释 | 排序确定、保存规则分和前五候选，修订后保留初始匹配证据并标明范围 |
| LC-4 | 用户只取消手工补点前提 | 保留显式有效点、审批、校验及最终题号分配 |

DEC-1：采用现有评分：题干与名称 casefold 后，完整名称包含加 20 分，不同连续二字片段每命中一个加 1 分；同分按 topicId 字典序升序。复用原机制使结果可解释且无需依赖。拒绝阈值留 null（违反 LC-1）、随机或跨科兜底（违反 LC-2/3）、引入语义模型（超出需求）。零分仍选择第一项并标记 zeroEvidence。匹配效果不足时再评估算法。

DEC-2：合法显式点优先，明确跨前缀继续 SUBJECT_CONFLICT。同前缀但字段学科冲突的点不参与候选；数学兼容 subject 缺省/null/math。每次解析只读一次 topics、每题共用一次排名；不额外读取全题库。

DEC-3：报告 `matches` 保留 id、match、suppliedTopicId、suggestedTopicIds，增加 topicId、score、zeroEvidence、tieBreak 和 candidates。候选含 topicId/name/score/nameMatched/matchedKeywords；最多五项，包含零分项。match 为 explicit 或 best_match；显式点的 score 对应该显式点，候选仍按排名显示。顶层 `matchesScope=initial_parse`；修订仅保留机器证据，不把当前改挂伪装成原算法选择。

## 模块、错误与兼容边界

主线仅修改共同解析入口的选点，辅助报告留在 parse schema，不写入正式题 schema。沿用 worker 持有的快照和原锁；不新增并发、远程调用或存储迁移。无候选抛出 `NO_TOPIC_CANDIDATES`，worker 保留原文、记录 failed/error/parse 报告；现有 requeue 可重新解析 failed/rejected 任务。新点/题继续人工批准，审批摘要及可恢复 apply 不变。旧 null 草稿不会自动迁移：needs_review 任务先驳回再 requeue；approved 任务可直接修订合法挂点并重新批准；已应用任务不重试。

高级架构检查适用性：Selected Check IDs=`AA-DELIVERY`。原因：持久报告新增字段且修订会重建报告；兼容旧字段、明确初始匹配范围，避免改挂后误导。旧读取器忽略新增字段；历史规格中 null 路径由本规格取代。回滚代码不会修改已应用题目，审核报告新增字段无需迁移。

## 承诺、变更组与验证计划

| 设计承诺 | 逻辑链 | 变更组与落点 | 验证 |
| --- | --- | --- | --- |
| DC-1：每个成功解析题有合法同科挂点；无候选失败 | LC-1/2 | CHG-1：paper.py 共同入口排名与选点 | V-1：缺点 MD/TXT/CSV、未知点、各学科、零分/同分/乱序、external/字段冲突/无候选、跨科拒绝 |
| DC-2：初始规则得分及候选可追溯，改挂不丢证据 | LC-3 | CHG-1/2：paper.py 报告及 revision.py 保留 | V-2：报告与初始题一致、修订后证据不变并标注范围、改挂后可 apply |
| DC-3：无需手工补点即可批准/apply；保留现有门禁 | LC-4 | CHG-1/2：测试与独立进程 smoke | V-3：原 null 路径及 OCR 必挂、coverage unresolved=0、批准前拒绝 apply、批准后正式题号正确；完整 Python/Node/validator 回归 |
| DC-4：说明准确体现自动挂点与复核可改 | LC-1/3/4 | CHG-3：指南、两个 README、历史规格覆盖说明 | V-4：文档与失败码核对、链接检查、advisor 终审 GO、推送 SHA 确认 |

实施顺序：advisor 开工 GO → 先写回归并观察旧行为失败 → CHG-1/2 实现 → CHG-3 文档 → 全部验证 → 同一 advisor 终审 → 精确路径提交推送与执行记录。

## Issues、Blocks 与风险

- ISSUE-1（resolved，开工评审，影响 DC-1/V-1）：数学缺省/null subject 兼容；按 validator 规则筛选并补测试。负责人主代理，无 Block。
- ISSUE-2（resolved，开工评审，影响 DC-2/CHG-2/V-2）：修订原先丢失 matches；决定保留初始机器证据并标明 initial_parse，补改挂回归。负责人主代理，无 Block。
- 无 open issue；Blocks：无。
- Accepted Risk R-1：零分规则选择不证明语义正确，按用户明确要求仍挂第一项并显式标识；审核者可改，发现规则匹配不足时重新评估 DEC-1。
- Follow-ups：无当前范围必需之外的扩展。Plan B：修复评分后 requeue failed/rejected 任务，needs_review 任务先驳回再 requeue，approved 任务可直接修订挂点并重新批准；不绕过审批或校验，不重试已应用任务。
- 完成条件：DC-* → CHG-* → V-* 全部验证，无 open issue/block，advisor 终审 GO，远端分支与本地提交一致。

## 完成证据

- DC-1/CHG-1/V-1：先写测试后观察原逻辑失败，聚焦 7 项测试中的 17 个断言/子用例失败均指向 null、未自动赋点或无候选未失败；OCR 原 null 路径亦有失败证据。实现后全部通过，覆盖十学科、缺省/null/math、零分/同分/乱序、同前缀未知 ID、external 及字段学科冲突排除。
- DC-2/CHG-1/2/V-2：报告匹配 ID/分数与初始题一致；改挂后初始证据完整保留且明确作用范围，重新批准/apply 成功。显式零分点可保留自身分数，不误取机器第一名分数。
- DC-3/CHG-1/2/V-3：`python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v`：84/84 通过。`python3 import-module/smoke.py`：`ok=true`，缺省挂点的题自动选 `cnm_006`、22 分，无修订直接批准/apply。`.codex-run/browser-venv/bin/python import-module/browser-smoke.py`：`ok=true`，浏览器异常为空。`python3 scripts/validate.py` 通过；`node tests/test_engine.js`、`node tests/test_blindspots.js`、`node tests/test_children.js` 分别 95、8、38 项通过。
- DC-4/CHG-3/V-4：指南和 README 去掉必须手工补点的前提，记录评分/并列/零分/失败码/初始证据语义；47 个文档相对链接目标检查通过，`git diff --check` 通过。advisor 审查全部 11 路径并独立运行稳定排序、改挂保留证据、无候选失败、OCR 必挂四项回归，4/4 通过，终审 GO。
- 运行边界：正式数据、部署和审核 UI 无变更；validator 的 `cnm_130`、`cnm_192` 空课标警告来自未改动的基线数据。本轮使用真实解析/worker 与 OCR 引擎替身验证自动路径，未重跑真实 MinerU，不宣称语义归属或 OCR 准确率。
- Issues/Blocks 最终均无 open 项。交付日志位于忽略的 `.codex-run/topic-match-*.log|json`；推送及最终 SHA 以 `.codex-run/last-message.md` 为准。
