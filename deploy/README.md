# qqvps-cd 发布

以下为目标发布流程，并非已切流现状。按 2026-11-06 任务简报，线上仍使用工作树的 `python -m http.server`；本次未连接 VPS 验证或执行下列步骤。

1. 开发机推送 fork 上的标签或提交。
2. VPS 上的镜像仓配置 `origin` 指向 fork，并 `git fetch`。
3. `sudo mkdir -p /srv/cn-math-taxonomy/releases && sudo chown -R ubuntu:ubuntu /srv/cn-math-taxonomy`
4. 用 `deploy/systemd/cn-math.service.example` 替换/覆盖现有 unit（目录改为 `/srv/.../current`）。
5. `./deploy/publish-to-qqvps.sh deploy/qqvps-YYYYMMDD`

业务代码与 nginx/systemd 分离，上游更新时一般不会碰到这些文件。

发布脚本目前先切换 `current` 再尝试重启，没有健康检查与失败回滚；需在正式切流前独立验收。仅切换 symlink 不证明在线进程已使用新版本。

## 导入模块部署约束

导入代码可随 release 发布，`IMPORT_WORKSPACE` 必须是静态根和 releases 之外的独立持久目录（例如 `/srv/cn-math-taxonomy/import-workspace`）。其中 `data/` 为正式数据工作副本，`data/imports/` 为原文、队列和审核记录。禁止用现有 `python -m http.server` 暴露该目录。

API/worker 使用同一个 workspace；本次仅支持 loopback API 与 SSH 转发。生产要求独立 origin 的 gate + TLS，不能挂在现有站点 SW scope 下：现有 SW 会缓存同源 GET，仅设置 no-store 或排除预缓存不能保证隔离。

`apply` 不修改 release、不自动上线。工作区正式数据交付、部署健康检查、回滚、独立 systemd 单元和 gate 接入需要后续单独验收。本次不修改生产 nginx/systemd，也不执行本脚本。运行方法见 [导入模块说明](../import-module/README.md)。
