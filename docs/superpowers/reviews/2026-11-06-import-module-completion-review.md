# 导入模块完整交付审核与验收

日期：2026-11-06（Asia/Shanghai）。范围：本轮用户A–F（完整v1、OCR接口、qqvps切流）。此记录与上一轮骨架审核分别保留。
当前状态：本轮A–F实现、自测、生产切流/实际回滚和线上浏览器全部完成，独立终审GO。

## 开工审核与主代理准入

实际 `spawn_agent(agent_type="advisor", task_name="opening_advisor")`，审核者 `/root/opening_advisor`。
结论 **GO_WITH_FIXES**；主代理判定 **GO_WITH_FIXES（六项开工阻塞约定已消化）**，随后才编码。

1. 一致审核快照，HTTP修订/批准都携带所见摘要；apply只用一次核准草稿。
2. 五文件修订revision-intent可恢复，pending拒绝读取/批准，成功失效旧审批。
3. 全库→job锁；备份/stage fsync后prepared，全部replace后committed；prepared回滚、committed恢复applied；历史单文件兼容；GET不修复正式库。
4. 真正可完成的expand修订/apply、保留原覆盖率/q1/图/课标schema；建议字段在报告、ID映射在事务审计。
5. OCR运维JSON argv、教材显式页/最多10页、输出与资源边界、错误保留原文、fixture与真实OCR质量分开。
6. 首次切流记录旧current存在性、服务drop-in/运行状态；独立bare mirror；正文健康与自动回滚；import独立代码指针。

以上先写入[规格附录D](../specs/2026-11-03-import-module-design.md)与[full gate计划](../plans/2026-11-06-import-module-completion.md)。
开工fetch完成，上游0 behind / 2 ahead，无需merge/rebase；没有force-push。用户已有.gitignore修改获准纳入。

## 本地验证证据（当前批次）

| 命令/场景 | 结果 |
|---|---|
| `python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_import_module.py' -v` | 52项通过；含旧mappings回归、paper MD/TXT/CSV/choice、十前缀、建议/null、unparsed/零题、mismatch、审核摘要竞态、五文件修订中断 |
| 多文件故障注入 | 三文件真实expand成功；每个替换前后失败恢复旧字节；真实子进程os._exit(77)后显式恢复；commit后meta故障；回滚再次故障；第三种摘要、坏备份拒绝全部恢复；跨任务及ID竞争 |
| `python3 -m unittest discover -s tests -p 'test_publish.py' -v` | 5项通过；真实archive/HTTP与受控systemd，切前失败、首次切后失败恢复current原不存在与旧服务、后续失败恢复旧current/drop-in |
| `python3 import-module/smoke.py` | 独立API/CLI/worker通过；paper人工批准后applied；教材补齐后3文件apply且原validator通过；mappings自动applied；OCR关闭/fixture成功 |
| `CHROME=... .codex-run/browser-venv/bin/python import-module/browser-smoke.py` | Chromium153.0.8010.12；登录、完整JSON修订、过期页面批准409、paper/mappings apply、驳回、原文XSS仅作文本、390px无横向溢出；pageErrors=[] |
| `python3 scripts/validate.py` | VALIDATION PASSED，3297点/4868边/13335题；保留基线cnm_130/cnm_192空课标警告 |
| JS回归 | `node tests/test_engine.js` 95；`test_children.js` 38；`test_blindspots.js` 8，均通过 |
| `CHROME=... node tests/test_children_browser.js` | 实际下载/跨上下文恢复、取消/合并/幂等/配额回滚、坏文件、375px与明暗主题通过，无页面异常 |
| 编译/语法/空白 | compileall、node --check、git diff --check通过；最终批次另补 |
| 正式JSON字节 | 开工保存93份既有JSON SHA256；本批次全数一致。所有apply仅在隔离副本，真实仓库无正式JSON写入 |

初始新增11项测试在缺失功能时红（6fail/5error）；新增HTTP快照用例先因缺digest失败；发布用例先证明旧脚本会将缺文件release切上线。未伪称新增所有测试都有独立红绿历史。
本地最终审查另发现物理mathLinks手工修订的对象schema被候选重映射误当字符串数组；新增回归先复现TypeError，再修正为对象id重映射，reason保留。新增所有目标已写但commit尚未落盘的恢复测试，扩展每个目标写前/写后故障。

