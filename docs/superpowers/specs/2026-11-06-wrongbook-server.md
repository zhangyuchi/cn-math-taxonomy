# 服务端错题本与复习组卷 v1

- 日期：2026-11-06；Task Mode：design-build；Outcome Status：complete（设计、实现与验证闭合）；实现前设计advisor `/root/wrongbook_design_advisor` GO，独立终审advisor `/root/wrongbook_final_advisor` GO；Git交付最终SHA见本地last-message。
- 起点：`feat/import-module` 的 `105c2187faafcb92aac2713c63cd168072b16836`；交付分支 `feat/wrongbook-server` 从此派生，包含全部既有导入提交，不改写或回推原分支。
- 读者：家长/教师使用 CLI 管理私有错题；维护者运行轻量服务。维护成本由服务管理员承担。目标是跨浏览器、重启后仍保留错题，并生成错题与相关未见题混合卷。

## 证据、目标与边界

Evidence Snapshot（本日工作树已核查）：

| 证据 | 已知事实与适用范围 |
| --- | --- |
| `assets/blindspots.js` | `cnmBlind.local.v1` 只保存知识点汇总，不含逐题 ID，不能冒充历史错题 |
| `assets/children.js`、`children/` | 档案和最近诊断摘要均在 localStorage；本机 childId 可复用，但不是鉴权凭证 |
| `diagnose/engine.js:applyAnswer`、`diagnose/app.js:exportJson` | 导出 `tool=cn-math-taxonomy/diagnose, version=2`，answers 含 questionId/topicId/result/userAnswer/order；没有逐题答错时间或 subject 字段；generatedAt 只是导出时间 |
| `import_module/api.py`、`deploy/README.md`、`WORKFLOW.md` | Python 标准库、loopback 独立 API、工作区不在静态根；qqvps 约 2 GiB，不运行重 OCR |
| `data/questions.json`、`topics.json`、`dependencies.json` | 13335 题、3297 点、4868 边，约 12.5 MiB JSON；题图可为 inline SVG；依赖方向 topicId → prerequisiteId |
| `deploy/publish.py` | 显式排除后端与运行目录；新增服务须加入排除列表 |

成功：规格审查 GO；API 记录/查询/状态/删除；服务进程重启仍在；错题优先+未见题且无重复的持久卷面；CLI 可打印/提交再测；自测、终审 GO、普通 push、last-message。
非目标：本次不部署/切流生产，不添加公网账号注册、不自动上传静态页数据、不修改正式 JSON、不运行 OCR/LLM、不承诺排版等同出版 PDF。用户无需人工验收。
未知/假设：生产磁盘持久性和备份排程尚未现场核验，交付运行配置与可执行备份工具；本次以真实本机进程/磁盘演练证明功能。正式库的引用题目必须存在且学科/知识点匹配；服务启动载入只读目录快照，更新题库需要重启。

## 概念与决策

- 用户：离线管理 CLI 创建 userId，随机至少 256 bit token；SQLite 只存 token SHA-256，服务从 Bearer token 推导 userId，绝不采信客户端 userId。token 不复用 IMPORT_TOKEN。创建时写独立 0600 token 文件且不覆盖已有文件，不输出 token。
- 孩子：`(userId, childId)` 命名空间，childId 可用现有档案 ID；无需上传姓名、学校。不同用户相同 childId 隔离。本机“切换/移动/删除档案”不自动变动服务端数据，CLI 显式选 child。
- 作答事件：eventId（客户端稳定幂等键）、questionId、topicId、subject、answeredAt（RFC3339 时区）、sourceSession、result（correct/wrong/skip）、可选 userAnswer、timeSource（client/export）、server receivedAt。事件在 `(user,child,eventId)` 唯一。同键同内容重放成功；不同内容 409；不以 session+question 唯一，允许同题多次作答。
- 错题：result=wrong/skip 的事件；skip 不宣称实际答错。每条事件有 mastered 状态。PATCH 按 questionId 将本孩子该题所有错题事件标记/取消掌握，之后新错题重新未掌握。correct 记录用于“已见题”集合，不自动清除错题。
- 试卷：持久 paperId、创建时间、题库摘要、策略、student（无参考答案且遵循 hideChoiceText 的题目数组）、answerKey、缺额统计。保存题目快照，题库以后更新也可读取原卷。纸面及再测均基于固定 questionId。旧卷始终可读/打印；再测回录须题ID在当前目录且知识点/学科归属仍匹配，否则400，不承诺跨版本缺失题回录。

