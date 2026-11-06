# qqvps-cd 发布

1. 开发机推送 fork 上的标签或提交。
2. VPS 上的镜像仓配置 `origin` 指向 fork，并 `git fetch`。
3. `sudo mkdir -p /srv/cn-math-taxonomy/releases && sudo chown -R ubuntu:ubuntu /srv/cn-math-taxonomy`
4. 用 `deploy/systemd/cn-math.service.example` 替换/覆盖现有 unit（目录改为 `/srv/.../current`）。
5. `./deploy/publish-to-qqvps.sh deploy/qqvps-YYYYMMDD`

业务代码与 nginx/systemd 分离，上游更新时一般不会碰到这些文件。