测试发现旧worker恢复后会自动重放人工approved事务，已改为prepared恢复仅回滚，人工再显式apply；自动重放仅纯mappings。首次发布夹具修正为实际旧服务目录，未放宽健康判定。

截图/日志只写忽略的 `.codex-run/`；无原题原文/密钥进入Git。

## 生产现场

开工现场：VM-0-13-ubuntu，1967MiB；旧cn-math.service指工作树、loopback8000；current不存在；nginx gate后proxy8000；匿名302、login200。
已有nginx `jiangshan-map.conf` 的 duplicate webp警告在切流前已存在。计划保持共享nginx/gate及其他站点配置不变。

实际部署实现提交：`32f3d107fcee2c3a132e9fcd41883c8b72c21ec9`，普通push成功。VPS GitHub拉取慢，改用开发机增量bundle；VPS从旧本地仓库只读获取先决上游完整SHA，bundle verify通过，镜像ref与已推送SHA相同；旧工作树未跟踪docs保留。

- `current → /srv/cn-math-taxonomy/releases/20261106T041443Z-32f3d107fcee-3c36b7`。cn-math.service的WorkingDirectory与ExecStart均走current；进程cwd实际为该release。
- 发布器临时端口预检和切后13份核心HTML/JS/CSS/JSON的HTTP200+SHA256通过，index SHA256=`171329ff439422b2bb34484628a8138c6479fe5d52f1c558931fcb4291cb58d4`。
- 在VPS进程内生成gate签名cookie（不输出、不存储），经真实nginx检查 `/k12-knowledge-graph/`、explore、diagnose、children、assets/site.css及data/topics.json，均200且正文与release一致。
- 听写/江山/login/portal正文SHA256与切前一致，共享nginx/gate未修改。nginx -t通过，原有webp警告保持。
- 实际故障演练：同SHA再发布，切后 `PUBLISH_GATE_CHECK=["/usr/bin/false"]` 故意失败；发布非零、自动恢复前一current/drop-in和服务，intent=rolled_back、rollbackHealthy=true；随后真实gate路径与其他站点复核通过。不是仅靠mock声称回滚。
- 导入代码独立 `import-current → import-releases/32f3d107fcee2c3a132e9fcd41883c8b72c21ec9`；workspace=`/srv/cn-math-taxonomy/import-workspace`，root0600私密环境文件，API/worker均active，OCR默认关闭。
- 实际curl：API与审核台无鉴权401；静态8000的data/imports、review-ui均404；ss只监听127.0.0.1:8765。通过SSH独立18765端口运维，不进入站点SW origin。
- 现场日志：`.codex-run/online-before.log`、`vps-bundle.log`、`online-publish.log`、`online-rollback-drill.log`、`online-import-install.log`、`online-isolation.log`。无cookie/token/key内容。

线上首页独立诊断确认约50秒加载完成（数学870点），核心请求全部完成，应用pageErrors/load-error均为空。既有topics约2.8MiB、questions约8.8MiB，公网首次下载较慢；既有统计collect请求发生DNS失败，单列非核心问题，未将其伪称正常。advisor补充顾问判断采用保持nginx的方案A：该证据不构成部署故障，验收以单页90秒和真实就绪/交互为准，不新增本轮性能改造。若核心页面90秒仍不就绪继续诊断，不任意加时。线上最终Chromium153多页验收已通过（`.codex-run/online-browser.log`）：首页数据计数就绪、图谱canvas显示、诊断真实交互就绪、children页均200且非登录页；pageErrors=[]。SSH独立18765端口生产审核台登录成功、未认证API401、navigator.serviceWorker.controller=null。截图为 `.codex-run/online-{home,explore,diagnose,children,import-review}.png`，已实际查看图谱与移动审核台截图。

首次检查使用networkidle/30秒未通过，诊断后改为明确数据就绪及90秒上限。没有把失败隐藏或仅靠延长等待声称正常；同一现场独立诊断记录显示核心JSON实际完成，并按advisor补充判断保留首次下载成本与统计DNS问题。修复mathLinks后的提交 `02fcdec687b5dfc2ae866a2bb0863d74945b1f45` 也已普通push，并经增量bundle核验后重新发布静态及独立导入服务；健康/鉴权检查通过，现有workspace不覆盖。最终仅文档/审计提交的发布SHA以 `.codex-run/last-message.md` 为准，避免文档提交自引用。

