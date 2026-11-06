# 卷子与教材目录导入使用指南

适用对象：在 **aipower** 上操作的教师和运维人员。本文按 2026-11-06 的实现编写，入口为仓库根目录的 `import-api`、`import-worker`、`import-cli`。

基本流程：准备私有工作区 → 启动 API 和 worker → 提交文件 → 查看结果 → 人工修订、批准 → apply。**新题、新知识点必须人工审核；apply 只更新 `IMPORT_WORKSPACE` 内的数据副本，线上数据需要另行发布。**

## 1. 先选择学科与输入形式

`--prefix` 指定**库 ID 的学科前缀**，不是文件名前缀。例如数学使用 `--prefix cnm`，文件可以叫 `exam.md`、`七年级期中卷.pdf`；题目中的 `topicId: cnm_001` 则指定一个具体数学知识点。不要将 `cnm_001`、`cnm_p` 或文件名传给 `--prefix`。

以下前缀均已在[前缀配置](../../../import_module/subjects.json)及[正式知识点数据](../../../data/topics.json)中核对：

| `--prefix` | 学科 | 现有知识点 ID 示例 |
|---|---|---|
| `cnm` | 数学 | `cnm_p001`、`cnm_001`、`cnm_h001` |
| `cne` | 英语 | `cne_p001` |
| `cnc` | 语文 | `cnc_p001` |
| `cnp` | 物理 | `cnp_001` |
| `cns` | 科学 | `cns_p001` |
| `cnd` | 道德与法治 | `cnd_p001` |
| `cnh` | 历史 | `cnh_j001` |
| `cng` | 地理 | `cng_j001` |
| `cnb` | 生物 | `cnb_j001` |
| `cnk` | 化学 | `cnk_j001` |

卷子及教材 `expand` 可使用上述十类；不同学科支持的年级范围见前缀配置，不代表所有学科均覆盖所有学段。教材 `mappings_only` 当前只支持数学 `cnm` 和英语 `cne`。

| 输入 | 处理方式 |
|---|---|
| 已整理的 MD/TXT/CSV 卷子或目录 | UTF-8 文本，直接提交，无需 OCR |
| PDF 卷子 | aipower 上先加载 OCR 环境再启动 worker；可识别整卷 |
| PDF 教材目录 | 同样需要 OCR，且必须指定目录页，最多 10 页，不默认识别整本教材 |

每份源文件须为 **1 字节至 1 MiB**，PDF 也受此限制。较大文件先按需要拆分；教材优先只提供目录。普通文字并不自动符合导入格式，卷子至少需要可解析的题干、答案，格式见第 3 节。

## 2. 在 aipower 准备并启动服务

以下命令在 **同一个 Bash 终端**中依次执行，API 和 worker 在该终端后台运行，CLI 也在此执行。需要 Linux、Python 3.10+ 及已有仓库；PDF 使用 aipower 已安装的 HOME 独立 MinerU 环境。不要同时对一个工作区启动多个 worker。

### 2.1 首次创建私有工作区

工作区必须在代码仓库、公开静态根、`current` 和 `releases` 之外，且不能被静态服务器公开。以下操作每次创建一个新目录，保留该路径以便以后继续使用：

```bash
cd /home/zhangjun/Projects/github.com/cn-math-taxonomy
umask 077
export IMPORT_WORKSPACE="$(mktemp -d "$HOME/cn-math-import.XXXXXX")"
mkdir -p "$IMPORT_WORKSPACE/data"
cp data/{topics,dependencies,questions,cn-curriculum-standards,id-aliases}.json "$IMPORT_WORKSPACE/data/"
cp -R data/mappings "$IMPORT_WORKSPACE/data/"
printf '请保存工作区路径：%s\n' "$IMPORT_WORKSPACE"
```

**继续已有导入时跳过上述复制步骤**，只重新设置 `IMPORT_WORKSPACE` 为保存的原路径；不要用仓库数据覆盖已经 apply 的工作区。

```bash
read -erp '已有工作区的绝对路径：' IMPORT_WORKSPACE
export IMPORT_WORKSPACE
```

首次创建与继续已有工作区二选一，然后设置认证环境。token 自行设置为非空私密值；后续同一 API 的 CLI、浏览器登录均使用该值。不要把 token 放进 URL、命令参数、Git 或公开文件。

