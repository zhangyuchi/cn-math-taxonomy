# 导入模块 v1 骨架实现计划

> 本文保留上一轮骨架的历史范围与结果。本轮完整v1/OCR/生产切流见[完整计划](../plans/2026-11-06-import-module-completion.md)和[完整验收](../reviews/2026-11-06-import-module-completion-review.md)。

- Task Mode：design-build；Outcome Status：complete（本次骨架的实现、验收与独立复核；Git 交付结果见最终回复）。
- 准入：advisor 最终结论 `GO_WITH_FIXES`；主代理核查后接受 B1–B6，已写入规格附录 C、WORKFLOW 与 deploy/README，阻塞约定已闭合。
- 权威：11 月 6 日任务简报与修订规格；原规格完整 v1 是后续目标。
- 读者：本仓库开发者及运维；维护责任：导入模块维护者。

## 证据快照与目标

2026-11-06，基线提交 `d7dd33d`，当前 `feat/import-module`，origin 为 fork，upstream 为原仓。
`scripts/validate.py` 基线通过（3297 点、4868 边、13335 题）；两条已有空 standards 警告为 cnm_130/cnm_192。
`check_mapping_files` 只支持数学和英语；`question_errors` 不允许仅 TOC 新点直接入正式库；`check_graph` 不允许孤立点。
现有 validator 使用固定路径及全局状态，需要隔离进程适配。Python 3.14.4 可用，无需安装第三方依赖。
`.gitignore` 已有用户新增 `.codex-run/`，不纳入本任务提交。VPS 现状仅来自简报，未现场验证。

成功信号：CLI 经 HTTP 创建 job，worker 生成 TOC 草稿到 needs_review；合法显式 mappings 校验后自动 applied；审批及失败路径能证明正式文件不变；分支普通 push 并核验远端。
非目标：完整 paper 解析、题目编辑、完整多文件事务、OCR、公开上传、自动发布、生产配置或大 JSON 修改。

## 工作模型、概念与逻辑链

代码仓库只拥有实现；workspace 拥有正式数据副本、不可变 source、draft、reports、review、队列与锁。
job 是一次不可变输入及其解析/审核记录；审批摘要只授权该版 draft；apply 是本次唯一正式数据写入口。
审核者拥有显式批准权；自动 mappings 是受实际内容限制的系统策略，不能由客户端自报“已审核”。
假设：Linux 本地文件系统支持 flock 与同目录 os.replace；同一 workspace 的导入写入全部由本服务协调。
外部编辑正式数据、静态目录泄露、跨机器共享锁不在该假设内，部署需保持独立工作区和单 worker。

- LC-1：因为原文不能公开且 release 会重建，因此使用静态根之外的持久 workspace。
- LC-2：因为 D4 禁止新点/题自动入库，因此门闸同时检查 job 类型和实际 draft，不信任 flags。
- LC-3：因为 TOC 不提供题库，因此保留真实校验缺口，到 needs_review 即完成 expand 骨架目标。
- LC-4：因为 os.replace 只保证单文件，因此仅 mappings 可 apply，其他范围明确失败关闭。
- LC-5：因为正式库可在排队期间变化，因此 apply 在全库锁内重新构建候选并调用未放宽的全量 validator。
- LC-6：因为替换与 meta 保存存在中断窗口，因此用持久 intent 和前后摘要恢复幂等结果。

## 决策与必要视图

| 决策 | 选择与理由 | 排除方案 / 重评条件 |
|---|---|---|
| API | 标准库 HTTP、Bearer token，loopback；CLI 仅请求 API | FastAPI 可用但本次无现成依赖；公网服务另行评估 |
| 队列 | data/imports/_queue 的持久指针，单 worker flock；启动恢复 running | Redis 无当前必要性；多机后再评估 |
| 校验 | 子进程导入现有 validate，将路径指向临时候选副本 | 不并发复用全局 errors，不修改正式 validator |
| 写入 | per-job 锁→全库锁→核对审批→候选校验→intent→单文件 replace→applied | 不循环替换多文件伪装整体事务 |
| 审核 | 独立 origin；公开无数据登录页换短期 HttpOnly/SameSite session；review/API 需认证 | 不把 token 放 URL、仓库、localStorage；gate 生产接入后续验收 |
| 恢复 | source 摘要验证；requeue 不改 source；已提交 intent 识别后恢复 applied | 源修订创建新 job；非预期目标摘要失败关闭 |

数据流：`import-cli → import-api → _queue → import-worker → draft/reports → review-ui → approve/reject → apply`。
状态：queued→running→needs_review→approved→applied；解析失败→failed；待审→rejected；failed/rejected→queued。
mappings 校验成功可 running→approved→applied，决策 actor 为 `system:auto-mappings`；缺 token 拒绝启动。
apply 未批准→REVIEW_REQUIRED；审批过期→DRAFT_CHANGED；范围不支持→APPLY_SCOPE_UNSUPPORTED；校验失败保持 approved；锁冲突→409。
PDF→OCR_REQUIRED；paper 文本→PIPELINE_NOT_IMPLEMENTED；两者都保留 job/source，不伪装成功。

