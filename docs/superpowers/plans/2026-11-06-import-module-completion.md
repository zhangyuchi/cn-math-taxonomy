# 导入模块完整 v1 与生产切流计划

日期：2026-11-06；负责人：本会话主代理；Task Mode：design-build；Outcome Status：in-progress（advisor GO_WITH_FIXES，六项准入约定已闭合）。
本计划承接骨架，取代旧计划中的非目标；历史审核记录仍只证明当时骨架。

## 证据、目标与边界

- E-1：开工 `git fetch --multiple upstream origin` 后，`feat/import-module` 为 dd0609b，较 upstream/main 0 behind / 2 ahead。仅用户 `.gitignore` 新增 `.codex-run/`，获准纳入。不得 force-push。
- E-2：实际读取原规格、骨架计划/审核、WORKFLOW、模块实现、测试、部署脚本与现有 validate/verify。paper 与 expand apply 明确未实现；原发布无健康回滚。
- E-3：SSH qqvps-cd 核实 hostname VM-0-13-ubuntu，内存 1967 MiB；cn-math.service 以 ubuntu 服务旧工作树，loopback:8000。nginx gate 后 `/k12-knowledge-graph/` 代理8000；current 尚不存在，releases 为空。VPS 工作树有未跟踪 docs，不覆盖。
- E-4：正式 JSON 摘要保存于忽略的 `.codex-run/formal-before.json`；后续真实仓库只运行只读验证，apply 验收均使用独立临时数据副本。
- 以上是开工快照，不证明新实现、切流或终审通过；验收结果另记 completion-review。

原始目标：本会话一次完成用户 A–F，自测和切流由代理执行，最后 advisor GO、提交并推送。
成功信号：文本卷可审核并 apply；教材完整草稿能多文件 apply 且写失败/崩溃可恢复；PDF 开关/fixture可证明；线上 current 健康与回滚验收；正式库未 apply 不变；Git远端SHA一致。
非目标：开放公网上传、多租户、整本教材正文默认OCR、修改上游 validator、自动把工作区 apply 发布到静态站、替代历史 build。

## 工作模型、约束与概念

代码/release 由 Git 发布；workspace 拥有正式数据工作副本与私密 job；apply 仅改变 workspace；静态上线是另一次显式发布。
操作者通过 CLI/API 创建任务；审核者在认证审核台修订、批准、应用；主代理负责本次全量验收，不要求用户复验。
审批授权的是完整 draft 摘要，不是任务名；修订即失效。新增点/题的人工门闸由服务器强制。
候选是正式库快照与经审核草稿的合并；正式库校验权威始终是现有 validate，verify报告写job。
多文件事务是持久 intent + 备份 + 提交/补偿恢复，不承诺不合作读者可观察到跨文件瞬时原子快照。
假设：Linux本地文件系统、fsync/flock/同目录replace；workspace写者都走模块；禁止外部编辑运行中的正式数据。非合作写入第三种摘要时失败关闭。
资源约束：VPS只运行轻API/静态/默认禁OCR worker；重解析/OCR在开发机。真实 OCR 引擎可选，fixture不能冒充识别准确率验收。
未知：gate认证实际可用性、发布后HTTP/浏览器与回滚表现，在部署前调查与测试闭合；若无法证明不切流。

## 逻辑链与决策

| 链 | 因为 | 所以 |
|---|---|---|
| LC-6 | 新题/新点需要人工决定挂点、内容、边和标准 | 增加摘要并发检查的完整draft修订入口和审核编辑器 |
| LC-7 | TOC不包含题库，必须保留既有覆盖率/q1/图规则 | expand先报告缺口，再由审核补全；不造题绕过验证 |
| LC-8 | 多个replace不存在整体原子性，单进程也会中断 | 提交前备份和intent，失败补偿回滚；提交后幂等完成状态 |
| LC-9 | 题号/点号建议在排队时会竞争 | 锁内分配正式ID并重映射所有引用，审计保存映射 |
| LC-10 | PDF不能当UTF8，整书默认OCR不合目标 | 显式插件协议，教材必须目录页范围，normalized走同一文本解析 |
| LC-11 | 原文不能暴露，SW可拦同源请求 | 私有持久workspace与独立loopback端口、token认证、SSH转发 |
| LC-12 | 切symlink不证明进程与HTTP正确 | preflight+切流后hash/HTTP健康+失败自动恢复旧current与服务 |

