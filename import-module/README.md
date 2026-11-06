# 导入教材与试卷

模块提供 CLI → 认证API → 单worker → 草稿修订/审核 → apply。支持教材TOC（expand/mappings_only）、文本卷MD/TXT/CSV、可开关PDF OCR插件接口，以及经过完整校验的可恢复多文件扩库。
新点/新题必须人工批准；仅纯mappings可在校验成功后自动应用。apply只更新独立工作副本，**不会自动发布线上数据**。

范围、事务与审批契约见[修订规格附录D](../docs/superpowers/specs/2026-11-03-import-module-design.md#附录-d2026-11-06-完整交付准入优先于附录-c-的骨架范围)、[完整计划](../docs/superpowers/plans/2026-11-06-import-module-completion.md)和[验收记录](../docs/superpowers/reviews/2026-11-06-import-module-completion-review.md)。旧骨架审核只描述历史。

## 快速自测

运行环境：Linux、Python 3.10+标准库（发布脚本需Python 3.12+的安全tar解包）；本轮开发机Python 3.14、VPS Python 3.12。
无需运行依赖安装。以下测试全部在隔离副本apply，不修改仓库正式JSON：

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_import_module.py' -v
python3 -m unittest discover -s tests -p 'test_publish.py' -v
python3 import-module/smoke.py
```

smoke用真实API/CLI/worker独立进程完成paper人工门闸/apply、教材完整修订后多文件apply、mappings自动apply、OCR_REQUIRED与fixture插件；候选库通过原validator。
Chromium审核台验收使用可选测试依赖，不是服务运行依赖：

```bash
uv venv --python 3.12 .codex-run/browser-venv
uv pip install --python .codex-run/browser-venv/bin/python playwright
# CHROME指定已安装的Chrome/Chromium；也可用该venv的playwright install chromium安装浏览器
CHROME=/path/to/chrome .codex-run/browser-venv/bin/python import-module/browser-smoke.py
```

输出含 `ok: true`；截图写忽略的 `.codex-run/`。测试依赖缺失须安装，不能以未运行替代验收。

## 准备私有工作区

workspace必须位于代码仓库、公开静态根、current及releases之外。`data/imports/.gitignore`只防意外提交，不能提供HTTP访问控制。

```bash
export IMPORT_WORKSPACE="$(mktemp -d /tmp/cn-math-import.XXXXXX)"
mkdir -p "$IMPORT_WORKSPACE/data"
cp data/{topics,dependencies,questions,cn-curriculum-standards,id-aliases}.json "$IMPORT_WORKSPACE/data/"
cp -R data/mappings "$IMPORT_WORKSPACE/data/"
read -rsp '设置私密 IMPORT_TOKEN: ' IMPORT_TOKEN
export IMPORT_TOKEN
export IMPORT_API_URL=http://127.0.0.1:8765
```

两个终端使用相同环境，分别运行 `python3 import-api` 和 `python3 import-worker`。
API固定绑定loopback；worker支持 `--once`。缺workspace/token失败关闭，不隐式使用仓库目录。
源MD/TXT/CSV/PDF限1MiB；HTTP请求限2MiB；修订草稿限1MiB。源必须UTF-8（PDF由插件转换）。

## 教材与版本对照

expand输入采用[TOC示例](examples/toc.md)，或CSV `grade,term,unit,chapter,topic_name,optional_notes`。MD头部有grade/term，一级标题为章，二级标题/普通行为知识点；也可用CLI补元数据，冲突失败。

```bash
python3 import-cli textbook --mode expand --prefix cnm --file import-module/examples/toc.md
```

TOC生成新点、相邻soft边、空课标建议与真实校验缺口。不会自动虚构题库或跨正式库先修边。
在审核台修订五份草稿：补齐描述/诊断题、真实题库、先修关系和已有课标挂靠。每个新点必须满足原validator的≥3题、basic/standard、q1题干、图连通及学科约束。标准采用现库对象，例如 `{"id":"cms-2022:4.NA.01","confidence":"inferred"}`；建议文件不会直接修改课标正文。

mappings必须显式topicId；[CSV示例](examples/mappings.csv)最小列为 `topicId,grade,term`，可选unit/chapter/note，也支持同列Markdown表。
仅支持现validator已有的数学 `cnm/*_math`、英语 `cne/*_english`；其他学科mappings明确拒绝。保留edition人教限制。

```bash
python3 import-cli textbook --mode mappings_only --prefix cnm \
  --file import-module/examples/mappings.csv --edition importdemo_math \
  --label '原创导入演示版本' --source '原创管道示例，不是真实教材对照'
```

默认合法纯mappings自动applied；加 `--no-auto-apply-mappings` 要求人工审核。只更新 `data/mappings/<edition>.json`，保留其他条目和历史source，不批量重写topics内嵌mappings。示例不能作为真实版本对照发布。

## 文本卷

```markdown
## Q1
topicId: cnm_001
difficulty: standard
type: numeric
prompt: 负数-2的相反数是多少？
answer: 2
explanation: 相反数之和为零。
verify: -(-2)
```

TXT也支持 `1.` / `1、` / `Q1` 编号块。多行字段续行保留；choice使用3–4个连续A/B/C/D选项：

```text
choices:
- A: -2
- B: 0
- C: 2
answer: A
```

CSV列：`prompt,answer,type,difficulty,topicId,choices,explanation,verify,unordered`，可选列可省略；choices为JSON数组或分号分隔 `A:文字;B:文字;C:文字`。

```bash
python3 import-cli paper --prefix cnm --file exam.md
```

显式挂点必须同学科；未挂点/未知点保持null，最多5条名称关键词建议写 `reports/parse.json.matches`，不自动采纳。新题始终needs_review。
不完整块写unparsed，零题失败 `EMPTY_PAPER`。verify在受限子进程调用原核验逻辑；mismatch仍待审，但禁止apply。修正答案/表达式或删除verify后须重新审核。
正式题号在apply锁内续号；新点号竞争机械重映射全部新增引用，映射保存于 `review/apply-intent.json.idMap`。

## 审核台、修订与CLI

打开独立origin `http://127.0.0.1:8765/import-login/`，token换取8小时HttpOnly/SameSite会话；token不进URL/localStorage。业务API、文件、差异与审核台均鉴权，无浏览器上传入口。
审核台“修订完整草稿”提供五份JSON编辑，可修改题挂点/答案/依赖去留与强度/新点/课标；保存后审批失效，必须重新读取并批准。

```text
python3 import-cli list [--status needs_review] [--type paper] [--limit 100]
python3 import-cli status <jobId>
python3 import-cli diff <jobId>
python3 import-cli draft <jobId>                 # 返回含draft/draftSha256的一致快照
python3 import-cli revise <jobId> --file reviewed.json --expected-digest <draftSha256> [--comment ...]
python3 import-cli approve <jobId> [--comment ...] [--expected-digest <draftSha256>]
python3 import-cli reject <jobId> --reason '需补充依据'
python3 import-cli apply <jobId>
python3 import-cli requeue <jobId>
```

revise接受完整五字段对象，或 `draft` 命令导出的快照对象。approve未指定digest时，CLI先读快照再批准该版本；并发修订返回409。HTTP approve/revise必须 `expectedDraftSha256`，旧页面不能批准新稿。
CLI成功0，业务拒绝2，系统故障1。API根 `/import/v1`，创建POST `/jobs/textbook|paper`；GET jobs/status/diff/draft/files；POST draft/approve/reject/apply/requeue。
错误格式 `{error,code}`。覆盖率override显式拒绝，禁止绕过现有validator。

## PDF/OCR插件

默认禁用，PDF保留job/source并 `OCR_REQUIRED`。命令仅由worker运维环境配置，上传者不能决定可执行程序。
插件是一个JSON argv数组，系统追加 `--input <PDF> --output <临时MD> --kind textbook|paper --pages <页码>`，不使用shell。
教材必须 `--ocr-pages 1-3,5`，最大10页；paper可省略pages（整卷），不默认OCR整本教材。

接口验收使用明确标识的假插件：

```bash
export IMPORT_OCR_COMMAND='["python3", "/absolute/repo/import-module/ocr_fixture.py"]'
export IMPORT_OCR_TIMEOUT=120
python3 import-worker
# 在同API环境的另一终端：
python3 import-cli textbook --mode expand --prefix cnm --file toc.pdf --ocr-pages 1-2
python3 import-cli paper --prefix cnm --file exam.pdf
```

fixture只检查PDF标记并输出原创固定MD，**不识别PDF，不代表OCR准确率验收**。成功后只发布 `source/normalized.md`，进入与文本相同的流水线，机器内容仍须校对。
真实引擎应在开发机用户空间安装，并提供符合上述参数的适配程序（例如PDF页渲染+中文OCR+规范化MD）。替换argv为该适配器即可；本仓库不捆绑真实OCR引擎或中文模型。无真实引擎时仍可用fixture验收接口，不能将固定输出用于真实入库。VPS unit不配置重OCR。
`IMPORT_OCR_TIMEOUT` 默认120秒，上限600；超时终止整个插件进程组。输出必须非空普通文件、非符号链接、UTF-8、≤1MiB。成功报告保留pluginSha256/confidence=unreviewed；失败删除旧normalized，保留原PDF与错误。
错误：`OCR_REQUIRED`、`OCR_PAGES_REQUIRED`、`OCR_CONFIG_INVALID`、`OCR_FAILED`、`OCR_TIMEOUT`、`OCR_OUTPUT_INVALID`。

## 事务、失败与恢复

- queued→running→needs_review→approved→applied；解析failed，待审可rejected；failed/rejected可重试同一源，改源须新job。
- 新点/题未经人工批准 `REVIEW_REQUIRED`。审批绑定完整draft摘要；外部改动后apply `DRAFT_CHANGED` 并退回needs_review。
- 五文件修订也有revision-intent；prepared中断时读取/批准返回 `REVISION_PENDING`，worker或API启动显式恢复旧草稿并保持待审。不会批准混合修订。
- mappings是单文件原子replace，兼容骨架intent恢复；新点/边/题使用**可恢复多文件事务**。备份/stage及目录先fsync，再prepared；全部替换后committed是提交点；最后applied。
- 普通故障补偿回滚；prepared进程死亡恢复旧值，committed之后恢复applied。回滚自身再次失败保留prepared，后续可重入。恢复前检查全部目标、备份和候选摘要；第三种内容/损坏备份 `RECOVERY_CONFLICT`，阻止全部新apply，不覆盖外部改动。
- 全库锁→job锁，候选/diff/审核快照与写入互斥。GET/解析不悄悄修复正式库，未完成事务时 `TRANSACTION_PENDING`；显式apply或worker/API启动先恢复历史事务。BUSY为409，稍后重试。
- 校验失败不写入，保持approved并更新报告。mismatch不能override；覆盖率、q1、图和学科不变量都由原validator决定。
- 不承诺无锁外部读取者看到跨文件瞬时原子快照；workspace禁止被静态服务器或历史build并发读写。多机共享锁与非合作写者不在支持范围。

## 部署与数据上线

[部署说明](../deploy/README.md)提供静态发布自动回滚、独立API/worker单位与生产验收命令。
运维默认 `ssh -N -L 18765:127.0.0.1:8765 qqvps-cd`，访问 `http://127.0.0.1:18765/import-login/`；该端口与站点SW origin隔离。
apply只改 `/srv/cn-math-taxonomy/import-workspace/data`。上线数据须显式将审阅后的正式JSON交付Git分支、跑原validate/回归、提交并发布新release；不得复制imports/source/token到Git或静态release。
