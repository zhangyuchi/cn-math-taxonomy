# 导入模块设计规格（教材 + 卷子）

- **日期**：2026-11-03（Asia/Shanghai）
- **仓库**：`cn-math-taxonomy`（qqvps-cd：`/home/ubuntu/Projects/cn-math-taxonomy`）
- **性质**：设计文档；本会话**只设计、不实现**
- **状态**：需求与架构选择已锁定，可据此开实现计划

---

## 1. 背景与目标 / 非目标

### 1.1 背景

当前仓库扩库路径偏开发者：

1. **版本对照（不扩库）**：按 `docs/edition-mappings.md` 写 `data/mappings/<edition>.json`，校验后合并进 topic 的 `mappings`。
2. **新章节 + 新习题（扩库）**：公开目录对齐 → 各学科 DSL / `data/_qbatches` 草稿 → 对应 `*_build.py` → `data/*.json` → `scripts/validate.py` / `scripts/verify_questions.py`。

没有「上传教材目录或卷子 → 统一入库」的运维入口。老师或运维拿着 PDF/Word/文本目录、试卷，无法在不手改巨型 JSON 的情况下安全扩库。正式库（`topics.json` / `dependencies.json` / `questions.json` / mappings）体积大、校验严，静默写入风险高。

### 1.2 目标

第一版交付**服务化导入模块**（路径 C），覆盖现有全部学科，做到：

1. **导入教材**：解析一册目录（TOC）→ 生成 topic / 依赖 / 课标建议草稿；或仅生成版本对照（mappings 支路）。
2. **导入卷子**：解析结构化文本卷 → 切题 → 建议 `topicId` → 生成 `_qbatches` 风格题目草稿。
3. **统一 CLI 管道**：运维在服务器上跑命令；按知识点 ID 前缀分发到现有 `validate.py` / `verify_questions.py`。
4. **人工门闸**：新建知识点与新题必须审核通过后才 merge；mappings 在校验通过后可自动 apply。
5. **审核台**：gate 后的网页只展示 job 结果、diff、通过/驳回；**不在网页上传解析**（v1）。
6. **落盘隔离**：所有未入库产物写在 `data/imports/<jobId>/`，正式库仍只认现有 `data/*.json`。

### 1.3 非目标（本规格明确不做）

- 不在本会话实现任何代码、脚本、UI 或改 `data/*.json`。
- 不把整本教材 PDF 正文 OCR 进题库或知识点正文（v1 仅 TOC 文本；全书 OCR 永久不在默认路径）。
- 不做自动教师权威背书：导入内容默认视为**待审 AI/机器草稿**，与现有题库「无教师审核」声明一致并强化门闸。
- 不拆分 `questions.json` 加载策略（可作为后续优化，见第 10 节）。
- 不替代各学科既有 `*_build.py` 的历史批次重编流程；导入模块与 build 脚本并存，正式 merge 后仍可用现有 validate 守门。
- 不在 v1 做公网开放上传、多租户、或面向学生的录题页。

---

## 2. 已锁定决策摘要

| # | 议题 | 决定 |
|---|---|---|
| D1 | 主入口 | 开发/运维在服务器跑导入命令；网页只展示结果（审核台） |
| D2 | 教材 v1 | 解析教材目录 → 新建 topic / 依赖 / 课标草稿 → 人工确认后进正式库 |
| D3 | 卷子 v1 | CLI 预留文本与 PDF；第一版打通文本/MD/CSV；OCR 后置为 worker 插件 |
| D4 | 合并门闸 | mappings 可自动 apply；**新建知识点 / 新题必须人工确认** |
| D5 | 学科范围 | 一套 CLI 管道覆盖现有全部学科，按 ID 前缀分发到现有 validate |
| D6 | 架构路径 | **C**：第一版即任务队列 + 审核台的服务化 |
| D7 | 组件 | `import-api`、`import-worker`、`import-cli`、`review-ui`、`apply` |
| D8 | 作业落盘 | `data/imports/<jobId>/` |
| D9 | 本会话 | **仅设计**，不写实现 |

ID 前缀（与现库一致，导入时按此前缀选学科校验与续号策略）：

