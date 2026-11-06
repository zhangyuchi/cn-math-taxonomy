# 试卷两段解析与受控补答

Task Mode：design-build。基线：`065c5ab71e613acb28260bd9d99ea7a505baf828`。负责人：Codex；日期：2026-11-06。
设计已获 advisor 开工 `GO_WITH_FIXES`，修正落实后终审 `GO`；Outcome Status：complete。

## 证据与目标

- `scripts/validate.py` 的 `Q_FIELDS` 拒绝未知题字段，要求 `answer`、`explanation` 非空；选择题答案须为选项键。因此不增加正式题字段。
- `paper.py` 将文本与 OCR 转成相同草稿；原有解析只存字符串。`revision.py` 已提供五文件摘要修订和恢复，修订会撤销审批。
- `worker.write_reports` 重建风险；其调用方原先持有旧 meta，新增 flags 必须避免被旧对象覆盖。
- 实测任务 `20261106-164917-5c979274` 有26题，答案全空。PDF SHA256 为 `fa8516656bbb21579162b26ffc05c3a2557b8882a288bb371460e9a0ec4548f3`。结构成功不能证明公式、选项或题图准确。

目标是让审核者直接阅读“思路提示→详细解答”，在初一知识范围内回填本卷可确定答案。成功须同时具备解析/兼容测试、待审草稿、风险报告、可读审核预览与指南。不得猜测图中数据、残缺选项字母，不改正式库、不部署、不推 upstream。
操作者负责校对与批准；程序负责格式、版本绑定和准入，不能验证解法的学段或数学真实性。

## 契约与决策

选择方案 B：`answer` 保留最终结果，`explanation` 统一为 `### 思路提示` 和 `### 详细解答` 两段 Markdown。方案 A 会要求同步正式 schema、学生端和导出方，超出必要范围。
输入允许临时字段 `solutionHint` / `solutionDetail`（中文 OCR 标签同义），输出只合成既有 `explanation`。旧解析作为详细解答保留；无提示时明确写“原文未提供思路提示，待人工补充”，不编造提示。混合输入内容冲突拒绝；旧任务读取不批量迁移。

生成来源用正文内可读注记 `【生成补答，待人工复核】`；不确定用 `【答案不确定】`；图或原文依赖用 `【依赖原图/需对照原卷】`。当前报告从注记推导 `generated`、`answerUncertain`、`originalRequired`，汇总 `PAPER_ANSWER_GENERATED`、`PAPER_ANSWER_UNCERTAIN`、`PAPER_ORIGINAL_REVIEW_REQUIRED`，同时写 meta flags。注记表示编辑者声明，不表示数学已验证。移除注记需人工确认，修订历史仍保留旧稿。

离线补答文件绑定 `jobId`、`sourceSha256`、`expectedDraftSha256` 和完整唯一题 ID 集合，只含答案、提示、详解与两类风险声明；只接受七年级约束。转换器不求解、不调用模型、不修改服务或正式库，拒绝覆盖已有非空答案或非占位解析。对 uncertain/originalRequired 行强制要求空 answer，输出交既有 `revise`，服务锁内再次校验摘要。不会添加第六份草稿或独立生成状态，因此恢复沿用现有五文件事务。

审核预览使用 `textContent` 展示完整解析与风险，保留换行，不解释上传 HTML。校验和导出继续消费原字符串；空 answer 不因两段齐全而通过。生成待审不自动批准，apply 必须经过人工门闸和原 validator。

## 逻辑链与验证映射

| 逻辑链 / 设计承诺 | 必要改动 | 验证 |
|---|---|---|
| LC-1 / DC-1：正式 schema 不变，所以两段合成既有字符串 | CHG-1：解析器与共享格式函数 | V-1：MD/TXT/CSV/OCR、旧解析、冲突、候选导出字段 |
| LC-2 / DC-2：生成内容不能视为已验证，所以保留来源/不确定性与人工门闸 | CHG-2：风险汇总、meta 同源；离线绑定转换 | V-2：错 job/source/digest/题集、非空覆盖、空答案、修订和 flags |
| LC-3 / DC-3：JSON 转义换行难以扫读，所以增加安全原文预览 | CHG-3：审核台与指南 | V-3：浏览器两段可见、空答案、HTML 不执行、移动端 |
| LC-4 / DC-4：本卷无答案区，所以由受约束的人工生成稿回填 | CHG-4：私有补答产物和既有 revise | V-4：26题统计、advisor 数学复核、正式文件摘要不变 |

处理路径：源→解析归一化→五文件草稿→风险/校验；旧空稿→一致快照→绑定补答→离线转换→revise→needs_review→人工批准→原校验/apply。
架构判断：复用现有标准库、事务与 validator，无新依赖；高级检查仅关注持久化与旧 meta 覆盖风险，不扩展部署或并发模型。

## 实施与问题账本

顺序：失败测试→CHG-1/2→CHG-3→CHG-4→回归→advisor 终审→提交推 origin。

| Issue | 状态 | 处置 / 影响 |
|---|---|---|
| I-1：未知正式字段不兼容 | resolved | 采用 B，DC-1 / V-1 |
| I-2：独立报告来源在 revise 后丢失 | resolved | 来源随 explanation 和原事务持久化，DC-2 / V-2 |
| I-3：write_reports 后旧 meta 覆盖 flags | resolved | 调用方重新读取再保存，测试验证报告/flags 一致，DC-2 / V-2 |
| I-4：第26题最小链跨度推理错误风险 | resolved | 独立复核后采用差为 ±x²±2x 的四种结果，V-4 终审通过 |
| I-5：OCR 去标题导致规范两段嵌套 | resolved | 三种规范标题输入均有失败→通过回归，DC-1 / V-1 |

Blocks：无 open block。Follow-ups：通用自动数学求解器不在本次范围。
Accepted Risks：初一限制为内容评审约束；原卷/OCR需人审，残缺题继续留空。机器注记可被编辑，审计不是防篡改认证。
Plan B：若服务未加载新代码，用同代码本地 Service.revise 在既有锁内修订私有工作区；不重导入、不绕过事务。

实际验收：103项相关单测、进程 smoke、浏览器 smoke 与正式库 validator 通过。V-4 使用原 job 的 API revise，26题两段、15题有答案、11题空答案，仍为 `needs_review`；实际浏览器26卡片/52标题，103个正式 JSON 文件摘要未变。空答案候选校验失败符合预期。证据与残余风险见[验收记录](../reviews/2026-11-06-paper-two-part-answers-review.md)。
