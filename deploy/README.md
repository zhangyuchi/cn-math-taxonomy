# qqvps-cd发布与导入服务

静态站使用 `/srv/cn-math-taxonomy/current`；导入服务代码使用独立 `import-current`，数据使用私有 `import-workspace`。本轮现场切流及回滚证据见[验收记录](../docs/superpowers/reviews/2026-11-06-import-module-completion-review.md)。

## 静态发布

在qqvps-cd以ubuntu运行，要求Python3.12+、git、curl/systemd、可非交互sudo。镜像与旧线上工作树分离，避免覆盖旧工作树的未跟踪文档：

```bash
mkdir -p /srv/cn-math-taxonomy/releases
# 首次由运维准备bare mirror；已有则只fetch，不覆盖工作树
# git clone --mirror https://github.com/zhangyuchi/cn-math-taxonomy.git /srv/cn-math-taxonomy/repository.git
REPO_MIRROR=/srv/cn-math-taxonomy/repository.git ./deploy/publish-to-qqvps.sh <完整SHA>
```

流程：独占 `.publish.lock` → fetch/精确SHA archive → 排除imports/隐藏运行目录/审核台/导入代码 → 核心HTML/JS/CSS/JSON完整性 → 临时loopback端口HTTP与正文hash预检 → 保存 `publish-intent.json` → 安装专用 `cn-math.service.d/zz-release.conf` → 原子切current → 重启cn-math → 有界HTTP与内容摘要健康检查。
失败自动恢复旧current（包括原先不存在）、专用drop-in及原运行状态，并验证旧站；失败返回非零，不能把“已切symlink”当发布成功。原base unit及其他drop-ins不覆盖。
首次迁移旧站目录默认为 `/home/ubuntu/Projects/cn-math-taxonomy`；成功前也验证其正文，以证明回滚目标实际可用。
发布收到SIGTERM会进入回滚；进程异常退出遗留switching intent，在下次发布开头先恢复。保留失败release和意图供审计，不自动清理旧release。

可配置环境：`ROOT`、`REPO_MIRROR`、`LEGACY_ROOT`、`STATIC_PORT`（默认8000）、`HEALTH_TIMEOUT`（默认20秒，上限60）。`SYSTEMCTL`、`SYSTEMD_DIR`、`PUBLISH_PRIVILEGE_PREFIX`仅供本地测试替身；生产使用默认sudo/systemctl。`PUBLISH_FETCH=0`可用于本地测试，或已通过bundle核验且远端SHA一致的离线发布。不要把token放参数。
`PUBLISH_GATE_CHECK`可设为JSON argv，发布器追加新release路径执行真实gate路径检查；失败同样触发回滚。

本仓库 `deploy/check-gate.py`在受信任VPS内读取现有签名机制，只在进程内产生cookie，不读取/记录明文密码，不输出cookie/key。用本机自签名TLS检查真实nginx路径及其他站点正文：

```bash
sudo python3 deploy/check-gate.py /home/ubuntu/Projects/cn-math-taxonomy --capture
PUBLISH_GATE_CHECK='["sudo","-n","python3","/srv/cn-math-taxonomy/operations/deploy/check-gate.py"]' \
  /srv/cn-math-taxonomy/operations/deploy/publish-to-qqvps.sh <完整SHA>
```

GitHub网络慢时可从开发机生成增量 `git bundle create import.bundle feat/import-module ^upstream/main`，VPS先从已有本地仓库读入所需上游完整SHA作为镜像基础（只读旧工作树），`git bundle verify`成功后fetch bundle到镜像；核对SHA与已推送origin一致，再设置 `PUBLISH_FETCH=0` 发布。不得省略bundle先决提交检查或改写旧工作树。

`--capture`必须在切流前运行。检查 `/k12-knowledge-graph/`、图谱/诊断/档案、资产、topics正文与release一致；听写、江山、login/portal摘要与切前一致。
共享nginx/gate不需要改动；现有gate后proxy到127.0.0.1:8000保持。nginx示例仅供参考，不应整体替换同机配置。

## 独立导入服务

```bash
python3 deploy/install-import-services.py <完整SHA>
ssh -N -L 18765:127.0.0.1:8765 qqvps-cd
```

安装器在VPS执行：精确SHA archive到私有 `import-releases/<SHA>`；首次复制正式data到独立 `import-workspace/data`；不覆盖既有workspace。生成root0600 `/etc/cn-math-import.env`，token不输出。停止API/worker、切独立import-current、安装示例units、协同重启并检查未认证401/认证200；安装失败恢复旧指针/units/运行状态。
API固定loopback8765；浏览器在开发机访问 `http://127.0.0.1:18765/import-login/`。运维通过受保护的 `/etc/cn-math-import.env`管理token，不将其写入仓库/文档/命令行。API还有自身token/短期会话鉴权。
worker默认关闭重OCR，重解析/OCR在开发机；插件与协议见[模块说明](../import-module/README.md)。应用与静态发布独立，静态release排除审核台/导入运行入口，不能从站点同源访问审核服务。
独立loopback端口+SSH转发是本轮生产运维默认，不开放公网导入。今后如开放公网，须另建独立TLS origin+gate并保留API鉴权，禁止放入现有SW同源scope。仅no-store不足以隔离SW。

## 数据交付与恢复

apply不修改release、不自动更新current。需要上线工作区数据时：从workspace仅导出经审阅的正式JSON到交付分支 → 原validate/JS回归 → 审阅并提交 → 显式发布该SHA。绝不导出 `data/imports/`、原文、审批缓存或token。
workspace位于current/releases之外，不能通过Python静态服务器或nginx alias提供。历史build与导入不能同时改工作区；事务恢复方法见模块README。

发布故障查看 `publish-intent.json` 与 `journalctl -u cn-math.service`；回滚失败保留switching意图并返回失败，不继续发布。恢复时先排除磁盘/systemd问题，再重跑发布器，由其恢复旧状态后重新发布。导入故障查看 `journalctl -u import-api -u import-worker` 及私有job/reports。

## 可重复验证

```bash
python3 -m unittest discover -s tests -p 'test_publish.py' -v
python3 import-module/smoke.py
```

发布测试使用真实git archive/HTTP与受控systemd替身，覆盖预检失败、首次迁移失败恢复current不存在/原服务、后续失败恢复旧current/drop-in。生产还需本轮现场HTTP/浏览器证据，不能仅凭这些测试宣称已切流。