| 前缀 | 学科 |
|---|---|
| `cnm` | 数学 |
| `cne` | 英语 |
| `cnc` | 语文 |
| `cnp` | 物理 |
| `cns` | 科学 |
| `cnh` | 历史 |
| `cnd` | 道德与法治 |
| `cng` | 地理 |
| `cnb` | 生物 |
| `cnk` | 化学 |

人教版目录仍是 topic 本体的 `grade` / `semester` / `chapter`；其他版本只写 mappings（见 `docs/edition-mappings.md`）。

---

## 3. 架构

### 3.1 在现有站点中的位置

现有交付形态是**静态站**（图谱 / 诊断 / PWA）+ 运维脚本（`validate.py`、`verify_questions.py`、各科 `*_build.py`、`deploy-cf.sh`）+ 可选 gate（运维后台）。导入模块作为**并列的轻量服务**部署在同一主机（qqvps-cd），不塞进浏览器 SW 预缓存路径：

```
[运维] import-cli ──HTTP──► import-api ──入队──► 队列
                              │                    │
                              │              import-worker
                              │                    │
                              │         写 data/imports/<jobId>/
                              │         调 validate / verify
                              ▼
                         状态机更新
                              │
[审核者] review-ui (gate 后) ──► API: list/diff/approve/reject
                              │
                         apply（仅 approved）
                              ▼
                    合并进 data/topics.json 等
                              │
                    可选触发 deploy-cf / 静态刷新
```

约束：

- 静态前端（`explore/`、`diagnose/`）**v1 不读** `data/imports/`。
- `sw.js` **不得**预缓存 `data/imports/**`。
- 正式运行时数据源仍是现有 `data/topics.json`、`dependencies.json`、`questions.json`、`cn-curriculum-standards.json`、`data/mappings/*.json`。
- import 服务与静态文件可由同一 nginx/caddy 反代：例如 `/import-api/` → API，`/import-review/` → 审核台静态资源（需 gate）。

### 3.2 组件职责

| 组件 | 职责 | 不负责 |
|---|---|---|
| **import-api** | 创建 job、接收上传、查询状态、审核通过/驳回、触发 apply；鉴权（运维 token / gate session） | 重解析、OCR |
| **import-worker** | 消费队列；教材 TOC / 文本卷解析；写草稿与报告；调用 validate/verify；可选 OCR 插件 | 直接改正式 JSON |
| **import-cli** | 运维入口：`textbook` / `paper` / `status` / `approve` / `reject` / `apply` / `list`；内部只打 API | 本地静默改库（禁止旁路 API） |
| **review-ui** | gate 后列表、源文件摘要、draft diff、validate/verify 报告、通过/驳回按钮 | 上传大文件、跑 OCR、改 schema |
| **apply** | 将 `approved` job 原子合并进正式库；mappings 可在策略允许时自动进入此步；合并前再跑全量 validate | 跳过审核台对「新建点/题」的确认 |

实现选型（规格层固定选择，避免 TBD）：

- **队列**：本机 Redis 列表或目录队列（`data/imports/_queue/`）；v1 单 worker 进程即可，不做多机分布式。
- **API**：同机 HTTP（建议 FastAPI 或等价），仅绑定内网 / gate 后路径。
- **进程管理**：systemd 单元 `import-api`、`import-worker`；与静态站生命周期独立。
- **鉴权**：复用现有 gate；CLI 使用长期运维 token（环境变量 `IMPORT_TOKEN`），不把 token 写入仓库。

### 3.3 磁盘布局

```
data/imports/
  _queue/                          # 可选：文件队列时的待处理指针
  <jobId>/
    meta.json                      # 类型、学科、状态、时间、操作者、策略标志
    source/                        # 原始上传（只增不改）
      original.ext
      normalized.md                # 规范化后的文本（若有）
    draft/
      topics.json                  # 仅本 job 新增/修改候选
      dependencies.json
      questions.json
      mappings.json                # 符合 mappings 文件 schema 的草稿（或补丁）
      standards_suggestions.json   # 课标挂靠建议（inferred），非直接写正式 standards 全文
    reports/
      validate.json
      verify.json
      parse.json                   # 切章/切题统计与告警
      coverage.json                # 知识点题量/难度覆盖缺口
    review/
      decision.json                # approve/reject 记录、评论、操作者
```