| 决策 | 采用 | 排除及重评条件 |
|---|---|---|
| 审核 | 结构化JSON完整draft修订、最终人工批准，服务器验类型/学科/新增范围 | 不引入富文本框架；不直改正式库；未来专用字段UI可单独改进 |
| 事务 | 全库锁、同job持久备份/stage、prepared→committed journal | 不用多次replace宣称整体原子；不改现有data布局引入版本化根 |
| 校验 | 全量原validator + verify子进程超时/内存上限 | overrideCoverage不能绕过第6节不变量，显式拒绝；不改上游脚本 |
| OCR | 环境变量JSON argv，不使用shell；输出UTF8/大小/退出/超时检查 | fixture接口验收，不把fixture当真实OCR；重引擎后续按运维自选安装 |
| 发布 | git archive、单发布锁、loopback临时端口预检、current切换+健康回滚 | 不重建共享nginx、不影响其他站；首次切流额外保存旧service配置 |

高级检查选择：AA-RUNTIME（事务/审批/worker锁顺序、恢复优先于快照）；AA-DELIVERY（首次迁移、回滚锚点、工作区独立于release）。
主线是审批后可恢复入库与显式发布，不新增分布式队列、数据库或自动商业教辅抓取。

## 接口、状态与恢复视图

既有CLI/API保留。新增 authenticated GET `/jobs/{id}/draft` 与 POST `/jobs/{id}/draft`，请求 `{draft, expectedDraftSha256, comment}`；响应含最新摘要。CLI `draft` / `revise --file --expected-digest`。
修订及批准HTTP均必须expectedDraftSha256；CLI批准先读取快照后携带摘要，UI使用当前显示摘要。GET job在全库锁→job锁内一次返回meta/draft/diff/reports/digest。修订仅needs_review/approved，保存完整before/after的revision-intent，先失效审批退needs_review再写5文件；prepared中断在显式恢复入口回滚，读取/审批拒绝混合版本。任何未完成apply intent禁止修订。
JSON形状限定五份约定文件；标准建议保持独立，正式topics.standards以审核后的实际字段为准；建议不直接改课标正文。
自动审批只限真实纯mappings；其他自动身份无权应用。mismatch禁止apply；未挂点、未知/其他学科挂点或不完整草稿报告失败。
paper解析保留unparsed；零题失败；未指定挂点只建议不采纳。正式q号在锁内续号，不覆盖原题。
状态沿用 queued→running→needs_review→approved→applied；解析错误failed；驳回rejected；修订回needs_review。

全库写/候选/对比快照统一受 `_apply.lock` 协调；显式apply/worker启动恢复先于新写；GET/解析不修复正式库，仅在未完成事务时拒绝快照。各事务在持久prepared后才改目标，committed前失败按备份恢复；prepared崩溃重启恢复旧值，committed恢复applied。
恢复先检查所有target只能为before/after之一，第三种摘要→RECOVERY_CONFLICT且禁止新写；不覆盖外部改动。回滚本身可重入。单文件mappings保留旧intent兼容分支。
锁全局顺序：全库锁→job锁；不在持有job锁时阻塞等待全库锁。所有锁非阻塞409；worker BUSY保留队列稍后重试。
审批绑定draft；intent保存该摘要与正式ID映射；提交完成只写本job授权内容。任何失败报告不污染正式verification_report。

OCR配置 `IMPORT_OCR_COMMAND` 为命令数组JSON（worker进程环境），`--ocr-pages`为教材PDF必填；插件收到输入、输出、类型、页范围。禁用→OCR_REQUIRED；缺教材页→OCR_PAGES_REQUIRED；故障→明确OCR失败码并保留source/job。

发布先生成release并验证HTML/资产/JSON，再切current和重启cn-math.service，验证8000及外部gate路径；失败自动回滚。首次迁移先以旧工作树作为current回滚锚点，并备份unit/drop-in。
API独立8765+Bearer/短期会话；私有环境文件0600；worker OCR默认关闭。SSH转发不要求改同机TLS/gate。

## 承诺与验证映射

| 承诺 | 变更组与责任落点 | 验证 |
|---|---|---|
| DC-6 文本卷/建议/verify与必审 | CHG-6 paper.py、worker、测试/示例 | V-6 MD/TXT/CSV、十学科、建议/零题/unparsed/mismatch/未批拒绝/编号 |
| DC-7 完整草稿审核、摘要绑定 | CHG-7 store/api/cli/review-ui | V-7 并发摘要、approved修订、鉴权/CSRF、Chromium编辑/批准/驳回/apply |
| DC-8 多文件可恢复事务且不放宽校验 | CHG-8 validation/apply/transactions、worker快照协调 | V-8 expand成功、validator失败不写、每写入位置故障回滚、崩溃恢复、外部冲突、mapping回归 |
| DC-9 OCR默认关闭且可切换 | CHG-9 ocr.py、fixture插件、参数/说明 | V-9 OCR_REQUIRED、fixture normalized同管线、超时/退出/无效输出、教材页限制 |
| DC-10 生产可回滚切流/独立workspace | CHG-10 deploy脚本与units/nginx示例、私有验收证据 | V-10 本地发布失败回滚、VPS实际current/HTTP/鉴权/SW隔离、其他站前后状态 |
| DC-11 可审阅交付 | CHG-11 规格附录/计划/审核/README/WORKFLOW/.gitignore | V-11 unittest/smoke/Chromium/JS/validate/JSON哈希/终审GO/提交与远端核验 |

