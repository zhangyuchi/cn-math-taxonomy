# 本地开发与上游同步（少冲突）

- **日期**：2026-11-06（Asia/Shanghai）
- **开发机**：horndalf-aipower → `~/Projects/github.com/cn-math-taxonomy`
- **线上**：qqvps-cd → `/srv/cn-math-taxonomy/current`（勿再在 git 工作树上直接改线上）

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