`jobId` 格式：`YYYYMMDD-HHMMSS-<shortuuid>`（Asia/Shanghai 本地时墙钟），保证字典序大致时间序。

`meta.json` 必备字段：

```json
{
  "jobId": "20261103-163000-ab12",
  "type": "textbook|paper",
  "mode": "expand|mappings_only",
  "subjectPrefix": "cnm",
  "edition": "renjiao_math|hujiao_math|…|null",
  "status": "queued",
  "createdAt": "2026-11-03T16:30:00+08:00",
  "updatedAt": "2026-11-03T16:30:00+08:00",
  "createdBy": "ops",
  "flags": {
    "autoApplyMappings": true,
    "requiresHumanReview": true
  },
  "sourceFiles": ["source/original.md"],
  "error": null
}
```

规则：

- `data/imports/` 可进 gitignore 或仅提交示例 job；**默认不把真实试卷原文提交到公开仓库**（版权）。
- apply 成功后 job 保留只读快照；不删除 source（审计）。
- 正式库合并采用「读入 → 内存合并 → validate → 写临时文件 → `os.replace`」的原子替换；失败则正式文件不变，job 退回 `approved` 并写错误。

---

## 4. 教材流水线（textbook）

### 4.1 输入

v1 接受：

- Markdown / 纯文本 TOC
- CSV（列：`grade,term,unit,chapter,topic_name,optional_notes`）

PDF：CLI 参数可指向 PDF，但 v1 worker **若无 OCR 插件则拒绝并提示**「请先导出目录文本，或待 v1.1 OCR」；不半解析二进制糊弄。

### 4.2 模式

| mode | 条件 | 产出 | 门闸 |
|---|---|---|---|
| `expand` | 人教（或「作为本体目录」的主版本）新章/新课 | `draft/topics.json` + `dependencies.json` + `standards_suggestions.json` | **必须**人工审核 |
| `mappings_only` | 非人教版本对照已有 topic | `draft/mappings.json`（`data/mappings/<edition>.json` 形状） | validate 通过后**可自动** `approved → applied` |

CLI 必须显式传 `--mode expand|mappings_only`；不得凭文件名猜测后静默选 expand。

### 4.3 expand 处理步骤

1. **识别元数据**：学科前缀、年级、学期、册次标签；与用户 CLI 参数交叉校验，冲突则 `failed`。
2. **解析 TOC**：章 → 节 → 课时级标题列表；过滤序言/附录噪声（可配置正则，默认丢弃「编者的话」「目录」自身等）。
3. **建议 ID**：在该前缀现有最大序号后续号（小学数学常用 `cnm_pNNN`，初中 `cnm_NNN`，高中 `cnm_hNNN` 等——**以现库该学段实际编号规则为准**，由配置表声明，不在 worker 内硬编码一套与库冲突的规则）。
4. **生成 topic 草稿**（每个新点），字段对齐现库：
   - 必填：`id`, `name`, `grade`, `semester`, `chapter`, `domain`, `type`, `description`, `mastery_criteria`, `diagnostic_question`, `standards`（可先空数组）, `external`（false）
   - 占位策略：`description` / `mastery_criteria` / `diagnostic_question` 写明「导入草稿，待审」结构化占位，**禁止**空字符串；`diagnostic_question` 需具备现 schema 要求的子字段形状。
5. **依赖草稿**：同册 TOC 顺序生成相邻 `soft` 边（`strength: "soft"`），`reason` 标注「目录顺序推断，待审」；**不自动生成 hard 边**。
6. **课标建议**：写入 `standards_suggestions.json`，每条 `{topicId, suggestedStandardIds[], confidence: "inferred", note}`；**不直接改** `cn-curriculum-standards.json` 正文。审核通过后 apply 仅把已确认的 `standards` id 列表写入对应 topic（id 必须已存在于正式课标库）。
7. **校验**：对「正式库 ∪ 本 job 草稿」做增量可检验子集，调用现有 `validate.py` 逻辑（或等价导入其检查函数）；报告写入 `reports/validate.json`。
8. **状态**：解析/结构失败 → `failed`；否则 → `needs_review`。