顺序：advisor开工→阻塞约定落文档→测试红→CHG6–9逐项绿→CHG10失败路径与VPS切流→完整验证→新advisor终审→修到GO→精确暂存提交推送→last-message落盘。

## Issues、Blocks、风险与完成条件

I-7（设计阶段）：旧C.3/C.6骨架范围与本轮目标冲突；通过本计划与新附录D替代，已由advisor GO_WITH_FIXES准入并修订附录D解决（resolved）。
I-8（设计阶段）：覆盖率override与现库强不变量矛盾；选择禁止bypass并说明，不把失败草稿伪装可入库，已由advisor GO_WITH_FIXES准入并修订附录D解决（resolved）。
Blocks：无open开工阻塞；六项必修约定已写入附录D及本计划。部署证据不足时停止切流并咨询advisor。
Accepted Risks：私有workspace对不合作读者无瞬时原子可见；真实OCR准确率无保证，fixture只证明协议；使用独立loopback运维入口，未开放公网导入。
Follow-ups仅为原规格范围外：Word、公众上传、多租户、学生端拆包；不可把本轮A–F移入此栏。
Plan B：解析/校验保留job并失败关闭；事务回滚旧值；切流恢复旧current与服务；若advisor切流NO_GO按用户特例报告唯一阻塞。
完成条件：Issues/Blocks均无open、DC6–11对应验证与终审GO、有实际push及线上证据。结果写completion-review与 `.codex-run/last-message.md`。

## 开工审核闭合记录

advisor `/root/opening_advisor`：GO_WITH_FIXES。主代理：GO_WITH_FIXES（六项阻塞约定已消化，可编码）。
F1 审核者所见摘要与单快照→DC7/V7；F2 revision-intent崩溃回滚→DC7/V7；F3 备份/stage/fsync/全库恢复及历史intent→DC8/V8；F4 实际完整expand及标准schema/ID竞争→DC8/V8；F5 OCR普通文件/坏输出/失败清理/配置标识→DC9/V9；F6 首次current/旧unit/运行状态恢复、独立bare mirror、API独立代码指针→DC10/V10。
事务恢复先校验全部target与备份，回滚故障保持prepared并阻止新apply；已applied终结事务不再校验目标以免把后续合法提交误判冲突。apply只用一次核准draft快照。发布与import代码指针独立，import在自身代码指针切换时协同重启API/worker；静态发布不切import指针。

## 实施证据更新

52项导入unittest、5项发布回归、完整独立进程smoke、本地Chromium、原validator与95/38/8 JS回归均通过；93份既有JSON摘要不变。初步实现提交32f3d10已普通push并部署VPS，current迁移、独立API/worker、13文件健康、gate真路径和实际失败回滚均有证据。
I-9（验收阶段，resolved）：原线上浏览器30秒预算未完成首次JSON加载；请求诊断确认约50秒全部核心请求完成、首页870点且无应用错误。advisor补充选择A，不改nginx；以单页90秒及数据就绪条件完成多页验收。现仓库统计collect DNS失败另行记录，非本轮新增错误。
最终多页验收与advisor终审、最终SHA推送/发布结果见完整验收记录。

I-10（本地完成审查，resolved）：发现手工物理mathLinks采用现有{id, reason}对象schema，而候选ID重映射误当字符串数组。先用物理草稿修订回归复现TypeError，再按对象id字段重映射，保留reason与完整validator规则；不影响正式数据。新增“全部目标已写、commit尚未持久”恢复用例，并扩展每一目标写前/写后故障覆盖。

## 终审修订闭合

I-11（resolved，DC8）：同job新增点的mappings引用需同步ID重映射；补真实并发复现与四文件新增mapping回滚测试。
I-12（resolved，DC7）：进程死亡留下atomic临时文件阻断恢复；显式恢复/worker重解析受锁保护地仅清理本模块普通临时文件，保留未知文件/符号链接拒绝，含恢复再次中断用例。
I-13（resolved，DC10）：发布恢复曾依赖网络/新ref成功；改为独占锁内先恢复，网络/非法ref失败不再阻断旧站恢复，5项发布回归通过。
I-14（resolved，DC7）：worker生成before备份可大于入站限制；分离入站大小策略与恢复结构/摘要验证，保留after 1MiB上限。
以上由新advisor独立终审核查，代码与聚焦测试已修复，最终整体验收/生产更新和GO仍需记录。