```bash
read -rsp '设置本次服务使用的私密 IMPORT_TOKEN：' IMPORT_TOKEN
printf '\n'
export IMPORT_TOKEN
export IMPORT_API_URL=http://127.0.0.1:8765
```

### 2.2 先启动 API，再启动 worker

API 固定监听本机回环地址。若 8765 已被其他导入服务占用，先确认其工作区和认证配置，不要继续向身份不明的旧服务提交。

```bash
python3 import-api >"$IMPORT_WORKSPACE/api.log" 2>&1 &
IMPORT_API_PID=$!
```

稍候运行下列命令，**确认返回任务列表（首次为空数组）后再启动 worker**。若暂时连接失败，查看日志并重试；日志有端口占用、工作区或事务恢复错误时应先处理。

```bash
kill -0 "$IMPORT_API_PID"
python3 import-cli list
```

需要导入 PDF 时，在启动 worker **之前**执行：

```bash
source ~/tools/mineru/import-ocr.env
```

该环境文件配置真实 MinerU 适配器、CPU 模式、OMP/MKL 线程数为 4 及 600 秒 OCR 超时。引擎位于 `~/tools/mineru/venv/`，无需激活其他项目的虚拟环境。首次模型下载也计入超时，可能需要提前预热；安装、模型与独立预热命令见[模块 OCR 说明](../../../import-module/README.md#aipower-的-home-mineru-安装)及 aipower 的 `~/tools/mineru/INSTALL.md`。

然后启动 worker：

```bash
python3 import-worker >"$IMPORT_WORKSPACE/worker.log" 2>&1 &
IMPORT_WORKER_PID=$!
```

重新 `source` **不会改变已经运行的 worker 环境**。如需启用或调整 OCR，应在任务处理完毕后按第 7 节停启 worker。只导文本无需加载 OCR 环境。

## 3. 提交一份卷子

### 3.1 文本卷：直接导入

以下是可运行的原创格式示例；实际使用时换成经整理的卷子内容和正确挂点。`topicId` 应来自所选学科的现有知识点，不能用前缀代替。

```bash
cat >"$IMPORT_WORKSPACE/exam.md" <<'EOF'
## Q1
topicId: cnm_001
difficulty: standard
type: numeric
prompt: 负数-2的相反数是多少？
answer: 2
explanation: 相反数之和为零。
verify: -(-2)
EOF
python3 import-cli paper --prefix cnm --file "$IMPORT_WORKSPACE/exam.md"
```

TXT 也可使用 `Q1`、`1.`、`1、` 编号块；CSV 使用 `prompt,answer,type,difficulty,topicId,choices,explanation,verify,unordered` 列，可选列可以省略。选择题选项、字段续行等完整格式见[模块“文本卷”说明](../../../import-module/README.md#文本卷)。

没有挂点或挂点未知的题会保留 `topicId: null`；名称关键词建议仅写入报告，不会自动确认归属。必须在审核时指定合法挂点。不能把“命令成功提交”视为“卷子已全部解析”。

### 3.2 PDF 卷：先确认 worker 已加载 OCR 环境

```bash
read -erp 'PDF 卷子的文件路径：' PAPER_PDF
python3 import-cli paper --prefix cnm --file "$PAPER_PDF"
```

PDF 的 OCR 结果写入该任务的 `source/normalized.md`，再进入文本解析。**OCR 不保证生成 `answer`、`type`、`topicId` 等结构化字段，不会补答案，也不交付临时题图资源。** 即使 OCR 成功，也可能报 `EMPTY_PAPER` 或存在漏题。

出现这种情况，在私有工作区内查看 OCR 文本，结合原 PDF 人工校对公式、题干、答案、题图和挂点，整理成第 3.1 节的文本格式后创建新任务；若原题依赖尚未补齐的题图，不应批准入库。不要使用返回固定文本的 `ocr_fixture.py` 识别真实卷子。

## 4. 教材目录：选择扩库或版本对照

### 4.1 `expand`：形成新知识点草稿

MD 目录用 `grade`、`term` 指定年级、学期（`上`、`下`、`全`），一级标题为章，二级标题或普通行为知识点。例如：

```markdown
grade: 7
term: 上
prefix: cnm

# 第一章 有理数
## 正数与负数
## 相反数
```

将整理好的目录保存为 UTF-8 文件后提交：

```bash
read -erp '教材目录文本路径：' TOC_FILE
python3 import-cli textbook --mode expand --prefix cnm --file "$TOC_FILE"
```

CSV 目录列为 `grade,term,unit,chapter,topic_name,optional_notes`。年级、学期也可通过 CLI 的 `--grade`、`--term` 补充；与文件内容冲突会拒绝解析。

PDF 教材必须限定目录所在的 **PDF 页序号，从 1 开始**，不一定等于印刷页码。下例只识别第 1、2 页；按真实目录位置修改，最多 10 页，也可写 `1-3,5`：

```bash
read -erp '含目录的教材 PDF 路径：' TOC_PDF
python3 import-cli textbook --mode expand --prefix cnm --file "$TOC_PDF" \
  --grade 7 --term 上 --ocr-pages 1-2
```

**只导入目录不会得到可直接 apply 的完整题库。** 系统生成新知识点、相邻软依赖和待补全建议；教师须修订描述、诊断题、真实练习题、课标挂靠及合理先修关系。每个新点至少 3 题，至少包含 `basic`、`standard`；小学数学还有 `challenge` 等约束。`q1` 题干须以该点诊断题题干开头，依赖图须满足连通与学科规则。挂靠已有真实课标对象；课标建议文件不会直接改写课标正文。最终以原 validator 的完整校验结果为准，不能用批准代替补全。

### 4.2 `mappings_only`：对照已有知识点

适合仅补充教材版本与现有知识点的对应关系，不新增知识点或题目。CSV 至少有 `topicId,grade,term`，可选 `unit,chapter,note`；必须人工确认每条 ID 和教材来源。

```bash
read -erp '已核对的版本对照 CSV 路径：' MAPPINGS_FILE
read -rp '版本标识（数学以 _math 结尾）：' EDITION
read -rp '版本显示名称：' EDITION_LABEL
read -rp '本次对照的真实来源说明：' MAPPINGS_SOURCE
python3 import-cli textbook --mode mappings_only --prefix cnm \
  --file "$MAPPINGS_FILE" --edition "$EDITION" \
  --label "$EDITION_LABEL" --source "$MAPPINGS_SOURCE" \
  --no-auto-apply-mappings
```

上例显式要求人工审核，之后走第 5 节。若省略 `--no-auto-apply-mappings`，**合法纯 mappings 在完整校验成功后默认自动批准并 apply**；此例外不适用于新点、新边、新题。英语需同时改为 `--prefix cne` 和 `_english` 结尾的版本标识。版本标识使用小写字母、数字及下划线，首字符为字母；`renjiao_math`、`pep_math` 为保留版本，不能用此方式写入。仓库演示对照不是真实教材依据，不能当作真实版本发布。

## 5. 查看、审核并 apply

### 5.1 记录任务并观察进度

提交返回 `jobId`，将需要处理的 ID 输入变量。每切换一个任务都要重新设置，避免批准错任务。

```bash
read -rp '本次返回的 jobId：' JOB_ID
python3 import-cli list
python3 import-cli status "$JOB_ID"
```

普通人工流程为 `queued → running → needs_review → approved → applied`。`failed` 表示解析等失败，查看 `error.code`；`rejected` 表示人工驳回。`needs_review` 只是解析完成待审，可能仍有校验缺口；`approved` 也不保证 apply 能通过校验。

OCR/解析期间 worker 持有全库锁，`status`、`draft`、`diff` 可能返回 `BUSY`；此时用 `list` 看状态，稍后再读详情。不要因此重复提交或终止正在识别的 worker。

### 5.2 由教师审核：审核台或 CLI 二选一

**审核台：**在 aipower 的浏览器打开 `http://127.0.0.1:8765/import-login/`，输入本次 token 登录（会话有效期 8 小时）。若教师从自己的电脑远程操作 aipower，应由运维提供 SSH 端口转发；浏览器地址使用实际转发端口。不要开放公网端口或将审核台挂在现有静态站同源路径下。

审核台没有文件上传入口；先用 CLI 提交，再在任务详情中核对原文、草稿、差异和报告。需要修改时使用“修订完整草稿”，编辑五份 JSON 后保存，再重新审阅和批准。保存修订会使旧审批失效。批准之后仍需执行 apply，可使用第 5.3 节的 CLI 命令。

**CLI：**先读取差异和完整草稿快照：

```bash
python3 import-cli diff "$JOB_ID"
python3 import-cli draft "$JOB_ID" >"$IMPORT_WORKSPACE/reviewed.json" &&
  REVIEW_FILE="$IMPORT_WORKSPACE/reviewed.json"
```

检查命令成功后再打开 `reviewed.json`。它包含 `draft`、`draftSha256`、`reports`、`sourcePreview` 等字段；修改时仅编辑 `draft` 内的 `topics`、`dependencies`、`questions`、`mappings`、`standards_suggestions` 五项，保留原 `draftSha256` 用于提交版本检查。需要修订时执行：

```bash
REVIEW_DIGEST=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["draftSha256"])' "$IMPORT_WORKSPACE/reviewed.json")
python3 import-cli revise "$JOB_ID" --file "$IMPORT_WORKSPACE/reviewed.json" \
  --expected-digest "$REVIEW_DIGEST" --comment '根据原文完成人工修订' &&
  python3 import-cli draft "$JOB_ID" >"$IMPORT_WORKSPACE/final-review.json" &&
  REVIEW_FILE="$IMPORT_WORKSPACE/final-review.json"
```

只有修订及再次导出均成功才继续；失败时保留 `reviewed.json` 中的人工编辑，按错误处理，不得直接批准。成功后重新打开并审阅 `final-review.json`；不需要修订时审阅首次导出的 `reviewed.json`。`REVIEW_FILE` 指向当前应审阅的文件。至少核对：

- 原卷题数与 `reports.parse` 中解析数、`unparsed` 是否一致，漏题是否已补齐；机器挂点建议是否正确。
- 每题答案、公式、选项、难度、`topicId` 和必要图示是否可靠；`reports.verify` 是否有表达式核验不一致。
- `reports.coverage` 和 `reports.validate` 是否仍有未解决缺口；教材新增点是否已经完成第 4.1 节要求。

只有实际审阅通过后，才执行下列人工批准命令，显式绑定**所审阅快照**的摘要：

```bash
REVIEW_DIGEST=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["draftSha256"])' "$REVIEW_FILE")
python3 import-cli approve "$JOB_ID" --expected-digest "$REVIEW_DIGEST" \
  --comment '已核对原文、答案、挂点和完整校验报告'
```

默认不带 `--expected-digest` 的 approve 会自动抓取当前稿摘要，不能替代对所见版本的人工核对。发生 `DRAFT_CHANGED` 时重新读取、审阅再批准；不要只更新摘要而跳过审核。需驳回时可用 `python3 import-cli reject "$JOB_ID" --reason '需补充题目依据'`。

### 5.3 应用到工作区

```bash
python3 import-cli apply "$JOB_ID"
python3 import-cli status "$JOB_ID"
```

确认状态为 `applied`。apply 会重新校验当前完整候选库，通过后才写 `IMPORT_WORKSPACE/data`。若 `VALIDATION_FAILED`，数据不写入，任务保持 `approved`；重新读取报告，在审核台或 CLI 修订、重审后再 apply。答案表达式不一致须修正答案或表达式；确认不适用时可删除 `verify`，但仍须重新人工审核，不能 override 绕过校验。

## 6. apply 与线上发布的关系

服务运行在 **aipower**，服务代码位于仓库；上传原文件副本、草稿和 apply 结果保存在私有工作区。apply 不修改代码仓库的 `data/`，不更新 qqvps 的工作区、静态 release 或 `current`，也不自动提交、推送 Git。

上线由运维另行完成：暂停该工作区写入并确认待恢复事务处理完毕 → 仅将经审阅的正式 JSON 导出到 Git 交付分支 → 运行原 validate 与相关 JS 回归 → 审阅并提交 → 推送后显式发布该提交的完整 SHA。不得将 `data/imports/`、源文件、审批缓存或 token 交付到 Git 或静态 release；历史 build 也不能与 apply 并发写同一工作区。

发布操作以[工作流](../WORKFLOW.md#导入工作区与发布所有权)和[部署说明](../../../deploy/README.md#数据交付与恢复)为准。**qqvps 默认不安装、不启用重 OCR，也不会自动调用 aipower 的 OCR**；VPS 上应导入已校对文本。aipower 和 VPS 的环境变量、任务与工作区互不自动同步。

## 7. 停止与再次启动

先停止提交新任务，用 `list` 确认没有 `queued`、`running`，并确认无人正在修订或 apply，再停止**本终端记录的**进程：

```bash
python3 import-cli list --status queued
python3 import-cli list --status running
```

两次均为空数组且确认没有其他写入后执行：

```bash
kill "$IMPORT_WORKER_PID"
wait "$IMPORT_WORKER_PID" 2>/dev/null || true
kill "$IMPORT_API_PID"
wait "$IMPORT_API_PID" 2>/dev/null || true
```

`wait` 的非零退出可能来自上述主动终止。不要在 OCR 期间直接杀 worker；其 OCR 使用独立进程组，直接终止 worker 不保证清理 OCR 子进程。不要按进程名批量杀服务。

再次运行时，回到仓库根目录，使用第 2.1 节“已有工作区”命令及认证配置，按第 2.2 节先 API、后 worker 启动；PDF 仍须在启动 worker 前加载 OCR 环境。工作区及其审核记录应保留，后台进程不是 systemd 常驻服务；终端退出或机器重启后应重新启动。

日志位于私有工作区：

```bash
tail -n 50 "$IMPORT_WORKSPACE/api.log" "$IMPORT_WORKSPACE/worker.log"
```

## 8. 常见错误与恢复

错误可能出现在 CLI 响应，也可能在提交之后的 `status.error` 或 `reports` 中出现。CLI 成功退出码为 0，业务拒绝为 2，系统或传输故障为 1。

| 错误码 | 含义与处理 |
|---|---|
| `TOKEN_REQUIRED` / `UNAUTHORIZED` | 缺 token 或认证失败；检查当前终端和 API 使用的 token 是否一致 |
| `INVALID_PREFIX` / `SUBJECT_CONFLICT` | 前缀无效，或题目挂点与学科冲突；按第 1 节选择前缀与同学科 ID |
| `SOURCE_SIZE` / `ENCODING_REQUIRED` | 文件超过 1 MiB、为空或文本不是 UTF-8；拆分、转换后创建新任务 |
| `OCR_REQUIRED` | worker 未配置 OCR；aipower 按第 2 节加载环境并重启 worker，再重试原任务，或改用校对文本 |
| `OCR_PAGES_REQUIRED` | 教材缺目录页或页码范围不合法；限定最多 10 页，改参数后重新提交任务 |
| `OCR_CONFIG_INVALID` | OCR 命令 JSON argv 或超时配置无效；运维检查 worker 环境并在空闲时重启 |
| `OCR_TIMEOUT` / `OCR_FAILED` / `OCR_OUTPUT_INVALID` | OCR 超时、启动/识别失败或输出非法；原 PDF 保留，检查环境、模型和页数，排除原因后重试 |
| `EMPTY_PAPER` / `EMPTY_TOC` / `METADATA_CONFLICT` | 没有可解析题目/目录，或文件元数据与参数冲突；按规定格式整理文本，修正后新建任务 |
| `TOPIC_ID_REQUIRED` | 题目挂点未确认，或版本对照缺显式 ID；修订草稿或补齐对照输入 |
| `REVIEW_REQUIRED` | 新点、新题等尚未人工批准；审核最终稿后再 apply |
| `DRAFT_CHANGED` | 所见稿或获批稿已经变化；重新读取、审阅、批准 |
| `VERIFY_MISMATCH` / `VALIDATION_FAILED` | 表达式核验不一致或候选库不合格；读取报告，修订、重审，不可强制绕过 |
| `MAPPINGS_SUBJECT_UNSUPPORTED` / `INVALID_EDITION` | 对照学科或版本标识不支持；按第 4.2 节修正后新建任务 |
| `INVALID_STATE` | 当前状态不允许该操作；先查看状态，已 applied 的任务不要重新审批 |
| `BUSY` | 正在解析、修订或应用；用 list 观察，稍后重试，不能删除锁文件 |
| `REVISION_PENDING` / `TRANSACTION_PENDING` / `RECOVERY_CONFLICT` | 存在待恢复修订/事务或外部数据冲突；交运维按模块恢复流程处理，不直接覆盖数据或删除事务记录 |

仅 `failed`、`rejected` 可重试**同一原文件和参数**：

```bash
python3 import-cli requeue "$JOB_ID"
python3 import-cli list
```

需要修改原文件、学科、年级、页码等输入时，重新运行 `paper` 或 `textbook` 创建新任务；不要覆盖旧任务的 `source/original.*`。完整恢复边界见[模块“事务、失败与恢复”](../../../import-module/README.md#事务失败与恢复)。