### 4.4 mappings_only 处理步骤

1. 解析对照表或 TOC+人工列的 `topicId` 映射。
2. 生成符合 `docs/edition-mappings.md` 的文件：`edition`, `label`, `status: "draft"`, `source`, `entries[]`。
3. 拒绝：`renjiao_math` / `pep_math` 作为 edition；空 `mappings`；未知 `topicId`；学科与文件名后缀不一致（`*_math` 只能对数学点等）。
4. validate 通过且 `flags.autoApplyMappings=true`（默认 true）→ 直接标 `approved` 并调用 apply，将 entries 合并进正式 mappings 文件（及/或 topic.mappings，与现贡献指南一致：**贡献文件为 `data/mappings/<edition>.json`，由维护流程合并进 topics**）。v1 apply 对 mappings 的行为固定为：更新/创建 `data/mappings/<edition>.json`，**不**批量改写巨型 `topics.json` 内嵌 mappings（与当前贡献指南「独立小文件」一致）；前端未读 mappings 前这已足够。
5. 若 validate 失败 → `needs_review`（不自动丢弃），人工可驳回或修正源后重跑。

### 4.5 教材流水线显式不做

- 不从 TOC 自动写完整掌握标准与高质量诊断题（只占位）。
- 不跨学科自动连边（物理 `mathLinks` 不在 textbook v1 自动填充）。
- 不因为 mappings 缺失而隐藏知识点（与 edition-mappings 文档一致，属前端日后行为，非本模块）。

---

## 5. 卷子流水线（paper）

### 5.1 输入

v1：

- Markdown（推荐）：题号标题 + 题干 + 答案/解析区块
- CSV：`prompt,answer,type,difficulty,topicId?,choices?,explanation?,verify?`

PDF：参数允许，v1 无 OCR 插件时 `failed` 并指示改用文本；v1.1 启用 OCR 插件产出 `source/normalized.md` 再走同一文本管道。

### 5.2 推荐文本卷约定（v1 规范格式）

```markdown
## Q1
topicId: cnm_221
difficulty: standard
type: numeric
prompt: …
answer: …
explanation: …
verify: …
```

选择题用：

```markdown
choices:
- A: …
- B: …
answer: B
```

无法识别的块进入 `reports/parse.json` 的 `unparsed[]`，整单仍可进入 `needs_review`（除非 0 题成功解析 → `failed`）。

### 5.3 处理步骤

1. **切题**：按 `## Qn` / CSV 行切分。
2. **规范化**：映射到 questions schema（见第 6 节）；生成临时 id：`{topicId}_q{draftN}` 或尚未挂点时用 `_import_{jobId}_{n}`，apply 前再分配正式 `{topicId}_q{N}`（N 为该 topic 现有最大题号 +1）。
3. **建议 topicId**：
   - 若源中已给且存在于正式库（或同 job 教材草稿台账）→ 采用并标 `match: explicit`。
   - 否则按关键词 / 章节标题对正式 `topics.json` 做检索建议，写入每题 `suggestedTopicIds[]`（最多 5）；**不自动选定**唯一挂点时题目标 `topicId: null`，状态依赖审核台指定。
4. **verify**：对带 `verify` 的 numeric/choice 跑 `verify_questions.py` 等价检查；`mismatch` 写入报告，**不阻断**进入审核（标题级警告）。
5. **覆盖率**：按 topic 聚合；若某 topic 在「正式库现有题 ∪ 本 job 将新增题」下仍不满足「≥3 题且含 basic 与 standard」，在 `reports/coverage.json` 标 `needs_more`；允许提交审核，但 apply 默认拒绝该 topic 的不完整合并，除非审核决定里显式 `overrideCoverage: true`（高风险，UI 红字确认）。
6. **落盘**：`draft/questions.json` 使用与 `data/_qbatches/*.json` 相同的数组形状，便于日后手工改批或与旧 build 流程互通。
7. **状态**：0 题成功 → `failed`；否则 → `needs_review`（新题一律人工）。

