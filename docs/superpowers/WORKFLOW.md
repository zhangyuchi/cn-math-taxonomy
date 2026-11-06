# 本地开发与上游同步（少冲突）

- **日期**：2026-11-06（Asia/Shanghai）
- **开发机**：horndalf-aipower → `~/Projects/github.com/cn-math-taxonomy`
- **线上现状**：2026-11-06 开工SSH核实仍由工作树服务；本轮执行current切流，最终现场结果以完整验收记录为准。
- **发布目标**：`/srv/cn-math-taxonomy/current`（本轮实施，含健康检查与失败自动回滚）。

## 远程

| remote | 指向 | 用途 |
|---|---|---|
| `upstream` | `xiaoyang111222/cn-math-taxonomy` | 只拉，跟上游 |
| `origin` | `zhangyuchi/cn-math-taxonomy`（fork） | 推送本地分支与部署标签 |

## 分支约定

1. **`main`**：尽量与 `upstream/main` 快进对齐；不在上面堆功能提交。
2. **功能分支** `feat/<name>`：从最新 `main` 拉出（例：`feat/import-module`）。
3. **部署标签** `deploy/qqvps-YYYYMMDD`：打在准备上线的提交上，VPS 只按标签/提交发布。

上游很勤更新数据和页面。本地改动优先落在**上游不太会动的路径**，减少和 `data/*.json`、前端页面撞车：

- 允许长期叠加：`docs/superpowers/`、`deploy/`、导入模块新目录（如规格中的 `import-*` / `data/imports/`）
- 尽量不要为了功能直接改正式库大文件；扩库走导入草稿 + 门闸后再 merge

## 日常同步（开发机）

```bash
git fetch upstream
git checkout main
git merge --ff-only upstream/main   # 不能快进时再决定 merge 还是 rebase
git push origin main

git checkout feat/你的功能
git rebase main                     # 或 merge main；团队统一用一种
```

冲突时优先保留上游对 `data/`、现有页面的修改，再把你的功能重新接到新目录或补丁上。

## 发布到 qqvps-cd

在开发机测试通过并推送明确提交；VPS使用独立bare mirror按完整SHA发布。运行 `deploy/publish-to-qqvps.sh <SHA>`，先预检再切current+重启，HTTP/正文健康失败自动回滚；首次迁移连同旧服务配置与current存在性恢复。详见[部署说明](../../deploy/README.md)。
共享nginx继续经gate代理127.0.0.1:8000；本轮不替换同机听写、江山、gate配置。发布记录见[完整验收](reviews/2026-11-06-import-module-completion-review.md)。

## 导入工作区与发布所有权

- Git静态release、独立import-current代码指针与持久IMPORT_WORKSPACE相互分离。工作区包含正式数据副本与data/imports，不能放在静态根/current/releases下。
- apply只更新工作区。数据上线需显式导出审阅后的正式JSON到Git交付分支，原validate/回归通过、提交后另发release；绝不复制imports/source/token。
- API/review位于独立loopback8765+token/session；默认SSH转发到开发机独立端口，避开站点SW origin。静态release排除审核台与导入入口。重OCR不在VPS默认启用。
- 本轮授权完整v1、OCR接口验收、生产切流及普通push origin/feat/import-module；上一轮“只骨架、不切流”属于历史范围。不得force-push或改坏同机其他站。
- 上游validator仍是最终数据契约；导入不替代历史build。重放build前先合齐导入正式结果，禁止与apply并发写同一workspace。
