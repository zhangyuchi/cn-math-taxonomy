# 服务端永久错题本 v1

Python 3.12+ 标准库服务：SQLite 私有磁盘保存作答事件与固定卷面，Bearer token 隔离家庭，childId 区分孩子。组卷优先选未掌握错题，补同知识点及直接先修的未见题。正式题库只读；现有静态诊断无需改动。

- [使用与备份指南](../docs/superpowers/guides/2026-11-06-wrongbook-user-guide.md)
- [身份、API、组卷与持久化规格](../docs/superpowers/specs/2026-11-06-wrongbook-server.md)
- [轻量服务配置](../deploy/systemd/wrongbook-api.service.example)

```bash
export WRONGBOOK_WORKSPACE="$HOME/.local/share/cn-math-wrongbook"
python3 -m wrongbook.cli provision --user family-a --token-file "$WRONGBOOK_WORKSPACE/family-a.token"
python3 -m wrongbook.api
# 另开终端
export WRONGBOOK_TOKEN_FILE="$HOME/.local/share/cn-math-wrongbook/family-a.token"
python3 -m wrongbook.cli list --child child-a
```

默认只监听 `127.0.0.1:8766`；远程用 SSH 转发。工作区、token、备份不可进入任何公开静态根；额外静态根通过 `WRONGBOOK_STATIC_ROOTS`（冒号分隔绝对路径）声明。禁止反向代理到现有静态站的 Service Worker 同源范围。本轮交付独立服务代码和部署示例，不自动启用生产服务或备份排程。

验证：

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_wrongbook*.py' -v
```
