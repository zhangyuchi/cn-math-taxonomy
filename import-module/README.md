# 导入教材与试卷

模块提供 CLI → 认证API → 单worker → 草稿业务审核 → apply。支持教材TOC（expand/mappings_only）、文本卷MD/TXT/CSV、PDF OCR 后自动结构化，以及经过完整校验的可恢复多文件扩库。扫描卷不要求人工改写 Markdown。
新点/新题必须人工批准；仅纯mappings可在校验成功后自动应用。apply只更新独立工作副本，**不会自动发布线上数据**。

范围、事务与审批契约见[修订规格附录D](../docs/superpowers/specs/2026-11-03-import-module-design.md#附录-d2026-11-06-完整交付准入优先于附录-c-的骨架范围)、[完整计划](../docs/superpowers/plans/2026-11-06-import-module-completion.md)和[验收记录](../docs/superpowers/reviews/2026-11-06-import-module-completion-review.md)。旧骨架审核只描述历史。
本次文件上限和扫描卷自动结构化以[自动化规格](../docs/superpowers/specs/2026-11-06-pdf-paper-automation.md)及[自动化验收记录](../docs/superpowers/reviews/2026-11-06-pdf-paper-automation-review.md)为准。

## 快速自测

运行环境：Linux、Python 3.10+标准库（发布脚本需Python 3.12+的安全tar解包）；本轮开发机Python 3.14、VPS Python 3.12。
无需运行依赖安装。以下测试全部在隔离副本apply，不修改仓库正式JSON：

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_import_module.py' -v
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_ocr_mineru.py' -v
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_paper_automation.py' -v
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_paper_answers.py' -v
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
文件大小策略统一定义在 `import_module/limits.py`：MD/TXT/CSV 源上限 **16 MiB**，PDF 源 **64 MiB**，OCR 单段及合并输出 **64 MiB**，入站修订草稿 **64 MiB**，HTTP 请求 **65 MiB**（包含 multipart/JSON 元数据）。源文件不能为空；文本必须 UTF-8，PDF 由插件转换。CLI 与 worker 回读最多读取对应上限加一字节，HTTP 在读取前检查长度；保留有限上限以约束内存缓冲，PDF/OCR 为扫描图像和公式预留更大空间。单 worker、OCR 超时与临时目录清理继续生效，工作区需为原文、草稿及审计备份预留磁盘。

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
solutionHint: 利用相反数之和为零。
solutionDetail: -2+2=0，所以-2的相反数是2。
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

CSV列：`prompt,answer,type,difficulty,topicId,choices,explanation,solutionHint,solutionDetail,verify,unordered`，可选列可省略；choices为JSON数组或分号分隔 `A:文字;B:文字;C:文字`。

### 两段解析与无答案卷补答

`answer` 只存最终结果：选择题存选项键，填空/解答题存结果。`numeric` 多值沿用逗号分隔数值；带分问标签或多行结论须使用 `short`。`solutionHint`、`solutionDetail` 是输入字段，解析后合成既有 `explanation`，不向正式题目增加字段：

```markdown
### 思路提示
利用相反数之和为零。

### 详细解答
-2+2=0，所以-2的相反数是2。
```

也可直接在 `explanation:` 后提供上述多行格式。OCR 支持 `【思路提示】`、`【详细解答】` 或带冒号的中文标签。旧 `explanation` 完整保留为详细解答，提示注明“原文未提供思路提示，待人工补充”；缺解析时仍明确待补，不自动编造解法。混合字段内容冲突时拒绝该块（OCR 整卷结构失败），不得静默覆盖。

审核台选中任务后，“逐题答案与解析”默认展开，可直接阅读最终答案、风险及两段正文；所有内容按纯文本显示。编辑仍使用“修订完整草稿”的 `answer` / `explanation`；保存后重新送审。

无原卷答案时，解析器保留空答案；本模块没有通用自动求解器。可用离线转换器将**已按七年级知识撰写、仍需人工复核**的补答清单回填空稿，禁止用初二/初三或高中技巧，不得超出题干可推出的内容。知识范围由编辑者声明并由人审确认，程序不证明数学正确性。选择题 OCR 残缺时不猜字母；题图、表格或关键分数不清时只写可靠的思路框架和已知步骤，明确“依赖原图/需对照原卷”，保留空 `answer`。

```bash
python3 import-cli draft <jobId> > "$IMPORT_WORKSPACE/before.json"
# 按下述清单格式撰写 solutions.json，覆盖该快照全部题 ID
python3 -m import_module.answers --snapshot "$IMPORT_WORKSPACE/before.json" \
  --solutions "$IMPORT_WORKSPACE/solutions.json" --output "$IMPORT_WORKSPACE/revised.json"
IMPORT_ANSWER_DIGEST=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["expectedDraftSha256"])' "$IMPORT_WORKSPACE/revised.json")
python3 import-cli revise <jobId> --file "$IMPORT_WORKSPACE/revised.json" \
  --expected-digest "$IMPORT_ANSWER_DIGEST" --comment '七年级生成补答，待人工复核'
```

清单顶层严格包含 `jobId`、`sourceSha256`（均取快照同名值）、`expectedDraftSha256`（取快照 `draftSha256`）、`knowledgeScope: "grade7"`、`solutions`。每行格式如下，ID 必须来自快照，不能重复、缺题或多题：

```json
{"id":"快照中的题ID","answer":"","hint":"先读取图中条件，再列式。","detail":"关键图示未核实，依赖原图/需对照原卷，暂不能确定最终结果。","answerUncertain":true,"originalRequired":true}
```

`answerUncertain` / `originalRequired` 必须为布尔值；任一为 true 时只接受空答案。能确定结果时两者均为 false 并填写答案。只允许待审数学卷、空答案与空/占位解析，已有实质内容须人工通过 `revise` 编辑；错任务、错源、过期摘要、题集不符或覆盖内容都会拒绝。转换器只生成本地 JSON；`revise` 再次在锁内核对摘要，保留修订历史，状态仍为 `needs_review`，不会批准或 apply。

生成稿正文含 `【生成补答，待人工复核】`，按需含 `【答案不确定】` / `【依赖原图/需对照原卷】`。当前 `questionRisks` 推导 `generated`、`answerUncertain`、`originalRequired`；报告及 meta flags 汇总 `PAPER_ANSWER_GENERATED`、`PAPER_ANSWER_UNCERTAIN`、`PAPER_ORIGINAL_REVIEW_REQUIRED`，并保留空答案/图片原有风险。注记是编辑声明，不是机器准确性认证；人工确认并修订后可删除相应注记，旧稿仍在修订审计中。**两段齐全不等于答案完整；空答案仍被原 validator 拦截，生成稿仍须人工批准。** 正式 schema、候选导出及 apply 都继续使用原有 `answer` / `explanation`。

```bash
python3 import-cli paper --prefix cnm --file exam.md
```

每道成功导入题均自动具有 `topicId`：合法显式挂点优先；未填写或同前缀未知 ID 时，选择同 `--prefix` 学科内得分最高的现有非 external 知识点。候选的 subject 须与学科一致（数学兼容缺省/null/math）；显式跨学科挂点仍返回 `SUBJECT_CONFLICT`。无可用知识点返回 `NO_TOPIC_CANDIDATES`，整卷失败，不生成虚假挂点。

评分沿用名称规则：题干与名称忽略大小写，完整名称包含加 20 分，每个不同连续二字片段命中加 1 分；按分数降序，同分按 `topicId` 字典序升序。零分仍选第一项并标记 `zeroEvidence=true`。规则分用于复核，不表示准确率或置信概率。
`reports/parse.json.matches` 保留初始选择 `topicId`、`score`、`match`（`explicit` / `best_match`）、原始 `suppliedTopicId`、`zeroEvidence`、`tieBreak`、`suggestedTopicIds`，以及最多五个 `candidates`（含名称、分数、完整名称命中及命中二字片段）。显式挂点优先时，所选点可能不在前五候选内，`score` 仍为该显式点得分。报告的 `matchesScope=initial_parse` 表示初始机器证据；复核改挂后仍保留，当前挂点以草稿为准。
新题始终 `needs_review`；人工只需复核答案、难度及自动挂点，必要时可改挂，无需逐题补点后才能批准/apply。
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

fixture只检查PDF标记并输出原创固定MD，**不识别PDF，不代表OCR准确率验收**。成功后发布 `source/normalized.md`；PDF paper 自动生成 `draft/questions.json`，进入业务审核。
`IMPORT_OCR_TIMEOUT` 默认120秒，上限600；超时终止整个插件进程组。输出必须非空普通文件、非符号链接、UTF-8、≤64 MiB。成功报告保留pluginSha256/confidence=unreviewed；OCR失败删除旧normalized，保留原PDF与错误；结构解析失败仍保留已成功生成的 OCR 原文及结构诊断。
错误：`OCR_REQUIRED`、`OCR_PAGES_REQUIRED`、`OCR_CONFIG_INVALID`、`OCR_FAILED`、`OCR_TIMEOUT`、`OCR_OUTPUT_INVALID`。

### aipower 的 HOME MinerU 安装

真实 OCR 仅在开发机 `aipower` 使用。稳定入口为 `/home/zhangjun/tools/mineru/venv/bin/mineru`（其他用户为 `$HOME/tools/mineru/venv/bin/mineru`）。这是独立 Python 3.12 环境，适配器通过 `Path.home()` 定位，不需要激活 venv；`pdf_md/.venv` 仅作过 CLI 参考，不是安装来源或运行依赖。仓库不捆绑引擎、模型和用户环境配置。

固定 MinerU **2.5.4**、CPU PyTorch **2.8.0+cpu**、torchvision **0.23.0+cpu**、transformers **4.49.0**。2.5.4 CLI 直接执行解析器，继承 worker 的进程组；参考环境 3.4.5 会另起 API 进程组，不能直接替换。4.x 也变更了 CLI。升级必须重新验证参数、输出路径和超时清理。

在 Linux x86_64 上重建（需要已安装 `uv`、网络和足够磁盘）：

```bash
cd /home/zhangjun/Projects/github.com/cn-math-taxonomy
uv python install 3.12
uv venv --python 3.12 "$HOME/tools/mineru/venv"
uv pip sync --python "$HOME/tools/mineru/venv/bin/python" \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  --index-strategy unsafe-best-match import-module/mineru-requirements.txt
uv pip check --python "$HOME/tools/mineru/venv/bin/python"
"$HOME/tools/mineru/venv/bin/mineru" --version
```

锁定文件记录本次独立安装的全部依赖。首次生成它所用的安装命令为：

```bash
uv pip install --python "$HOME/tools/mineru/venv/bin/python" \
  'mineru[pipeline]==2.5.4' 'torch==2.8.0+cpu' \
  'torchvision==0.23.0+cpu' 'transformers==4.49.0' \
  --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
```

适配器默认 `pipeline` + `ocr` + `ch`，`MINERU_DEVICE_MODE=cpu`、`MINERU_MODEL_SOURCE=modelscope`（可由开发机环境覆盖）。模型按需下载至 HOME 的 `~/.cache/modelscope`，Hugging Face 来源使用 `~/.cache/huggingface`；首次下载计入调用时间，宜先预热。旧缓存可能缺少该版本需要的模型，不能仅凭缓存目录存在判断可用。不修改已有 `~/mineru.json`；选择 `MINERU_MODEL_SOURCE=local` 前须确认其 `models-dir.pipeline` 包含本版本所需模型。环境和模型通常占用数 GiB，应在安装前以 `df -h "$HOME"` 检查空间。

仅在 **aipower worker 所在终端**配置；JSON argv 使用绝对路径，shell 不会替换 JSON 内的 `$HOME` 或 `~`：

```bash
export IMPORT_OCR_COMMAND='["/usr/bin/python3", "/home/zhangjun/Projects/github.com/cn-math-taxonomy/import-module/ocr_mineru.py"]'
export IMPORT_OCR_TIMEOUT=600
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
python3 import-worker
```

本机另保存同等配置于 `/home/zhangjun/tools/mineru/import-ocr.env`，可先 `source /home/zhangjun/tools/mineru/import-ocr.env`，再在已配置 `IMPORT_WORKSPACE` 的终端启动 worker。这不会自动改变已运行 worker 的环境。

独立验证示例（将输入替换为真实 PDF）：

```bash
source /home/zhangjun/tools/mineru/import-ocr.env
timeout -k 5s 600s /usr/bin/python3 \
  /home/zhangjun/Projects/github.com/cn-math-taxonomy/import-module/ocr_mineru.py \
  --input /absolute/path/toc.pdf --output /tmp/toc-ocr.md \
  --kind textbook --pages 1-2,5
```

`--pages` 为 1-based，最多10页，与 worker 相同；上述页码分别转换为 `-s 0 -e 1`、`-s 4 -e 4`，按序合并，不识别间隙页。教材不允许空页码，paper 可用 `--pages ''` 识别整卷。适配器使用每任务临时目录，仅在全部页段成功且合并 MD 合法后写目标；临时图像及中间文件退出即清理。超时仅由 worker 统一负责，独立运行时使用上述外层 `timeout`。

与 fixture 的区别：fixture 返回固定测试文本；此适配器实际调用本地 MinerU。PDF paper 的后续结构解析由 worker 自动执行，不要求 OCR 生成英文键值字段，也不要求用户改写 MD：识别连续数字题号、中文大题标题、同行/分行 A–D 选项、中文答案/解析标签，以及卷末编号答案、Markdown/简单 HTML 答案表。子问与公式保留；依据选项及答案形态生成 `choice`、`numeric` 或 `short`，默认难度 `standard`，每题按上述规则自动挂到同学科现有知识点，评分和候选写报告供复核。已有英文键值 OCR 插件输出继续兼容。

业务审核确认答案、难度和挂点后批准，不承担切题格式整理。PDF 无可识别题号、题号重复/不连续、未知未归属内容、答案冲突/错配、选项残缺等仍返回 `PAPER_STRUCTURE_FAILED`，整卷失败并保留原文与诊断；不以部分题替代完整试卷。分节边界的已知作答/评分说明跳过并保存在 `reports/parse.json.structure.ignoredNotes`（行号/所属节/原文），正文同句保留。“第×部分”也视为分节标题；选择题上下文支持括号/LaTeX 标签，归一化原始顺序与文本保存在 `structure.choiceNormalizations`，仍须人工核对。

**缺答案/缺图不阻断结构成功的 PDF 草稿。** 缺答案保留 `answer: ""`，不编造；`questionRisks` 按草稿 `id` 标记 `answerMissing`、`missingAssets`、已保存的 `assets` 及 `riskCodes`；卷级 `riskCodes` 汇总 `PAPER_ANSWER_MISSING`、`PAPER_ASSET_MISSING` 或 `PAPER_ASSET_REVIEW_REQUIRED`，报告与任务 flags 均要求人审。每题仍执行现有强制同学科最佳知识点挂接，状态为 `needs_review`，校验未通过也可复核。文本卷仍沿用缺答案块进入 `unparsed` 的契约。

适配器和 worker 在各自临时目录清理前，优先搬运本地安全图片到私有 job 的 `source/assets/`，并规范化 Markdown 中的资产路径；同名页段不会相互覆盖。仅支持根内、无符号链接的 PNG/JPEG/GIF/WebP 普通文件，每张 ≤16 MiB、每轮累计 ≤64 MiB；缺失/超限/不支持引用保留风险，不抓取远程地址。图片保存在报告 `assets`，可通过已有认证的 `/import/v1/jobs/<jobId>/files/<asset路径>` 作为二进制下载供审。

私有题图不自动发布。已保存图片为 `PAPER_ASSET_REVIEW_REQUIRED`，未找到为 `PAPER_ASSET_MISSING`；仍含图片引用的题干/答案/解析/选项在 apply 时重新检查并拒绝，须人工转写为完整文字等正式库支持的形式，补齐有依据的答案后保存修订并重新审核。空答案仍由原 validator 拦截，合法正式 `figure` SVG 沿用原校验。修订保留初始 `structure`、挂点与 `initialRisks`，当前风险重新计算。`needs_review`、批准、校验通过含义不同；正式题 schema 不增加风险字段。详见[可审草稿契约](../docs/superpowers/specs/2026-11-06-paper-reviewable-draft.md)。OCR 公式、表格及选项分割质量仍须对照原 PDF，不以结构成功代替准确率验收。

自动化夹具 `tests/fixtures/paper-mineru.md` 是无英文结构字段的原创中文 OCR 结果；测试完整执行 HOME 适配器、worker 和真实结构解析，仅替代耗时引擎。其通过证明自动路径及字段，不代表任意真实扫描件的识别准确率。

**qqvps 不配置此命令、不安装重 OCR。** 保持 `deploy/systemd/import-worker.service.example` 和安装器的 `IMPORT_OCR_COMMAND=`；VPS 使用已校对的文本导入。fixture 继续只用于协议验收，不能代替真实识别。

## 事务、失败与恢复

- queued→running→needs_review→approved→applied；解析failed，待审可rejected；failed/rejected可重试同一源，改源须新job。
- 新点/题未经人工批准 `REVIEW_REQUIRED`。审批绑定完整draft摘要；外部改动后apply `DRAFT_CHANGED` 并退回needs_review。
- 五文件修订也有revision-intent；prepared中断时读取/批准返回 `REVISION_PENDING`，worker或API启动显式恢复旧草稿并保持待审。恢复/重解析只清理本模块生成的普通临时文件，未知文件和符号链接仍拒绝；worker生成的大before备份按结构/摘要恢复，不受入站修订64 MiB限制。不会批准混合修订。
- mappings是单文件原子replace，兼容骨架intent恢复；新点/边/题使用**可恢复多文件事务**。备份/stage及目录先fsync，再prepared；全部替换后committed是提交点；最后applied。
- 普通故障补偿回滚；prepared进程死亡恢复旧值，committed之后恢复applied。回滚自身再次失败保留prepared，后续可重入。恢复前检查全部目标、备份和候选摘要；第三种内容/损坏备份 `RECOVERY_CONFLICT`，阻止全部新apply，不覆盖外部改动。
- 全库锁→job锁，候选/diff/审核快照与写入互斥。GET/解析不悄悄修复正式库，未完成事务时 `TRANSACTION_PENDING`；显式apply或worker/API启动先恢复历史事务。BUSY为409，稍后重试。
- 校验失败不写入，保持approved并更新报告。mismatch不能override；覆盖率、q1、图和学科不变量都由原validator决定。
- 不承诺无锁外部读取者看到跨文件瞬时原子快照；workspace禁止被静态服务器或历史build并发读写。多机共享锁与非合作写者不在支持范围。

## 部署与数据上线

[部署说明](../deploy/README.md)提供静态发布自动回滚、独立API/worker单位与生产验收命令。
运维默认 `ssh -N -L 18765:127.0.0.1:8765 qqvps-cd`，访问 `http://127.0.0.1:18765/import-login/`；该端口与站点SW origin隔离。
apply只改 `/srv/cn-math-taxonomy/import-workspace/data`。上线数据须先暂停工作区写入（停止API/worker或持全库锁）并确认事务已恢复，再将审阅后的正式JSON交付Git分支、跑原validate/回归、提交并发布新release；不得复制imports/source/token到Git或静态release。