API 以 `/import/v1` 为根，创建 textbook/paper 使用 multipart（file+显式参数），查询 list/status/diff/files，POST approve/reject/apply/requeue。
错误统一 `{error, code}`；CLI 成功 0、业务拒绝 2、系统错误 1。jobId 为上海时间 `YYYYMMDD-HHMMSS-<shortuuid>`。
MD/文本 TOC 采用明确 grade/term 元数据与 Markdown 章/节，或 CLI grade/term；CSV 采用锁定列。
mappings CSV/表格必须含显式 topicId，不自动猜挂点；edition/label/source 必填，合并保留已有未涉及条目。

高级检查选择：本次只展开授权/状态一致性、单文件持久化恢复、上游兼容与部署隔离；不扩展分布式设计与性能框架。

## 任务切片、目录、接口与验证映射

| 承诺 | Change Group 与落点 | 验证 |
|---|---|---|
| DC-1：真实落盘与持久队列，源不可变 | CHG-1 `import_module/store.py`、`worker.py`、`data/imports/.gitignore` | V-1 job 布局/上海时间/source hash/恢复/requeue/PDF/paper |
| DC-2：十学科 TOC、真实候选报告 | CHG-2 `subjects.json`、`parsers.py`、`validation.py` | V-2 十前缀、CSV/MD、元数据冲突、完整 validator、正式字节不变 |
| DC-3：D4、摘要审批、幂等单文件 apply | CHG-3 `apply.py`、store 决策接口 | V-3 mappings 自动/人工、混合草稿/新点/题、审批过期、校验失败、锁竞争、replace 后恢复 |
| DC-4：认证 HTTP 与最小审核台 | CHG-4 `api.py`、`cli.py`、三个包装入口、`review-ui/` | V-4 真 HTTP/CLI、无 token/路径越界/CSRF、只读 diff 与批准/驳回 |
| DC-5：上游/部署边界可执行 | CHG-5 规格附录、WORKFLOW、deploy/README、`import-module/README.md`、示例 | V-5 原 validate/JS 回归、文档 smoke、正式 JSON 哈希、限定暂存、远端 SHA |

执行顺序：先完成本文及规格修订；写 unittest 观察功能缺失的失败；依次实现 CHG-1–4，逐项验证；完成运行说明与可重复 smoke；完整变更自查后请原 advisor 复核；修复确认问题再提交/push。
模块公共函数以中文 docstring 说明责任；状态、审批及原子写入的关键函数补充错误/锁/恢复契约说明。

## Issues / Blocks / Follow-ups / Accepted Risks

| Issue / Block | 状态 | 解决记录 |
|---|---|---|
| I-1 / B1 schema 漂移 | resolved | 规格附录 C.1，十前缀配置，mappings 限数学/英语 |
| I-2 / B2 TOC 与正式覆盖率冲突 | resolved | C.2，只到 needs_review，完整校验保持原义 |
| I-3 / B3 多文件原子性与范围 | resolved | C.3，单文件 mappings apply，其他明确失败关闭 |
| I-4 / B4 审批/恢复不完整 | resolved | C.4，摘要/锁/intent/source 重试契约 |
| I-5 / B5 静态泄露与 SW | resolved | C.5，独立 workspace/端口/origin、统一鉴权 |
| I-6 / B6 发布现状与所有权 | resolved | C.6 与 WORKFLOW/deploy 修订，禁止本次切流 |

编码前无 open issue/open block；以上 resolved 指开工所需约定闭合，实际实现仍需 V-1–V-5 证明。
Follow-ups：完整 paper、多文件事务、题目/课标/边编辑、OCR、生产 gate/systemd、健康检查回滚；均非本次简报骨架验收必需。
Accepted Risks：标准库 API 仅本地运维、单 worker；候选校验复制正式数据会产生本地 I/O；源/草稿不提供浏览器编辑；没有上线或真实 gate 验收。
Plan B：任何校验/恢复歧义失败关闭并保留 job 与报告；不直接修正式库，不执行线上回滚。

## 计划验证命令（结果另记）

```bash
python3 -m unittest discover -s tests -p 'test_import_module.py' -v
python3 scripts/validate.py
node tests/test_engine.js
node tests/test_children.js
node tests/test_blindspots.js
python3 -m compileall -q import_module
git diff --check
```

另执行独立临时 workspace 的 API/worker/CLI mappings smoke，检查自动 applied 与落盘；真实仓库仅保留忽略规则，不写正式 JSON。

## 执行结果与追踪

DC-1–DC-5 / CHG-1–CHG-5 / V-1–V-5 均已落实，详见 [审核与验收记录](../reviews/2026-11-06-import-module-review.md)。
26 项 unittest、独立进程 smoke、Chromium 审核操作、现有 validator 与 95/38/8 项 JS 回归通过。
advisor 初审 GO_WITH_FIXES；六项约定先修订后开工；代码复核发现的元数据冲突、旧 intent 恢复、mappings diff 与队列测试问题均已修复；最终复核 GO。
Issues/Blocks 无 open 项，后续范围与 Accepted Risks 保持上文边界；当前未执行生产部署或 gate 验收。
