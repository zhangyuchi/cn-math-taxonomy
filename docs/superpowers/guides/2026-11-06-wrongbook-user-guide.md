# 使用服务端错题本与复习卷

本指南对应[错题本规格](../specs/2026-11-06-wrongbook-server.md)。v1 通过独立 CLI 使用，数据在服务器 SQLite 中永久保存；浏览器本机盲区仍按原方式运行，不会自动上传。需要 Python 3.12+，无需安装第三方包。

## 首次启动

在仓库根运行，工作区放到仓库及所有静态网站目录之外：

```bash
export WRONGBOOK_WORKSPACE="$HOME/.local/share/cn-math-wrongbook"
python3 -m wrongbook.cli provision --user family-a --token-file "$WRONGBOOK_WORKSPACE/family-a.token"
python3 -m wrongbook.api --port 8766
```

创建用户仅在服务主机的管理 CLI 执行。生成的 token 文件为0600；命令只显示路径，不显示密钥。保留这个文件，后续 CLI 读取它发送 Bearer 请求。不要复用导入模块 token，也不要把 token 贴到 URL、仓库或聊天中。

另开终端执行后续命令：

```bash
export WRONGBOOK_TOKEN_FILE="$HOME/.local/share/cn-math-wrongbook/family-a.token"
```

远程服务器使用 `ssh -N -L 18766:127.0.0.1:8766 qqvps-cd`，然后客户端 `export WRONGBOOK_URL=http://127.0.0.1:18766`。服务始终绑定 loopback；本轮不自动部署生产或变更现有 nginx/gate。qqvps 的服务示例为 [wrongbook-api.service.example](../../../deploy/systemd/wrongbook-api.service.example)。

## 从现有诊断收集错题

1. 在原诊断页完成作答，使用结果页的“导出 JSON”。
2. 选定孩子 ID。可复用本机孩子档案中的 ID，也可用稳定的 `child-a`；同一家庭持续使用同一个 ID。
3. 为该轮诊断指定稳定的来源会话名称，执行：

```bash
python3 -m wrongbook.cli import-diagnosis --child child-a --subject math --session math-20261106-01 --file /私有目录/diagnosis.json
python3 -m wrongbook.cli list --child child-a
```

同一孩子、来源会话、逐题内容重复导入不会重复计数；重新导出仅generatedAt变化时保留首次时间。同会话同序号其他内容变化返回409，应为新一轮或更正记录选择新会话。导出不含逐题作答时间，服务会标记 `timeSource=export`，使用导出时间。correct 用于排除已见题，wrong/skip 进入待复习列表；skip 表示跳过。不会把本机知识点汇总伪造成具体错题。

本地档案的切换、移动、删除不会同步改动服务端；每条 CLI 都显式指定 `--child`。清除浏览器数据也不会删除已同步的服务端错题。

## 直接录入、掌握与删除

保存以下 JSON 到私有文件 `attempts.json`，然后录入。每次新作答使用新的 `eventId`；重试同一次上传保留 eventId。

```json
{"attempts":[{"eventId":"attempt-001","questionId":"cnm_p001_q1","topicId":"cnm_p001","subject":"math","answeredAt":"2026-11-06T14:00:00+08:00","sourceSession":"practice-001","result":"wrong","userAnswer":"5"}]}
```

```bash
python3 -m wrongbook.cli record --child child-a --file /私有目录/attempts.json
python3 -m wrongbook.cli master --child child-a --question cnm_p001_q1
python3 -m wrongbook.cli list --child child-a --status mastered
python3 -m wrongbook.cli master --child child-a --question cnm_p001_q1 --undo
```

标记作用于该孩子该题现有的所有错题事件，后续再次答错会重新列入待复习；答对一题不会自动消除历史错题。

`delete` 会永久删除该孩子该题全部作答事件，包括 correct；已经保存的试卷快照仍保留原题，旧卷不受影响。

```bash
python3 -m wrongbook.cli delete --child child-a --question cnm_p001_q1
```

## 生成、打印与再测

```bash
python3 -m wrongbook.cli paper --child child-a --subject math --count 10 --wrong-count 5 --output /私有目录/review.json --html /私有目录/review.html
```

结果显示 paperId 和实际题数。默认约一半错题，后续为同知识点及直接先修的未见题；新题不足时补剩余错题，再不足则短卷并显示 shortfall。不会重复选同题，不会用其他学科或无关题补齐。

打开 HTML 使用浏览器打印；学生卷不含参考答案、孩子身份或作答原文，题图采用隔离的 SVG 图片。数学表达保留原题文字/LaTeX 源文本，不依赖公网公式排版库。JSON 的 `student` 可供其他再测端直接使用，`answerKey` 单独供家长核对。JSON 不是发给学生的无答案文件。

```bash
python3 -m wrongbook.cli get-paper --child child-a --paper PAPER_ID --output /私有目录/review-copy.json --html /私有目录/review-copy.html
```

旧卷始终可读取和打印；题库更新后，已删除或改知识点/学科的题不支持直接回录，服务会报400；这也适用于已存旧事件的原样重放，历史记录仍保留。再测时将 `student.questions` 中的 questionId/topicId 及实际判定结果写成前述 attempts 格式，使用新的 eventId、sourceSession 和实际 answeredAt，再运行 `record`。这会保存新的答题事件并影响后续未见题选择。v1 不引入新的自动判题器。

## 备份与恢复

在服务主机使用管理命令进行一致性在线备份。输出目标须在私有目录且不存在：

```bash
python3 -m wrongbook.cli backup --output "$WRONGBOOK_WORKSPACE/backup-20261106.sqlite3"
```

备份包含所有用户的 token 哈希、作答事件和试卷，按私密数据保管；明文 token 文件另行保管。运维应每日备份、至少保留7份，并保存异机加密副本。本轮交付备份命令与恢复测试，未安装生产备份排程。

恢复时先停止错题服务，把当前 `wrongbook.sqlite3` 重命名留存，复制备份为同目录的 `wrongbook.sqlite3` 并设0600，再启动匹配版本服务。SQLite DELETE journal 模式；只在服务已停止且无事务时替换数据库。查询已知孩子与 paperId 确认恢复，原 token 继续有效。不支持直接用旧版本代码打开未来 schema 的数据库。

列表可加 `--subject math --limit 100 --offset 0 --status all` 翻页。400检查字段和题库ID；401检查token文件；404表示当前用户/孩子下资源不存在；409表示幂等键内容冲突；422表示无有效待复习错题；503表示磁盘/数据库忙，保留原eventId稍后重试。不要将原文和token放入故障日志。