### 5.4 卷子流水线显式不做

- v1 不把 Word 二进制当一等公民（运维先转 MD/CSV）。
- 不静默覆盖正式 `questions.json` 已有 id。
- 不做听力/口语媒体题（与英语库现状一致）。

---

## 6. Schema 约束（必须遵守的不变量）

导入草稿在 apply 瞬间必须使「合并后的正式库」通过现有脚本；下列不变量从 `docs/questions.md`、`docs/edition-mappings.md`、`scripts/validate.py`、`scripts/verify_questions.py` 归纳，**worker/apply 不得放宽**。

### 6.1 Topics

- `id` 全局唯一；禁止与 external 节点冲突；禁止臆造未登记前缀。
- 人教路径写在 `grade` / `semester` / `chapter`；`mappings` 内不得出现 `renjiao_math` / `pep_math`。
- `external: true` 的节点不挂诊断题库扩容对象。
- 新 topic 若带 `standards`，每个 id 必须存在于 `data/cn-curriculum-standards.json`。

### 6.2 Dependencies

- 每条：`topicId`, `prerequisiteId`, `strength`（`hard`|`soft`）, `reason`（非空）。
- 两端 id 必须存在于「正式 topics ∪ 本 job 将 apply 的 topics」。
- 教材自动边仅为 `soft`；hard 边只允许人工在审核台勾选提升或驳回后手改草稿再准。

### 6.3 Questions

- `id` = `<topicId>_q<N>`；唯一；与 `topicId` 前缀一致。
- `topicId` 必须是正式（apply 后）非 external 知识点。
- `difficulty` ∈ {`basic`,`standard`,`advanced`}；`type` ∈ {`numeric`,`choice`,`short`,`self`}。
- `prompt` / `answer` / `explanation` 非空；无未知字段。
- `choice`：3–4 选项，key 依次 A–D，文字不重复，`answer` 为其中某一 key。
- `numeric` 的 `answer` 可解析为数（含分数、负数、全角逗号多值）；多值无序需 `unordered: true`。
- 每个正式知识点：**≥ 3 题**，且必须含 **basic** 与 **standard**。
- 全库 numeric+choice 占比等统计项仍由全量 `validate.py` 把关。
- 若存在 `verify`，apply 前该题不得处于 `mismatch`（审核可删 verify 或改正答后再批；override 不允许带着 mismatch apply）。

### 6.4 Mappings 文件

- 文件名与 `edition` 一致；`status` ∈ {`example`,`draft`,`reviewed`}；`draft`/`reviewed` 必有 `source`。
- `entries[].topicId` 已存在、文件内不重复；学科后缀与 topic 前缀匹配。
- `grade` 整数；`term` ∈ {`上`,`下`,`全`}；`unit`/`chapter` 若出现则非空。

### 6.5 校验入口

- 结构与图谱：`python3 scripts/validate.py`
- 题目核验：`python3 scripts/verify_questions.py`（及报告 `data/verification_report.json` 的生成方式不污染为导入失败条件，导入自有 `reports/verify.json`）

---

## 7. API 与 CLI 表面（草图）

### 7.1 HTTP API（均需鉴权）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/import/v1/jobs/textbook` | multipart: 源文件 + `mode` + `subjectPrefix` + 元数据 |
| `POST` | `/import/v1/jobs/paper` | multipart: 源文件 + `subjectPrefix` + 可选默认 grade |
| `GET` | `/import/v1/jobs` | 列表；查询参数 `status`,`type`,`limit` |
| `GET` | `/import/v1/jobs/{jobId}` | meta + 报告摘要 |
| `GET` | `/import/v1/jobs/{jobId}/diff` | 相对正式库的新增/修改摘要 |
| `GET` | `/import/v1/jobs/{jobId}/files/{path}` | 受限读取 job 内文件（防 `..`） |
| `POST` | `/import/v1/jobs/{jobId}/approve` | body: 评论、覆盖率 override、题-挂点最终指定 |
| `POST` | `/import/v1/jobs/{jobId}/reject` | body: 原因 |
| `POST` | `/import/v1/jobs/{jobId}/apply` | 仅 `approved`；幂等 |
| `POST` | `/import/v1/jobs/{jobId}/requeue` | 仅 `failed` 或已修源后的 `rejected` 重跑解析 |

