# 导入模块审核与验收记录

> 本文保留上一轮骨架的历史范围与结果。本轮完整v1/OCR/生产切流见[完整计划](../plans/2026-11-06-import-module-completion.md)和[完整验收](../reviews/2026-11-06-import-module-completion-review.md)。

日期：2026-11-06（Asia/Shanghai）。范围：任务简报授权的 **v1 骨架 + 教材 TOC 文本链路**。
不将本记录视为完整 v1 或线上部署验收。

## 实现前准入

主代理先实际调用 `spawn_agent(agent_type="advisor")`，审核者为 `/root/import_advisor`。
advisor 实际阅读规格、WORKFLOW、部署脚本、现有 scripts/data，并完成基线校验；最终答复后才进入文档修订与编码。

初审结论 **GO_WITH_FIXES**，阻塞项与解决约定：

| 项目 | 审核发现 | 开工前解决 |
|---|---|---|
| B1 | 学科前缀、mappings 学科支持、difficulty 与现库不符 | 校正十前缀，现有 validator 为准，mappings 限数学/英语 |
| B2 | TOC 无题库，无法直接通过正式覆盖率/图校验 | expand 到真实草稿+报告+needs_review，不伪造题目/关系，不绕过校验 |
| B3 | 多次 replace 无法承诺多文件事务 | 本次仅 mappings 单文件 apply，其他范围先人工门闸后明确拒绝 |
| B4 | 审批后修改、中断及幂等恢复约定缺失 | 完整 draft 摘要、任务/worker/apply 锁、持久 intent、不可变源 |
| B5 | 静态根暴露原文，同源 SW 可缓存 | workspace 在静态根之外，独立端口/origin，统一鉴权 |
| B6 | /srv 发布目标混成现状，release 无状态持久化/健康回滚 | 修订 WORKFLOW/deploy，工作区与发布分离，本次不切流 |

上述约定先写入规格附录 C、WORKFLOW、deploy/README 与实现计划。
主代理核查证据后的开工判定：**GO_WITH_FIXES（B1–B6 的阻塞约定已处理）**。

## 实现复核及修复

完成骨架后复用同一 advisor 独立只读审核，第一次代码复核为 GO_WITH_FIXES：

1. mappings 行元数据未与 CLI 参数交叉检查：先用回归复现 `7/上` 与 `8/下` 冲突仍 applied，再统一 metadata_value，现为 METADATA_CONFLICT。
2. 审批摘要变化后的恢复：退回 needs_review 并保存失效决策；替换前失败遗留的 intent 只在目标仍为旧摘要时归档。已写入或第三种摘要继续 RECOVERY_CONFLICT，不静默重放。
3. mappings diff 缺少 before：改为当前正式 before 与完整合并 after，覆盖既有条目的回归通过。
4. 双任务测试假设同秒 UUID 字典序等于创建顺序：改为逐个创建处理，不改变生产队列行为。

主代理另验证并修复：新 mappings 输入不能借旧 source 代替自身依据；HTTPError 响应句柄显式关闭；本地 HTTP 验收绕过外部代理。
本次使用测试先行，初始 16 项均在实现缺失时失败；恢复、元数据、diff 与依据缺失回归也先观察失败，再作对应修复。

advisor 最终复核为 **GO**：四项 findings 已闭合，批准范围内无剩余交付阻塞。
advisor 独立执行 8 项针对性回归；另验证“已 replace、meta 保存失败、draft 随后变化”保持旧 intent 与目标字节，恢复原草稿后可幂等恢复 applied。
该复核没有替代主代理的完整测试、独立进程或浏览器验收。

## 主代理最终验证

| 验证命令 / 场景 | 结果与范围 |
|---|---|
| `python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_import_module.py' -v` | **26 项通过**；真实临时正式库副本、落盘、十前缀、HTTP/CLI、门闸、错误、审批摘要、锁、intent 恢复 |
| `python3 import-module/smoke.py` | 真实 API、单次 worker、CLI 独立进程通过；TOC 人工门闸与 scope 拒绝；mappings 自动 applied；正式大文件字节不变；进程和临时副本退出清理 |
| `python3 scripts/validate.py` | VALIDATION PASSED；与基线相同的 cnm_130、cnm_192 空 standards 警告 |
| `node tests/test_engine.js` | 95 passed, 0 failed |
| `node tests/test_children.js` | 38 passed, 0 failed |
| `node tests/test_blindspots.js` | 8 passed |
| `python3 -m compileall -q import_module import-module/smoke.py` | 通过 |
| `node --check review-ui/app.js` / `login.js` | 通过 |
| `git diff --check` | 通过 |
| Chromium 153.0.8010.12，Playwright 用户缓存环境 | 登录、列表/详情/diff、人工批准、mappings apply、驳回通过；注入示例原文仅作为文本展示；390px 无横向溢出；pageErrors=[] |

浏览器仅为验收使用 Playwright，未新增模块第三方运行依赖；本次未将浏览器脚本/截图加入公开提交。
截图本地保存在 `.codex-run/import-review-desktop.png`、`.codex-run/import-review-mobile.png`。
验证不是全局安全审计、真实 gate 接入或多浏览器兼容性保证。

## 最终判定与未做事项

主代理最终判定：**GO（v1 骨架范围内验收通过）**。
真实仓库正式 JSON、现有 validator、大前端文件、SW 和生产 nginx/systemd 均不修改。
已有用户 `.gitignore` 中 `.codex-run/` 修改保留，不纳入本任务提交。
Git 交付仅限 `feat/import-module`，普通 push；远端实际结果以最终回复及 Git 为准。

明确未做：paper 文本解析、题目编辑、覆盖率 override、多文件扩库事务、OCR、其他学科 mappings 扩展、生产 gate/systemd/独立 origin 配置、VPS 发布与切流。
apply 只更新独立工作副本，不会自动上线数据。以上属于已审核收窄范围，未伪称完整 v1 已完成。
