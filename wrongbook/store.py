"""SQLite 是错题/身份/卷面的唯一持久化权威；不写正式题库。"""
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from .common import (WrongbookError, identifier, json_text, new_private_file, now,
                     object_fields, private_path, require, subject_name, text, timestamp)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 user_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS attempts (
 user_id TEXT NOT NULL REFERENCES users(user_id), child_id TEXT NOT NULL, event_id TEXT NOT NULL,
 question_id TEXT NOT NULL, topic_id TEXT NOT NULL, subject TEXT NOT NULL, result TEXT NOT NULL,
 answered_at TEXT NOT NULL, received_at TEXT NOT NULL, mastered INTEGER NOT NULL DEFAULT 0,
 data_json TEXT NOT NULL, PRIMARY KEY(user_id,child_id,event_id));
CREATE INDEX IF NOT EXISTS attempts_list ON attempts(user_id,child_id,subject,result,mastered,answered_at DESC,event_id);
CREATE INDEX IF NOT EXISTS attempts_question ON attempts(user_id,child_id,question_id);
CREATE TABLE IF NOT EXISTS papers (
 paper_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id), child_id TEXT NOT NULL,
 data_json TEXT NOT NULL);
'''


class Service:
    """持有只读题库快照，逐操作开启有界事务，不持有全部孩子历史。

    HTTP身份从authenticate取得；管理CLI是受信任的数据库所有者。
    所有事件/卷面SQL同时绑定user_id和child_id，不能通过客户端选择他人身份。
    """
    def __init__(self, workspace, catalog_root=None, static_roots=()):
        os.umask(0o077)
        self.static_roots = tuple(static_roots)
        self.workspace = private_path(workspace, self.static_roots)
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.workspace.stat().st_mode & 0o077:
            raise WrongbookError('INVALID_PATH', '工作区必须为私有目录（0700），不自动修改已有目录权限')
        self.db_path = private_path(self.workspace / 'wrongbook.sqlite3', self.static_roots)
        if self.db_path.is_symlink():
            raise WrongbookError('INVALID_PATH', '数据库不能是符号链接')
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise WrongbookError('SCHEMA_VERSION', '数据库版本超出本代码支持范围')
            db.execute('PRAGMA journal_mode=DELETE')
            db.executescript(SCHEMA)
            db.execute('PRAGMA user_version=1')
        self.db_path.chmod(0o600)
        from .paper import Catalog
        self.catalog = Catalog(catalog_root or Path(__file__).resolve().parents[1] / 'data')

    @contextmanager
    def connect(self):
        """异常自动回滚并始终关闭连接，成功离开上下文才提交。

        DELETE/FULL保证单文件在线备份及进程中断恢复；事务锁最多等5秒。
        """
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def provision(self, user_id, token_file):
        """仅本地管理员创建身份，token明文只写独占0600文件，不进入DB/日志。"""
        identifier(user_id)
        token = secrets.token_urlsafe(32)
        path = private_path(token_file, self.static_roots)
        # 先独占文件再写DB；失败清除本次文件，不覆盖原凭据。
        with new_private_file(path, self.static_roots) as stream:
            try:
                with self.connect() as db:
                    db.execute('INSERT INTO users VALUES (?,?)', (user_id, hashlib.sha256(token.encode()).hexdigest()))
                    stream.write((token + '\n').encode()); stream.flush(); os.fsync(stream.fileno())
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        return token

    def authenticate(self, token):
        """高熵Bearer token取哈希后查用户，任何失败均为相同401。"""
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            raise WrongbookError('UNAUTHORIZED', '需要有效错题本 token', 401)
        with self.connect() as db:
            row = db.execute('SELECT user_id FROM users WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None:
            raise WrongbookError('UNAUTHORIZED', '需要有效错题本 token', 401)
        return row['user_id']

    def normalize_attempt(self, data):
        """验证逐题事件归属；忽略不了未知身份/题干字段，时间来源显式保存。"""
        required = {'eventId', 'questionId', 'topicId', 'subject', 'answeredAt', 'sourceSession', 'result'}
        object_fields(data, required | {'userAnswer', 'timeSource'}, required)
        for field in ('eventId', 'questionId', 'topicId'):
            identifier(data[field])
        subject_name(data['subject'])
        text(data['sourceSession'])
        require(data['result'] in ('correct', 'wrong', 'skip'))
        require(data.get('timeSource', 'client') in ('client', 'export'))
        answer = data.get('userAnswer')
        require(answer is None or (isinstance(answer, str) and len(answer) <= 10000))
        q = self.catalog.questions.get(data['questionId'])
        require(q is not None and q['topicId'] == data['topicId'] and self.catalog.subject(q['topicId']) == data['subject'])
        return {**data, 'answeredAt': timestamp(data['answeredAt']), 'userAnswer': answer,
                'timeSource': data.get('timeSource', 'client')}

    def record(self, user, child, body, *, export_replay=False):
        """整批先验证再原子插入，同键异内容冲突导致整批回滚。

        仅diagnoses调用export_replay：允许相同导出作答在重新导出后保留首次时间。
        每个事件独立，重复答同题须不同eventId；correct不会清除历史错题。
        """
        identifier(user); identifier(child)
        object_fields(body, {'attempts'}, {'attempts'})
        require(isinstance(body['attempts'], list) and 1 <= len(body['attempts']) <= 500)
        events = [self.normalize_attempt(a) for a in body['attempts']]
        inserted = replayed = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for data in events:
                old = db.execute('SELECT data_json FROM attempts WHERE user_id=? AND child_id=? AND event_id=?',
                                 (user, child, data['eventId'])).fetchone()
                if old:
                    previous = json.loads(old['data_json'])
                    comparison = dict(data)
                    if export_replay and previous['timeSource'] == data['timeSource'] == 'export':
                        comparison['answeredAt'] = previous['answeredAt']
                    if json_text(previous) != json_text(comparison):
                        raise WrongbookError('EVENT_CONFLICT', 'eventId 已存在且作答内容不同', 409)
                    replayed += 1
                else:
                    db.execute('INSERT INTO attempts(user_id,child_id,event_id,question_id,topic_id,subject,result,answered_at,received_at,data_json) VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (user, child, data['eventId'], data['questionId'], data['topicId'], data['subject'],
                                data['result'], data['answeredAt'], now(), json_text(data)))
                    inserted += 1
        return dict(inserted=inserted, replayed=replayed)

    def list_attempts(self, user, child, *, status='active', subject=None, limit=100, offset=0):
        """按稳定UTC时间/事件键分页，只列错题事件；all含已掌握但不含correct。"""
        identifier(user); identifier(child)
        require(status in ('active', 'mastered', 'all'))
        require(type(limit) is int and 1 <= limit <= 200 and type(offset) is int and 0 <= offset <= 2**63 - 1)
        where = "user_id=? AND child_id=? AND result IN ('wrong','skip')"
        values = [user, child]
        if subject is not None:
            subject_name(subject); where += ' AND subject=?'; values.append(subject)
        if status != 'all':
            where += ' AND mastered=?'; values.append(int(status == 'mastered'))
        with self.connect() as db:
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM attempts WHERE ' + where, values).fetchone()[0]
            rows = db.execute('SELECT data_json,received_at,mastered FROM attempts WHERE ' + where +
                              ' ORDER BY answered_at DESC,event_id ASC LIMIT ? OFFSET ?', values + [limit, offset]).fetchall()
        return dict(items=[{**json.loads(r['data_json']), 'receivedAt': r['received_at'], 'mastered': bool(r['mastered'])} for r in rows],
                    total=total, limit=limit, offset=offset)

    def mark(self, user, child, question, body):
        """修改指定孩子指定题的全部现有错题状态，后续错题仍默认未掌握。"""
        identifier(user); identifier(child); identifier(question)
        object_fields(body, {'mastered'}, {'mastered'})
        require(type(body['mastered']) is bool)
        with self.connect() as db:
            count = db.execute("UPDATE attempts SET mastered=? WHERE user_id=? AND child_id=? AND question_id=? AND result IN ('wrong','skip')",
                               (int(body['mastered']), user, child, question)).rowcount
            if not count:
                raise WrongbookError('NOT_FOUND', '当前孩子下没有该错题', 404)
        return {'updated': count}

    def delete(self, user, child, question):
        """显式删除该题所有作答事件；历史卷面快照保持原样。"""
        identifier(user); identifier(child); identifier(question)
        with self.connect() as db:
            count = db.execute('DELETE FROM attempts WHERE user_id=? AND child_id=? AND question_id=?', (user, child, question)).rowcount
            if not count:
                raise WrongbookError('NOT_FOUND', '当前孩子下没有该题', 404)
        return {'deleted': count}

    def import_diagnosis(self, user, child, body):
        """导入现有诊断v2逐题JSON，时间为导出时间且标注来源，不伪造历史逐题时间。"""
        object_fields(body, {'sourceSession', 'subject', 'diagnosis'}, {'sourceSession', 'subject', 'diagnosis'})
        session = text(body['sourceSession']); subject = subject_name(body['subject'])
        d = body['diagnosis']
        require(isinstance(d, dict) and d.get('tool') == 'cn-math-taxonomy/diagnose' and type(d.get('version')) is int and d['version'] == 2)
        require(isinstance(d.get('answers'), list) and 1 <= len(d['answers']) <= 500)
        at = timestamp(d.get('generatedAt'))
        events, orders = [], set()
        for a in d['answers']:
            require(isinstance(a, dict))
            order = a.get('order')
            require(type(order) is int and 1 <= order <= 500 and order not in orders)
            orders.add(order)
            events.append(dict(eventId=hashlib.sha256(session.encode()).hexdigest() + '_' + str(order),
                               questionId=a.get('questionId'), topicId=a.get('topicId'), subject=subject,
                               answeredAt=at, sourceSession=session, result=a.get('result'),
                               userAnswer=a.get('userAnswer'), timeSource='export'))
        return self.record(user, child, {'attempts': events}, export_replay=True)

    def create_paper(self, user, child, body):
        """在同一事务快照读取错题与已见题，保存卷面后才返回可复用paperId。"""
        from .paper import assemble
        identifier(user); identifier(child)
        object_fields(body, {'subject', 'count', 'wrongCount'}, {'subject'})
        subject = subject_name(body['subject'])
        count = body.get('count', 20); wrong_count = body.get('wrongCount', (count + 1) // 2 if type(count) is int else 0)
        require(type(count) is int and 1 <= count <= 100 and type(wrong_count) is int and 1 <= wrong_count <= count)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            wrongs = db.execute("SELECT question_id,topic_id,MAX(answered_at) AS last_at FROM attempts WHERE user_id=? AND child_id=? AND subject=? AND mastered=0 AND result IN ('wrong','skip') GROUP BY question_id,topic_id ORDER BY last_at DESC,question_id ASC", (user, child, subject))
            # 只保留题库规模内的候选，不载入任意数量的重复作答历史。
            wrong_questions, missing, topics = [], [], set()
            for row in wrongs:
                # 原题失效仍保留该知识点的复习关联，不能静默丢掉其可用新题。
                topics.add(row['topic_id'])
                q = self.catalog.questions.get(row['question_id'])
                if q and q['topicId'] == row['topic_id'] and self.catalog.subject(q['topicId']) == subject and self.catalog.usable(q):
                    wrong_questions.append(q)
                else:
                    missing.append(row['question_id'])
            if not wrong_questions:
                raise WrongbookError('NO_WRONG_QUESTIONS', '没有当前题库可用的未掌握错题', 422)
            seen = {r[0] for r in db.execute('SELECT DISTINCT question_id FROM attempts WHERE user_id=? AND child_id=?', (user, child))}
            p = assemble(self.catalog, wrong_questions, topics, seen, subject, count, wrong_count, missing)
            p.update(paperId=uuid.uuid4().hex, createdAt=now(), catalogSha256=self.catalog.digest)
            db.execute('INSERT INTO papers VALUES (?,?,?,?)', (p['paperId'], user, child, json_text(p)))
        return p

    def get_paper(self, user, child, paper):
        """限所属用户/孩子读取固定卷面，题库更新不修改历史快照。"""
        identifier(user); identifier(child); identifier(paper)
        with self.connect() as db:
            row = db.execute('SELECT data_json FROM papers WHERE user_id=? AND child_id=? AND paper_id=?', (user, child, paper)).fetchone()
        if row is None:
            raise WrongbookError('NOT_FOUND', '当前孩子下没有该试卷', 404)
        return json.loads(row['data_json'])

    def backup(self, output):
        """以SQLite在线备份API生成一致私有副本，已存在目标绝不覆盖。

        10秒上限到达则中止，失败删除不完整副本；不能直接复制运行中的数据库。
        """
        path = private_path(output, self.static_roots)
        with new_private_file(path, self.static_roots):
            pass
        deadline = time.monotonic() + 10
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise WrongbookError('BUSY', '备份超时，请稍后重试', 503)
        try:
            with closing(sqlite3.connect(self.db_path)) as src, closing(sqlite3.connect(path)) as dst:
                src.backup(dst, pages=256, progress=progress)
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {'backup': str(path)}