错误响应：统一 `{ "error": "...", "code": "..." }`；校验类附 `reports` 路径。

### 7.2 CLI

```text
import-cli textbook --mode expand|mappings_only --prefix cnm \
  --file ./toc.md [--edition hujiao_math] [--no-auto-apply-mappings]

import-cli paper --prefix cnm --file ./exam.md

import-cli list [--status needs_review]
import-cli status <jobId>
import-cli approve <jobId> [--comment ...] [--override-coverage]
import-cli reject <jobId> --reason ...
import-cli apply <jobId>
import-cli requeue <jobId>
```

CLI 只把参数打到 API；退出码：0 成功，2 业务拒绝（校验/状态），1 系统错误。

---

## 8. 审核台（review-ui）

### 8.1 位置与权限

- 挂在 gate 后（与运维后台同级），路径例如 `/import-review/`。
- 只读解析逻辑；所有写操作走 API。
- v1 **不提供**浏览器上传入口（上传仅 CLI → API），避免未鉴权大文件与旁路路径；若 gate 用户需要上传，第二期再开，且仍创建同一 job 模型。

### 8.2 界面信息架构

1. **Job 列表**：类型、学科、状态、创建时间（Asia/Shanghai）、创建者、告警计数（mismatch / needs_more / unparsed）。
2. **Job 详情**：
   - 源文件预览（文本；PDF 仅元数据 + OCR 文本若有）
   - 草稿摘要：新增 topic 数、边数、题数、mappings 条数
   - Diff：id 列表 + 关键字段前后对比
   - 报告：validate / verify / parse / coverage
3. **操作**：
   - 通过：可编辑「题 → topicId」最终映射表；确认 soft 边去留；课标建议勾选
   - 驳回：必填原因
   - 对 `approved` 显示「应用到正式库」按钮（也可仅 CLI apply）
4. **展示约束**：红字提示「内容可能为 AI/机器生成，无教师背书」；展示现有 LICENSE/非商业声明不在此重复法律意见，但必须有待审警告。

---

## 9. 状态机与错误处理

### 9.1 状态

```
queued → running → needs_review → approved → applied
                 ↘ failed
needs_review → rejected
failed|rejected → requeue → queued
```

旁路说明：

- **mappings_only** 且 validate 通过且 `autoApplyMappings`：允许 `running → approved → applied`（跳过人工，但仍写 `review/decision.json` 为 `system:auto-mappings`）。
- **expand / paper**：禁止跳过 `needs_review`。
- **apply 前**：必须再跑一次全量（或「正式∪本 job」）validate；失败则保持/回到 `approved`，正式库不变，`reports/validate.json` 更新为 apply 阶段失败原因。
- **applied**：只读；禁止再次 approve；如需修正走新 job。

### 9.2 错误策略

| 场景 | 行为 |
|---|---|
| 源文件无法解析 / 0 题 / TOC 空 | `failed`，保留 source |
| validate 结构错误 | 进入 `needs_review` + 报告（不自动丢弃） |
| verify mismatch | 报告警告，仍可 `needs_review`；**apply 拒绝含 mismatch 的题** |
| 覆盖率不足 | 报告 `needs_more`；无 override 则 apply 跳过或整单失败（默认**整单失败**，避免半应用） |
| apply 写盘失败 | 正式库回滚（未 replace），job=`approved`，告警 |
| OCR 未启用却传 PDF | `failed`，明确错误码 `OCR_REQUIRED` |
| 并发 apply 同一正式文件 | 文件锁（flock）；第二请求 409 |

### 9.3 时间与日志

- 所有对外展示时间使用 Asia/Shanghai（UTC+8）并带时区标签。
- worker 日志写 journald；每 job 另有 `reports/parse.json` 作结构化审计。

---

## 10. 分阶段交付

### 10.1 v1（本规格实现目标）