## 终审与交付

全部实现、自测和现场部署后，实际spawn新的 `/root/final_advisor` 独立终审。终审结论 **GO**，对应代码提交 `c135f4403e1257ed745683582a83721377273c19`；主代理最终判定 **GO**。开工为GO_WITH_FIXES，六项阻塞约定先消化再编码；终审发现四项后继续修复至GO，无需用户复验。
已交付实现32f3d10、mathLinks修复02fcdec、终审边界修复c135f44，均普通push，无force。最终仅审核记录的文档提交及同代码发布SHA写 `.codex-run/last-message.md`，不在提交内自引用。
明确不包含：真实OCR引擎/中文模型的识别准确率验收（本轮允许fixture接口验收）、Word、公网上传、多租户、学生端拆包。它们不是本轮未完成A–F。
工作区数据上线需要显式Git交付和单独发布；本轮生产不应用真实新知识数据，避免未经内容审核的演示题上线。

## 独立终审发现与修复过程

终审实际spawn新advisor `/root/final_advisor`，与开工顾问分开。尚未据中间审阅声称GO；以下经主代理核查并补回归：

| 项目 | 独立证据与原因 | 修复与针对性验证 |
|---|---|---|
| F-ID-MAPPING（主代理复现，advisor核查） | 两个完整expand竞争，第二job新点应316→318，mapping仍指316，原validator因316已存在而通过 | mappings.entries.topicId与新点同步重映射；真实四文件成功及第4个新文件写后失败回滚（删除新mapping） |
| F-REV-TEMP | 真正os._exit于临时文件写好/rename前，残留.<draft>.json-随机名让严格集合检查阻断revision与worker恢复 | 只在显式恢复/queued重解析、全库→job锁内清理精确本模块临时名且普通文件；GET不清理，未知文件/符号链接保留并拒绝；真实revision中断、回滚再次中断、worker重入通过 |
| F-PUBLISH-ORDER | 遗留switching时fetch失败/非法新ref，原顺序在恢复前退出，current仍坏 | 发布锁内首先恢复历史意图，再fetch/ref解析；新增两项先红后绿，完整5项发布回归通过 |
| F-REV-SIZE | 合法TOC源约364KiB生成>1MiB草稿，删减修订后中断，原恢复误套入站1MiB限制 | before备份验证结构/完整sha，入站/after继续限1MiB；实际进程死亡后可恢复大before，不放宽正式validator |

主代理使用receiving-code-review先核查反馈与源码、再补复现；advisor独立复现上述恢复问题，并独立验证线上三服务、SHA、工作区权限、鉴权/静态隔离与真实gate路径。最新主代理完整52项导入回归exit0（120.544s）、5项发布回归exit0；真实smoke及本地Chromium再次通过，93份SHA一致。advisor独立51项全量（修复前不含新增大草稿用例）+新增大草稿恢复、5项发布、真实smoke、原validator和JS95/38/8均通过，确认四项代码问题闭合，无新增阻塞。最新修复 `c135f4403e1257ed745683582a83721377273c19` 已普通push并更新生产：static current→`releases/20261106T043710Z-c135f4403e12-6540c4`，import-current→`import-releases/c135f4403e1257ed745683582a83721377273c19`；workspace未覆盖，健康和鉴权通过（`.codex-run/online-reviewed-update.log`）。
advisor最终只读核实：17份线上Python运行文件与本地逐字节一致，operations发布器同步；三个服务active；未鉴权401、鉴权200、静态imports/review404；真实nginx六路径200，其他站点摘要与切前一致；GitHub分支SHA一致、工作树干净。终审 **GO**，无剩余实现或部署阻塞。

终审独立验证范围：51项导入全量＋新增大草稿恢复专项、5项发布、真实smoke、原validator及JS95/38/8；浏览器结论来自核对主代理日志/截图，不冒称advisor再次执行浏览器。主代理则运行完整52项最终回归及修复后的smoke/Chromium。最终收尾只改变审核文档，运行代码与已终审版本相同。
