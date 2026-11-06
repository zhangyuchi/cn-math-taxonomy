# 本地开发与上游同步（少冲突）

- **日期**：2026-11-06（Asia/Shanghai）
- **开发机**：horndalf-aipower → `~/Projects/github.com/cn-math-taxonomy`
- **线上现状**：按 2026-11-06 任务简报，qqvps-cd 仍由工作树 + `python -m http.server` 服务；本次未连接 VPS 复核。
- **发布目标**：`/srv/cn-math-taxonomy/current`（目录已准备，尚未切流；切换需独立验收）。

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

1. 在开发机：功能自测通过 → 打 `deploy/qqvps-…` 标签 → `git push origin main --tags`（或推送该提交）。
2. 在 VPS：跑 `deploy/publish-to-qqvps.sh`（见仓库 `deploy/`），发布到 `/srv/cn-math-taxonomy/releases/<id>` 并切换 `current`。
3. nginx / systemd 配置放在机器上（仓库里只留示例），避免和服务端私有路径绑死在上游树里。

静态站阶段仍可用现有 gate + `/k12-knowledge-graph/`；导入 API/worker 上线后再加独立 unit，重活优先放开发机。

## 导入工作区与发布的所有权

- 代码属于 Git release；导入运行状态属于独立持久 `IMPORT_WORKSPACE`，目录内包含正式数据副本及 `data/imports/`，不得放到静态根、`current` 或 `releases/` 下。
- `apply` 只更新该工作区，不触发站点发布。需将经过审阅的正式数据另行交付并发布；不得让 `git archive` 覆盖运行状态。
- API 与审核台使用独立 loopback 端口，远程运维通过 SSH 转发；生产要求 gate + TLS 与独立 origin，避免现有 SW 缓存 job、原文和审核请求。
- 本次只提交并推送 `feat/import-module`，不执行 main 同步、打部署标签、线上切流或生产配置修改。
- 当前发布脚本在重启前切换 `current`，没有健康检查及失败回滚；使用前必须在独立发布任务中完善和验收，不将示例视为生产保证。
