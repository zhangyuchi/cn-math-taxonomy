# 导入模块完整交付审核与验收

日期：2026-11-06（Asia/Shanghai）。范围：本轮用户A–F（完整v1、OCR接口、qqvps切流）。此记录与上一轮骨架审核分别保留。
当前状态：实现与本地主要验收已完成；现场部署与最终advisor审核进行中，未将计划当作上线事实。

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
| `python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_import_module.py' -v` | 44项通过；含旧mappings回归、paper MD/TXT/CSV/choice、十前缀、建议/null、unparsed/零题、mismatch、审核摘要竞态、五文件修订中断 |
| 多文件故障注入 | 三文件真实expand成功；每个替换前失败恢复旧字节；真实子进程os._exit(77)后显式恢复；commit后meta故障；回滚再次故障；第三种摘要、坏备份拒绝全部恢复；跨任务及ID竞争 |
| `python3 -m unittest discover -s tests -p 'test_publish.py' -v` | 3项通过；真实archive/HTTP与受控systemd，切前失败、首次切后失败恢复current原不存在与旧服务、后续失败恢复旧current/drop-in |
| `python3 import-module/smoke.py` | 独立API/CLI/worker通过；paper人工批准后applied；教材补齐后3文件apply且原validator通过；mappings自动applied；OCR关闭/fixture成功 |
| `CHROME=... .codex-run/browser-venv/bin/python import-module/browser-smoke.py` | Chromium153.0.8010.12；登录、完整JSON修订、过期页面批准409、paper/mappings apply、驳回、原文XSS仅作文本、390px无横向溢出；pageErrors=[] |
| `python3 scripts/validate.py` | VALIDATION PASSED，3297点/4868边/13335题；保留基线cnm_130/cnm_192空课标警告 |
| JS回归 | `node tests/test_engine.js` 95；`test_children.js` 38；`test_blindspots.js` 8，均通过 |
| 编译/语法/空白 | compileall、node --check、git diff --check通过；最终批次另补 |
| 正式JSON字节 | 开工保存93份既有JSON SHA256；本批次全数一致。所有apply仅在隔离副本，真实仓库无正式JSON写入 |

初始新增11项测试在缺失功能时红（6fail/5error）；新增HTTP快照用例先因缺digest失败；发布用例先证明旧脚本会将缺文件release切上线。未伪称新增所有测试都有独立红绿历史。
测试发现旧worker恢复后会自动重放人工approved事务，已改为prepared恢复仅回滚，人工再显式apply；自动重放仅纯mappings。首次发布夹具修正为实际旧服务目录，未放宽健康判定。

截图/日志只写忽略的 `.codex-run/`；无原题原文/密钥进入Git。

## 生产现场

开工现场：VM-0-13-ubuntu，1967MiB；旧cn-math.service指工作树、loopback8000；current不存在；nginx gate后proxy8000；匿名302、login200。
已有nginx `jiangshan-map.conf` 的 duplicate webp警告在切流前已存在。计划保持共享nginx/gate及其他站点配置不变。

此处将在实际发布、健康/浏览器、鉴权、其他站点与回滚结果后更新；在补齐前不视为已上线验收。

## 终审与交付

最终advisor尚待全部完成后另行spawn；主代理终判、生产SHA、提交/push结果待实证补齐。
明确不包含：真实OCR引擎/中文模型的识别准确率验收（本轮允许fixture接口验收）、Word、公网上传、多租户、学生端拆包。它们不是本轮未完成A–F。
工作区数据上线需要显式Git交付和单独发布；本轮生产不应用真实新知识数据，避免未经内容审核的演示题上线。
