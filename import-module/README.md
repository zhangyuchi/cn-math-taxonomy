# 运行教材导入骨架

本模块提供 CLI → HTTP API → 单 worker → 草稿/审核 → mappings 应用链路。
本次交付是 **v1 骨架**：教材目录扩点可生成草稿并审核；仅纯 mappings 支持正式工作副本写入。
完整文本卷解析、多文件扩库事务、OCR、题目/课标/边编辑、生产部署尚未实现。
设计与准入依据见 [实现计划](../docs/superpowers/plans/2026-11-06-import-module-implementation.md) 和 [审核记录](../docs/superpowers/reviews/2026-11-06-import-module-review.md)。

## 环境与快速验收

运行依赖：Linux、Python 3.10+ 标准库；本次实测 Python 3.14.4，无第三方运行依赖。
从仓库根执行：

```bash
python3 -m unittest discover -s tests -p 'test_import_module.py' -v
python3 import-module/smoke.py
```

smoke 创建独立临时数据副本，实际启动 API、调用 CLI 和单次 worker。
预期输出 `ok: true`：TOC 需要人工审核，批准后仍明确提示扩库应用未实现；显式 mappings 自动 applied。
脚本退出关闭进程并清理临时副本，仓库正式 JSON 不变。

## 准备持久工作副本

**工作区不能被静态服务器提供，不能放在代码仓库、公开静态根或 release 下。**
`data/imports/.gitignore` 只防止意外提交，不提供 HTTP 访问控制。
以下命令创建新的本地临时工作区；如需持久运行，使用独立私有目录并自行备份。

```bash
export IMPORT_WORKSPACE="$(mktemp -d /tmp/cn-math-import.XXXXXX)"
mkdir -p "$IMPORT_WORKSPACE/data"
cp data/{topics,dependencies,questions,cn-curriculum-standards,id-aliases}.json "$IMPORT_WORKSPACE/data/"
cp -R data/mappings "$IMPORT_WORKSPACE/data/"
read -rsp '设置私密 IMPORT_TOKEN: ' IMPORT_TOKEN
export IMPORT_TOKEN
export IMPORT_API_URL=http://127.0.0.1:8765
```

在保留相同三个环境变量的两个终端分别运行：

```bash
python3 import-api
```

```bash
python3 import-worker
```

worker 也支持 `python3 import-worker --once`，每次最多处理一个 job，并先恢复中断的 queued/running 任务及未完成 apply。
缺少 workspace 或 token 时拒绝隐式使用仓库静态根；API 固定绑定 `127.0.0.1`，默认端口 8765。

## 教材目录与显式 mappings

扩点 TOC 可使用 [示例 MD](examples/toc.md)：头部 `grade: 7` / `term: 上` / 可选 `prefix: cnm`，一级标题为章，二级标题或普通行作为知识点。
也支持 UTF-8 TXT、CSV：`grade,term,unit,chapter,topic_name,optional_notes`。
缺失 grade/term 可通过 CLI 补充；源中已有而与 CLI 冲突时失败。

```bash
python3 import-cli textbook --mode expand --prefix cnm --file import-module/examples/toc.md
```

worker 将生成带“导入草稿，待审”标记的 topics、相邻 soft 边、课标建议及真实候选全量校验报告，并进入 `needs_review`。
目录不包含题库，覆盖率/孤立点等错误会保留；不会为了通过校验虚构题目或跨库依赖。
`--domain` 可显式指定待审领域，否则使用配置建议值。

mappings 必须显式提供 `topicId`，可用 [CSV 示例](examples/mappings.csv) 或同列名的 Markdown 表格。
最小列为 `topicId,grade,term`；可选 `unit,chapter,note`。
目前仅支持 `cnm`/`*_math` 与 `cne`/`*_english`，其他学科返回 `MAPPINGS_SUBJECT_UNSUPPORTED`。

```bash
python3 import-cli textbook --mode mappings_only --prefix cnm \
  --file import-module/examples/mappings.csv --edition importdemo_math \
  --label '导入演示版本' --source '原创管道示例，不是真实教材对照'
```