| Decision | 方案比较与结论 | 重评条件 |
| --- | --- | --- |
| DEC-1 | SQLite 标准库事务/索引/在线备份，优于追加 JSON 的并发/恢复成本；无需新数据库服务 | 多进程高并发或百万级记录 |
| DEC-2 | 独立 `wrongbook/` + CLI + loopback 8766，沿用导入服务隔离模式；不接入原静态页的 token，避免 SW/跨源密钥风险 | 增加独立 TLS 用户端 |
| DEC-3 | 导入诊断逐题 JSON；拒绝将知识点汇总伪造为原题；本机盲区一次迁移延期 | 提供原题信息后 |
| DEC-4 | 只选已在正式库且可独立呈现的题，包含必要题图与材料；印刷 HTML 转义所有文本，SVG 以 base64 image 隔离 | 新题型/外部资源引入 |

## 逻辑链与架构判断

| LC | 因为 | 所以 |
| --- | --- | --- |
| LC-1 | 浏览器账本会丢失且不跨设备 | DC-1 服务端事务落盘并提供备份 |
| LC-2 | childId 可猜且本机无账户 | DC-2 token 确定用户，每条查询/修改必须同时绑定 user/child |
| LC-3 | 汇总没有原题，诊断 JSON 有逐题数据 | DC-3 走明确的诊断导入/逐题录入，事务且幂等 |
| LC-4 | 复习需要旧错题和相关未见题 | DC-4 错题优先、同点及直接先修题、严格去重与缺额说明 |
| LC-5 | 上游静态页频繁变化，VPS 资源有限 | DC-5 独立轻量目录、只读题库、原回归与资源限制 |

默认架构：主线为 authenticated event → SQLite → fixed paper；不引入任务队列、前端框架、通用身份平台。输入验证是防错题归属和防越权所需，不扩充无关内容审核。输出快照使打印与再测不会随题库更新漂移。
高级检查选择 AA-RUNTIME、AA-DELIVERY：单进程顺序 HTTPServer，10秒连接超时，SQLite busy timeout=5秒、journal DELETE + synchronous FULL，每次请求独立连接/事务；限制单请求 2 MiB、批量500事件、组卷1–100题、列表1–200且分页，不在内存累积全部历史。Python JSON 题库一次载入，目标常驻 <128 MiB，systemd MemoryMax=192M/CPUQuota=50%；本机实测 RSS。失败恢复由 SQLite 事务完成，不自写 WAL 备份逻辑。

## 数据与运行所有权

`WRONGBOOK_WORKSPACE` 必须显式绝对路径，默认不推导到代码树；拒绝项目根内、配置静态根内，以及已知 `/srv/cn-math-taxonomy/current`、`releases` 内（解析 symlink 后及词法路径均检查）。整个目录私有 0700，sqlite/token/备份 0600，umask077；禁止 HTTP 静态服务。部署工作区 `/srv/cn-math-taxonomy/wrongbook-workspace`；只读代码可在独立 wrongbook-current，和 import-current/workspace 分离。
SQLite schemaVersion=1，users、attempts、papers 三表，user/child/result/time 及 question 索引；写入事务提交成功才返回2xx；出错整体回滚。不自动清理历史，DELETE 是用户显式删除该孩子指定题的所有作答事件，不删除既有卷面快照；历史卷可能含已删除题，文档明确该行为。无隐式schema迁移；新于支持版本则拒绝启动，回滚先用匹配代码和备份。
备份：本地管理命令用 SQLite backup API 生成完整一致数据库，含 token 哈希和 papers；备份目标必须私有路径且不在静态根，不能是源 DB/覆盖已有文件。每日执行，至少7份，异机加密副本由运维保管；工具不假称已经有生产定时任务。恢复先停服务、保留当前库、以0600备份替换 sqlite、重启并查询；本次实际演练备份与恢复。任何 token 明文单独保管，恢复 DB 后原 token 仍有效。

## API 契约（全部需 Authorization: Bearer；无 cookie/CORS）

基础路径 `/wrongbook/v1/children/{childId}`；标识符仅 `[A-Za-z0-9_-]{1,128}`（sourceSession 允许普通字符串至256字符）。未认证优先401；他人命名空间资源与不存在资源都404；无用户全局枚举端点。错误 `{code,error}`；输入400、鉴权401、不存在404、幂等冲突409、无错题或无可用原题422、数据库忙503、内部错误500且不泄露原文/路径/token。