- import-api + 单 worker + import-cli + review-ui（gate 后）
- textbook：`expand` + `mappings_only`（文本/MD/CSV）
- paper：文本/MD/CSV 切题 + topic 建议 + verify_qbatches 草稿
- 状态机与门闸完整；mappings 自动 apply；新点/新题人工
- 接现有 validate/verify；apply 原子写正式 JSON
- PDF/OCR：**接口预留，默认关闭**

### 10.2 v1.1

- OCR 插件（PDF 目录页 / 试卷扫描件 → `normalized.md`）
- 人工校对队列字段（OCR confidence）
- Word `.docx` → 文本的官方转换路径（仍先变 MD 再解析）

### 10.3 后续（非 v1 承诺）

- 按学科/学段拆分加载 `questions.json`（缓解约数 MB 整包；导入模块 API 已按 draft 分文件，便于将来对齐）
- 审核台浏览器上传
- 半自动「目录挂靠已有知识点」合并 UI（减少误新建）
- 与 `deploy-cf.sh` 的一键挂钩（apply 成功后可选）

---

## 11. 验收标准

1. 运维仅用 CLI 能为**每个现有学科前缀**创建 textbook 与 paper job，并在 `data/imports/<jobId>/` 看到 meta/source/draft/reports。
2. 文本 TOC 的 `expand` job 进入 `needs_review`；未经 approve 时正式 `topics.json` / `dependencies.json` 字节级不变。
3. `mappings_only` 在合法草稿 + validate 通过后可自动 applied，且生成/更新 `data/mappings/<edition>.json`；错误 edition（人教）被拒绝。
4. 文本卷可切题，报告中含 topic 建议；新题路径必须人工 approve 后才进入正式 `questions.json`。
5. approve 后 apply：合并结果通过 `scripts/validate.py`（输出 VALIDATION PASSED）；含 `verify` 的题无 mismatch。
6. review-ui 在 gate 后可列出 job、查看 diff 与报告、执行通过/驳回；**不能**在未鉴权情况下访问。
7. 静态站与 SW 不因 imports 目录变化而缓存污染；诊断/图谱在未 deploy 新正式数据前行为不变。
8. PDF 在未启用 OCR 时得到明确失败，而不是空成功。

---

## 12. 范围外与风险

### 12.1 范围外（再次强调）

- 本会话不实现代码。
- 不做整书正文入库、不做自动商业教辅搬运。
- 不在 v1 承诺教师级内容正确性。
- 不在 v1 拆分正式 `questions.json` 运行时加载。

### 12.2 风险与缓解

| 风险 | 缓解 |
|---|---|
| **版权**：试卷/教材扫描 OCR 入库 | 默认不提交 source 到公开 git；服务端保留审计；文档声明运维自备授权；OCR 后置 |
| **AI/未审内容**进入正式库 | 新点/新题强制人工；审核台警告条；占位诊断题不可冒充已审 |
| **正式包体积**（题库整包已数 MB 级） | apply 仅增量；后续拆包；imports 不进 SW |
| **误 apply 破坏图谱** | apply 前全量 validate；原子 replace；flock；仅 approved |
| **与各科 `*_build.py` 双源真相** | 文档约定：导入 apply 后的正式 JSON 为真相；旧 build 仅用于历史重放，重放前需拉齐 |
| **软依赖边噪声** | 仅 soft + 待审标记；默认审核可一键去掉顺序边 |
| **ID 续号竞争** | apply 时重新读取正式库分配题号/topic 号，草稿 id 可重映射并写对照表到 `review/decision.json` |

### 12.3 文档归属

- 本文件：`docs/superpowers/specs/2026-11-03-import-module-design.md`
- 实现阶段另开 `docs/superpowers/plans/` 计划文档；实现不得与本规格门闸决策冲突，若冲突先改规格再写代码。

---

## 附录 A：与现有文档的关系

- `docs/edition-mappings.md` — mappings schema 与贡献语义（本模块 mappings 支路必须兼容）
- `docs/questions.md` — 题目字段、覆盖率、verify、validate 规则
- `docs/standards-mapping.md` / `docs/moe2025-alignment.md` — 课标条目权威性；导入只挂已有 id，不杜撰课标正文
- `docs/pwa.md` — 缓存与前端行为；imports 不得进入预缓存列表