默认校验成功后自动 `approved → applied`，只更新工作区的 `data/mappings/importdemo_math.json`。
相同 topicId 更新本次条目，其他条目保留，依据逐次保留；本次新输入必须自带 label/source。
不会批量重写 topics 内嵌 mappings。示例不代表真实教材对齐，不应直接发布为真实对照。
追加 `--no-auto-apply-mappings` 可要求人工审核。

## 审核及运维命令

```text
python3 import-cli list [--status needs_review] [--type textbook] [--limit 100]
python3 import-cli status <jobId>
python3 import-cli diff <jobId>
python3 import-cli approve <jobId> [--comment '核对意见']
python3 import-cli reject <jobId> --reason '需补充依据'
python3 import-cli apply <jobId>
python3 import-cli requeue <jobId>
python3 import-cli paper --prefix cnm --file exam.md
```

CLI 只请求 API；成功退出 0，HTTP 业务拒绝退出 2，系统/传输错误退出 1。
创建返回 queued 表示已落盘入队，最终状态需用 status 查询。
paper 会保留真实 job/source 后以 `PIPELINE_NOT_IMPLEMENTED` 失败；PDF 优先返回 `OCR_REQUIRED`。

浏览器打开 `http://127.0.0.1:8765/import-login/`，输入同一 token 后进入审核台。
登录仅换取 8 小时的 HttpOnly/SameSite 会话，token 不存入 URL 或 localStorage。
审核台可查看任务、原文摘要、before/after diff、完整报告，执行通过/驳回，以及批准后的 mappings apply；不提供上传和草稿编辑。
所有业务 API 及 `/import-review/` 都必须鉴权；公开登录页不含 job 数据。

远程运维使用 SSH 本地转发。生产部署需要独立 origin、gate、TLS，不能复用现有 SW scope；
仅设置 no-store 不能阻止现有 SW 缓存。`--secure-cookie` 仅供完成独立 HTTPS 反代验收后启用，本次未做生产 gate 集成。

## 状态、拒绝和恢复

- queued → running → needs_review → approved → applied；解析失败为 failed，待审可 rejected。
- 新知识点/新题未经人工审批：`REVIEW_REQUIRED`；批准后仍因本次范围返回 `APPLY_SCOPE_UNSUPPORTED`。
- 纯 mappings 自动审批同时检查实际草稿为空的 topics/dependencies/questions/课标建议，不信任客户端 flags。
- 审批绑定完整 draft 摘要。变化后 apply 返回 `DRAFT_CHANGED`，归档旧决策并退回 needs_review，允许重新批准；只在确认旧 intent 尚未提交时归档它。
- apply 在任务锁和全库锁内重新校验“正式 ∪ 草稿”；失败返回 `VALIDATION_FAILED`，保持 approved，正式文件不变。不支持覆盖率 bypass。
- `BUSY`（HTTP 409）表示锁竞争；可稍后重试。
- replace 前记录 `review/apply-intent.json` 的前后摘要。中断恢复时：目标为新摘要则恢复 applied，为旧摘要可安全重试，为第三种摘要则 `RECOVERY_CONFLICT` 并停止写入。
- 若旧事务已写入而 draft 又被外部修改，恢复原草稿后再恢复状态；修订另建 job。不得用新审批覆盖未确认的提交结果。
- 源字节只写一次，requeue 只允许 failed/rejected 重试同一源；修正源应创建新 job，手工改源返回 `SOURCE_CHANGED`。
- applied 不再接受审批/驳回/重试，重复 apply 为只读幂等查询。完整审计位于 job 的 review/reports 下。

API 根为 `/import/v1`；创建为 multipart POST `/jobs/textbook|paper`；list/status/diff/files 使用 GET；
approve/reject/apply/requeue 为 POST JSON，错误统一 `{error, code}`，校验错误附 reports 路径。
文件读取路径限定当前 job；源内容按文本展示。最大源文件 1 MiB，请求体最大 2 MiB。

## 与线上及上游的关系

apply 更新独立工作副本，**不会自动更新线上静态数据或发布 release**。
工作区的数据交付、生产 gate/systemd、健康检查及回滚必须另行验收，见 [部署约束](../deploy/README.md)。
保留现有 scripts/build 与 validator；新代码集中在新目录，降低同步 upstream 时的冲突。