| 方法/路径 | 请求与结果 |
| --- | --- |
| GET `/wrongbook/v1/health` | 鉴权后 `{ok:true}`，运维健康 |
| POST `.../attempts` | `{attempts:[事件...]}`，全批验证后同一事务，200 `{inserted,replayed}` |
| GET `.../attempts?status=active\|mastered\|all&subject=math&limit=100&offset=0` | 默认 active，仅列错题事件，all 仍只列 wrong/skip；`{items,total,limit,offset}`；有稳定时间+eventId排序；offset为0..2^63-1整数，与SQLite参数范围一致 |
| PATCH `.../questions/{questionId}` | `{mastered:true/false}`，不存在错题404 |
| DELETE `.../questions/{questionId}` | 删除该题所有事件，未找到404；显式删除允许之后同eventId重新录入 |
| POST `.../diagnoses` | `{sourceSession,subject,diagnosis:<导出对象>}`；严格tool/version，500条以内；eventId由来源session+order派生，answeredAt=generatedAt，timeSource=export；全部作答入库，不采信导出题干/参考答案；仅导入通道的同语义重放忽略 export answeredAt 变化并保留首次时间；其他字段有变化仍409，不隐式覆盖更正结果 |
| POST `.../papers` | `{subject,count:20,wrongCount:10}`（默认 wrongCount=ceil(count/2)，1..count）；持久返回 `{paperId,student,answerKey,stats,...}` |
| GET `.../papers/{paperId}` | 限user/child读取已存快照，可重复打印/再测 |

新录入和相同事件重放均先按当前题库校验；原题删除/改归属后旧事件仍可列出，但再次录入或重放返回400，不绕过当前目录。

请求字段按明确白名单校验；subject 为十学科固定枚举，通过 topicId 前缀并结合题库核对；questionId、topicId 必须在目录匹配。answeredAt 验证带时区且规范到 UTC。连接日志只记 method，不记路径、请求体/异常内容。非同源 Origin 写请求拒绝；Bearer CLI 无 Origin 可调用，无 token GET 也401。服务固定127.0.0.1，远程 SSH -L18766:127.0.0.1:8766，未来公网必须另行评审 TLS origin，不把 API 交给站点SW。

## 组卷策略及可再测输出

1. 本用户/孩子/学科未掌握 wrong/skip，按最近 answeredAt 优先，questionId 去重；选最多 wrongCount，来源=wrong。
2. 从**所有当前未掌握错题**提取知识点，候选先同点，再直接先修（不跨学科）；未见题指该孩子数据库内从未出现任何作答事件的 questionId。与已选去重。确定性按 topicId/questionId 排序，补至count。无错题422；全部原错题在当前库缺失/改归属/不可独立呈现时422，不静默生成纯新题卷，不以随机无关题替代。
3. 相关未见题不足时，用剩余未掌握错题补齐（仍不重复）；实际仍不足返回短卷，stats 明确 requested/actual/wrong/new/shortfall、missingQuestionIds，不伪造新题。题库更新后丢失原题不从客户端注入题干。
4. student题含顺序、questionId/topicId/subject/type/prompt/choices/hideChoiceText/必要材料/图与source，排除answer/explanation/verify；answerKey单独存放。CLI `paper` 保存JSON并可生成只含student的A4 HTML；打印必须执行 hideChoiceText=true 时隐藏带图选项文本、保留选项键与图，避免选项名称泄题。HTML不展示孩子身份、token、作答原文。数学使用原题文本，公式保留，不依赖公网排版库。再测用新 sourceSession+eventId 通过 attempts 记录，答案判定由家长或现有诊断规则完成，不新增不同的自动判题器。

## 实现、验证与追踪

