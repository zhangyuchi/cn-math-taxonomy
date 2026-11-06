# 服务端错题本 v1 验收与审核

日期：2026-11-06；起点 `105c2187faafcb92aac2713c63cd168072b16836`；交付分支 `feat/wrongbook-server`，从 `feat/import-module` 派生，保留全部导入提交。范围为开发、自验、审查和推送，不含生产部署。

## 设计准入

实际调用 advisor `/root/wrongbook_design_advisor`，初审 GO_WITH_FIXES 后修订规格，复核明确 **GO**，之后才编写实现。三项闭合：打印执行 hideChoiceText；重新导出仅时间变化时保留首次导出时间；旧卷始终可读/打印，而回录须满足当前题库归属，全部原错题不可用返回422。见[规格](../specs/2026-11-06-wrongbook-server.md) ISSUE-D1..D3。

## 实现边界

- 独立 `wrongbook/`，Python 3.12+ 标准库；用户token哈希、user/child复合作用域、SQLite事务和卷面快照。
- CLI支持用户创建、逐题录入、诊断JSON导入、分页列表、掌握/撤销、删除、组卷/读卷、在线备份。
- 服务固定127.0.0.1:8766；工作区、备份、token在代码和静态根之外。独立systemd示例192MiB，静态发布排除后端目录。
- 正式JSON只读，原静态诊断未修改；本地孩子档案操作不自动同步服务端；本轮未设置生产备份排程。

## 自测证据

| 验证 | 结果与边界 |
| --- | --- |
| `python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_wrongbook*.py' -v` | 终审修订后19项通过；含SQLite重开、事务冲突回滚、两用户同child隔离、鉴权与输入/Origin边界、诊断重放、掌握/删除、错题/同点/先修/未见/缺额、旧卷快照、转义及隐藏选项、备份恢复 |
| CLI真实子进程 | 已完成录入后SIGKILL服务、重启查询、全库混合卷/HTML、再测回录、在线backup、删除后停服务恢复备份、同token重启查卷；不以同一Service对象代替进程重启 |
| 全库可保留演示 | 13335题快照；6题=1错题+2同点新题+3先修新题，无重复/缺额；SIGKILL重启保留1条错题，再测后备份恢复为2条；HTTP服务RSS=63356KiB（约62MiB） |
| Chromium真实打印 | 6题、6图全部加载，选项图显示且隐藏名称，无pageError；390px无横向溢出；A4 PDF生成184562字节；主代理已查看完整截图 |
| 发布回归 | 5项通过；新增测试先证明后端会进入静态release，加入排除列表后通过；原首次迁移和失败回滚仍通过 |
| `python3 scripts/validate.py` | VALIDATION PASSED；现有cnm_130/cnm_192课标基线警告保持 |
| 原JS回归 | engine95、children38、blindspots8，通过 |
| 全部Python回归 | 修订后最终103项通过（158.208秒，exit0），修订前101项亦通过（159.429秒）；前两次100项各有1个测试夹具问题（权限、超限请求体发送竞态），均已定位修正；未把失败轮次当作全量通过 |
| 机械与数据检查 | compileall、git diff --check、文档相对链接通过；93份既有正式JSON SHA256一致 |

证据文件位于忽略的 `.codex-run/wrongbook-*.log`、`wrongbook-demo-proof.json`、`wrongbook-browser-proof.json`、`wrongbook-print.png/pdf`。真实演示workspace、DB、备份和token均位于仓库外0700目录；审计JSON只保存路径与非敏感统计，不保存token。可打印演示的绝对路径见本地last-message。

额外边界验证：超出SQLite有符号整数范围的offset、5000位Content-Length已分别复现OverflowError/HTTP500，补入先失败后通过的用例，并收紧到可表达范围，统一400，不改变正常分页或请求上限。

测试红绿记录：首批缺少持久模块时失败；CLI缺失时失败；发布排除先失败后通过。后续HTTP失败由环境代理导致（loopback未绕过代理），按既有导入CLI采用ProxyHandler({})并禁重定向；测试也显式直连。权限夹具初次受进程umask077影响，未实际建成0755目录，修正为显式chmod后用例验证拒绝公开目录且不改其权限；不将该夹具失败伪称产品缺陷或历史有效红绿。超限HTTP测试改为只发送大Content-Length，验证服务在读取body前返回413，避免客户端仍sendall而服务已关闭连接的BrokenPipe竞态，不为测试要求服务继续读取超限body。

## 终审

独立advisor `/root/wrongbook_final_advisor` 对完整变更（含未跟踪文件）给出正式 **GO**，无剩余阻塞项。主代理核查其证据后最终判定 **GO**。本记录随功能一起提交，推送后的最终SHA与远端确认写入本地 `.codex-run/last-message.md`，避免提交内自引用。

终审独立执行修订后19项专项（3.655秒），额外HTTP验证未认证优先401、重复Content-Length400、Transfer-Encoding400、SQLite写锁503及释锁后同eventId重试200且仅插入1条；独立检查正式data无差异、git diff --check通过，实际查看打印截图。103项全量、validator、JS、全库演示与浏览器结论来自核对主代理日志与证据，不冒称advisor独立重跑。

## 剩余适用边界

生产尚未部署，无公网入口/自动浏览器同步；CLI及诊断JSON同步构成v1完整入口。运维负责每日及异机加密备份，交付已实际验证在线备份与恢复。历史导出没有逐题时间，明确使用首次导出时间；题库删除或改归属后旧卷仍可打印但不能无条件回录。公式保留原文/LaTeX源文本，无额外公式排版库。

## 终审修订复验

独立终审advisor `/root/wrongbook_final_advisor` 实际运行原17项专项并核对101项全量日志，独立查看完整卷面截图。复现部分失效原题的知识点被排除：主代理先补失败用例，再移动topics收集位置；仍要求至少一条可用原错题。另补CLI JSON/HTML输出对额外静态根的拒绝，先红后绿。当前修订后19项专项通过（3.435秒）；修订后103项全量通过（158.208秒，exit0）。

当前题库删除/改归属时，旧事件的原样重放也400，历史记录和卷面仍可读，已在规格/指南明确并增加回归。孤立Unicode surrogate触发500但整批回滚、无新增事件，经独立复现列非阻塞FU-2；不扩充本次正常中文录入范围。两项复核闭合后advisor正式GO。