## 附录 B：术语

| 术语 | 含义 |
|---|---|
| 正式库 | `data/topics.json`、`dependencies.json`、`questions.json`、`cn-curriculum-standards.json`、`data/mappings/*.json` |
| 草稿 | `data/imports/<jobId>/draft/**` |
| apply | 将 approved 草稿合并进正式库的原子操作 |
| expand | 教材导入的扩图模式 |
| mappings_only | 仅版本对照、不新建知识点 |

## 附录 C：2026-11-06 实现准入修订（本次骨架的优先约定）

本附录经 advisor 审核后补充，优先于本文中冲突的完整 v1 描述。
原文“本会话只设计”指 11 月 3 日的设计会话；11 月 6 日的实现已由任务简报授权。
本次是**可运行骨架与教材文本链路**，不代表第 10.1 节完整 v1 已实现。

1. **Schema（B1）**：以现有 `scripts/validate.py` 为准。配置覆盖现库十个学科前缀与学段 ID；difficulty 采用现库 `challenge`（兼容 `advanced`）。现有 mappings validator 只支持数学、英语；其他学科 mappings 返回 `MAPPINGS_SUBJECT_UNSUPPORTED`，不修改上游 validator 放宽规则。
2. **TOC 与覆盖率（B2）**：expand 生成真实占位草稿、相邻 soft 边、课标建议及完整候选校验报告后进入 `needs_review`。不伪造题库以通过覆盖率，不臆造跨正式库的先修边。孤立点、覆盖率缺口在报告中保留。`overrideCoverage` 不得绕过正式校验，本次不提供该 bypass。端到端 apply 使用含显式 topicId 的 mappings 文本/CSV。
3. **应用范围（B3）**：本次仅实现 mappings 单文件事务。expand/paper 必须先检查人工审批；未批准返回 `REVIEW_REQUIRED`，批准后返回 `APPLY_SCOPE_UNSUPPORTED`，正式库不变。paper 接受真实 job、保留源文件，worker 返回 `PIPELINE_NOT_IMPLEMENTED`；PDF 优先返回 `OCR_REQUIRED`。多文件事务与题目解析属于完整 v1 后续工作，禁止把多次 `os.replace` 宣称为整体原子事务。
4. **门闸与恢复（B4）**：客户端不能决定 requiresHumanReview。自动审批要求 textbook+mappings_only 且 topics/dependencies/questions/standards_suggestions 实际为空。所有审批绑定完整 draft 摘要，apply 重新比对；变化后需重新审批。per-job 锁保护决策，单 worker 锁保护队列消费，全库 apply 锁冲突返回 409。替换前持久化目标前后摘要及 intent；中断后若目标等于已提交摘要则恢复 applied，否则在旧摘要时安全重试，第三种摘要拒绝恢复。源文件不可变；requeue 仅重试原源，改源创建新 job。
5. **数据隔离（B5）**：`IMPORT_WORKSPACE` 为含正式数据副本与 `data/imports/<jobId>/` 的独立持久目录，不得位于公开静态根或 releases。代码可在仓库，状态不能随发布目录切换。本次 API 仅绑定 loopback，审核使用独立端口；生产必须独立 origin，禁止受现有 SW 控制的同源部署。不依赖 Cache-Control 或 gitignore 解决静态泄露。job/diff/files/review 与写操作统一鉴权；文件限制在 job 内，源内容以文本展示。
6. **发布边界（B6）**：当前 qqvps 工作树服务尚未切到 `/srv/.../current`（来自任务简报，未在本次连接 VPS 核实）。发布产物与持久工作区分离；apply 更新工作区，不自动更新在线静态数据。数据需经明确审阅、交付并另行发布。现发布脚本尚无健康检查失败回滚保证，本次不运行发布或修改生产 nginx/systemd。

具体接口、状态恢复、测试和验收见 [实现计划](../plans/2026-11-06-import-module-implementation.md)。