| DC | 承诺 | CHG 落点 | 验证 |
| --- | --- | --- | --- |
| DC-1 | 私有磁盘持久、事务、重启和备份恢复 | CHG-1 `wrongbook/store.py`、`common.py`、CLI backup/provision | V-1 服务重启、权限、事务冲突整批回滚、备份恢复 |
| DC-2 | 用户和孩子隔离、鉴权、资源边界 | CHG-2 `wrongbook/api.py`、systemd示例 | V-2 两token同child、防越权GET/PATCH/DELETE/paper、无token、错误输入/体积/Origin |
| DC-3 | 逐题录入和现有诊断同步且幂等 | CHG-1/2 + CLI diagnoses | V-3 真实导出格式、correct/skip、多次同题、重放/冲突、同诊断重导出时间变化保留首次时间、不伪造时间 |
| DC-4 | 混合去重组卷、独立学生卷、可再测 | CHG-3 `wrongbook/paper.py`、CLI | V-4 错题优先/同点/先修/已见排除/缺额/全错题缺失422/快照/隐藏选项文本及图/打印转义/再测事件及缺失题拒绝 |
| DC-5 | 静态与正式库保持、轻量部署 | CHG-4 发布排除、说明/示例 | V-5 原validator和JS回归、发布测试、正式JSON摘要不变、真实全库RSS/CLI演示 |

顺序：advisor审设计GO → 各CHG测试先红再实现 → 真实进程录入/重启/备份/组卷/再测演示 → 回归 → advisor独立终审GO → 精确路径提交推送 → `.codex-run/last-message.md`。

Issues List：当前无已知设计 issue（advisor发现后在此记录实际闭合）。Blocks List：无；设计准入与独立终审均已GO。
Follow-ups：自动浏览器同步/本机汇总迁移、独立用户UI、公网多设备TLS入口；CLI已满足v1管理与同步入口，因此不阻塞当前目标。
Accepted Risks：v1需运维创建/分发token并用SSH访问；导出时间代替历史逐题时间且明示来源；删除事件不回写历史卷；完整公式精排延期但原文/图保留。单机磁盘灾难依赖异机备份，工具与恢复演练可验证而生产排程不在本次部署范围。
Plan B：停止wrongbook独立服务不影响静态诊断/导入；恢复数据库备份和匹配版本代码。
最终追踪：用户目标 → 本日证据 → LC-1..5 → DEC-1..4 → DC-1..5 → CHG-1..4 → V-1..5。各项已实现并验证，最终追踪见下文与审核记录。

## 设计初审闭合

ISSUE-D1 resolved（advisor）：cnm_p014_q1 等选项文本含形状名称，打印遵循 hideChoiceText，V-4验证保留图且隐藏文本。
ISSUE-D2 resolved（advisor）：重复导出 generatedAt 变化；仅diagnoses通道同语义重放保留首次时间，V-3验证；直接attempts保持严格幂等。
ISSUE-D3 resolved（advisor）：旧卷读取与当前题库录入边界分开，缺失/改归属拒绝回录，全部错题不可用422，V-4验证。无open issue/block。

## 实现验证进度

V-1..4 当前19项聚焦测试通过，含真实进程SIGKILL重启/备份恢复及再测。V-5原validator、JS95/38/8、5项发布测试、93份正式JSON摘要及Chromium打印通过；终审修复后全Python103项通过（158.208秒）；独立19项复核通过，正式终审GO，Outcome为complete。实际演示RSS63356KiB。证据及适用边界见[审核记录](../reviews/2026-11-06-wrongbook-server-review.md)。

## 终审修复记录

ISSUE-F1 resolved：终审复现部分原错题失效后其知识点被排除，已将topics收集移动到当前原题可用性判定之前；新增部分原题缺失仍补同点新题回归，保留全失效422（DC-4/CHG-3/V-4）。
ISSUE-F2 resolved：CLI卷面输出补传WRONGBOOK_STATIC_ROOTS，JSON/HTML均拒绝声明静态根（DC-2/CHG-2/V-2）。
旧事件原题失效后的同内容重放400已明确并增加回归（DC-3/V-3），保持先目录校验契约。
Follow-up FU-2：孤立Unicode surrogate可触发500，但已独立确认事务回滚且不落非法事件；后续增强非互通Unicode输入提示，不影响本轮正常中文作答/隔离/持久化目标。

## 完成判定

无open issue、无open block。DC-1/CHG-1/V-1：SQLite事务、SIGKILL重启、在线备份和恢复验证通过；DC-2/CHG-2/V-2：身份/孩子隔离、输入/Origin/文件边界及锁重试通过；DC-3/CHG-1/2/V-3：诊断同步、幂等/时间来源及旧题回录边界通过；DC-4/CHG-3/V-4：混合去重/短卷/部分缺题/持久快照/图与隐藏选项/打印/再测通过；DC-5/CHG-4/V-5：正式JSON、原回归、发布排除与资源演示通过。FU-2为已记录非阻塞项。生产部署与备份排程不在本次范围内。
