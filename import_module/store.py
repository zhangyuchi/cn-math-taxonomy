"""job 持久化、审批与互斥；所有路径均属于独立 workspace。"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

CODE_ROOT = Path(__file__).resolve().parents[1]
SUBJECTS = json.loads((Path(__file__).with_name('subjects.json')).read_text())
JOB_RE = re.compile(r'^\d{8}-\d{6}-[a-f0-9]{8}$')
DRAFT_NAMES = ('topics', 'dependencies', 'questions', 'mappings', 'standards_suggestions')
MAX_SOURCE_BYTES = 1024 * 1024


class ImportErrorResponse(Exception):
    """将明确的业务错误映射到 API 状态及 CLI 退出码。"""

    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.status = status


def now():
    """生成带 UTC+8 偏移的上海时间，不依赖系统时区。"""
    return datetime.now(timezone(timedelta(hours=8)))


def read_json(path):
    """读取 UTF-8 JSON，错误交由调用边界显式处理。"""
    return json.loads(Path(path).read_text(encoding='utf-8'))


def json_bytes(value):
    """为快照和原子落盘提供一致的 JSON 编码。"""
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def digest_bytes(content):
    """计算源、草稿及事务目标的内容摘要。"""
    return hashlib.sha256(content).hexdigest()


def sync_directory(path):
    """同步目录项，使原子重命名在进程/系统中断后可恢复。"""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_bytes(path, content):
    """同目录写临时文件、fsync、replace；只承诺单文件原子性。"""
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path, value):
    """原子保存单份 JSON，不隐藏 I/O 失败。"""
    atomic_bytes(path, json_bytes(value))


@contextmanager
def file_lock(path):
    """非阻塞 flock；锁文件不删除，避免出现不同 inode 的并行锁。"""
    with open(path, 'a+b') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ImportErrorResponse('BUSY', '该任务或工作区正在处理其他操作', 409) from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class Service:
    """管理独立数据工作副本，拒绝将源文件放入本仓库静态根。

    状态更新受 per-job flock 保护；worker/全库写入使用额外锁。
    本类不修改正式数据，唯一正式写入口在 apply 模块。
    """

    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        if self.workspace.is_relative_to(CODE_ROOT):
            raise ImportErrorResponse('UNSAFE_WORKSPACE', 'IMPORT_WORKSPACE 必须位于代码/静态根之外')
        self.data = self.workspace / 'data'
        if self.data.is_symlink():
            raise ImportErrorResponse('UNSAFE_WORKSPACE', '工作副本 data 不允许使用符号链接')
        for name in ('topics.json', 'dependencies.json', 'questions.json',
                     'cn-curriculum-standards.json', 'id-aliases.json'):
            path = self.data / name
            if not path.is_file() or path.is_symlink():
                raise ImportErrorResponse('WORKSPACE_NOT_READY', '请先准备独立正式数据副本：' + name)
        self.imports = self.data / 'imports'
        self.imports.mkdir(mode=0o700, exist_ok=True)
        if self.imports.is_symlink() or not self.imports.resolve().is_relative_to(self.workspace):
            raise ImportErrorResponse('UNSAFE_WORKSPACE', 'imports 不允许使用符号链接')
        (self.imports / '_queue').mkdir(mode=0o700, exist_ok=True)
        mappings = self.data / 'mappings'
        mappings.mkdir(exist_ok=True)
        if mappings.is_symlink():
            raise ImportErrorResponse('UNSAFE_WORKSPACE', 'mappings 不允许使用符号链接')

    def job_dir(self, job_id):
        """解析已有 jobId，拒绝路径遍历与符号链接目录。"""
        if not isinstance(job_id, str) or not JOB_RE.fullmatch(job_id):
            raise ImportErrorResponse('INVALID_JOB_ID', 'jobId 格式不合法', 400)
        path = self.imports / job_id
        if not path.is_dir():
            raise ImportErrorResponse('NOT_FOUND', '任务不存在', 404)
        if path.is_symlink():
            raise ImportErrorResponse('INVALID_PATH', '不允许链接到其他 job', 400)
        return path

    def get(self, job_id):
        """读取完整的原子 meta 快照。"""
        return read_json(self.job_dir(job_id) / 'meta.json')

    def save(self, meta):
        """持久化状态；调用方须持有 per-job 锁。"""
        meta['updatedAt'] = now().isoformat(timespec='seconds')
        atomic_json(self.job_dir(meta['jobId']) / 'meta.json', meta)

    def list_jobs(self, status=None, kind=None, limit=100):
        """按时间逆序列出已完成创建的任务，不暴露 staging 目录。"""
        results = []
        for directory in sorted(self.imports.iterdir(), reverse=True):
            if JOB_RE.fullmatch(directory.name):
                meta = self.get(directory.name)
                if (not status or meta['status'] == status) and (not kind or meta['type'] == kind):
                    results.append(meta)
                    if len(results) >= limit:
                        break
        return results

    def create_job(self, kind, filename, content, params):
        """先落完整 job 再原子发布目录；队列指针丢失可由 worker 恢复。

        输入元数据只接受约定字段，审核标志由服务器推导；原始字节只写一次。
        job 创建不修改任何正式库文件。
        """
        if kind not in ('textbook', 'paper'):
            raise ImportErrorResponse('INVALID_TYPE', '类型必须为 textbook 或 paper', 400)
        prefix = params.get('subjectPrefix')
        if prefix not in SUBJECTS:
            raise ImportErrorResponse('INVALID_PREFIX', '学科前缀不在现库配置中', 400)
        mode = params.get('mode')
        if kind == 'textbook' and mode not in ('expand', 'mappings_only'):
            raise ImportErrorResponse('MODE_REQUIRED', '必须显式指定 expand 或 mappings_only', 400)
        if mode == 'mappings_only' and prefix not in ('cnm', 'cne'):
            raise ImportErrorResponse('MAPPINGS_SUBJECT_UNSUPPORTED', '现有 validator 仅支持数学和英语 mappings')
        suffix = Path(filename).suffix.lower()
        if suffix not in ('.md', '.txt', '.csv', '.pdf'):
            raise ImportErrorResponse('FORMAT_UNSUPPORTED', '请提供 MD/TXT/CSV；PDF 需后续 OCR')
        if not content or len(content) > MAX_SOURCE_BYTES:
            raise ImportErrorResponse('SOURCE_SIZE', '源文件必须为 1 字节至 1 MiB', 413)
        stamp = now()
        job_id = stamp.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
        staging = self.imports / ('_staging-' + job_id)
        staging.mkdir(mode=0o700)
        for name in ('source', 'draft', 'reports', 'review'):
            (staging / name).mkdir(mode=0o700)
        source_path = 'source/original' + suffix
        atomic_bytes(staging / source_path, content)
        auto = params.get('autoApplyMappings', True)
        if not isinstance(auto, bool):
            raise ImportErrorResponse('INVALID_FLAG', 'autoApplyMappings 必须为布尔值', 400)
        meta = dict(jobId=job_id, type=kind, mode=mode if kind == 'textbook' else 'expand',
                    subjectPrefix=prefix, edition=params.get('edition'), status='queued',
                    createdAt=stamp.isoformat(timespec='seconds'), updatedAt=stamp.isoformat(timespec='seconds'),
                    createdBy='ops', flags=dict(autoApplyMappings=auto,
                    requiresHumanReview=kind != 'textbook' or mode != 'mappings_only'),
                    sourceFiles=[source_path], sourceSha256=digest_bytes(content), error=None,
                    parameters={key: params[key] for key in ('grade', 'term', 'domain', 'label', 'source', 'ocrPages') if key in params})
        atomic_json(staging / 'meta.json', meta)
        for name in DRAFT_NAMES:
            atomic_json(staging / 'draft' / (name + '.json'), {} if name == 'mappings' else [])
        for name in ('validate', 'verify', 'parse', 'coverage'):
            atomic_json(staging / 'reports' / (name + '.json'), {'status': 'pending'})
        os.rename(staging, self.imports / job_id)
        sync_directory(self.imports)
        atomic_json(self.imports / '_queue' / job_id, {'jobId': job_id})
        return meta

    def source(self, job_id):
        """核对原始字节摘要，防止 requeue 静默替换审计输入。"""
        meta = self.get(job_id)
        content = self.read_file(job_id, meta['sourceFiles'][0])
        if digest_bytes(content) != meta['sourceSha256']:
            raise ImportErrorResponse('SOURCE_CHANGED', '源文件已变化，请创建新 job', 409)
        return content

    def read_file(self, job_id, relative):
        """只读 job 内普通文件；拒绝绝对路径、.. 和符号链接越界。"""
        directory = self.job_dir(job_id)
        path = Path(relative)
        if path.parts and path.parts[0] == 'draft':
            from .revision import pending
            if pending(self, job_id):
                raise ImportErrorResponse('REVISION_PENDING', '草稿修订待恢复，暂不可读', 409)
        target = (directory / path).resolve()
        if path.is_absolute() or '..' in path.parts or not target.is_relative_to(directory):
            raise ImportErrorResponse('INVALID_PATH', '文件必须位于当前 job 内', 400)
        if not target.is_file():
            raise ImportErrorResponse('NOT_FOUND', '文件不存在', 404)
        return target.read_bytes()

    def cleanup_draft_temps(self, job_id):
        """显式恢复/重解析在全库→job锁内清理本模块孤儿临时文件。

        只认atomic_bytes的精确文件名前缀、8字符随机后缀及普通文件；先检查
        全部目录项，再清理。未知文件、目录或符号链接仍失败关闭，GET不调用。
        """
        directory = self.job_dir(job_id) / 'draft'
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.job_dir(job_id)):
            raise ImportErrorResponse('INVALID_PATH', '草稿目录不允许符号链接', 400)
        expected = {name+'.json' for name in DRAFT_NAMES}
        pattern = r'\.(?:' + '|'.join(DRAFT_NAMES) + r')\.json-[a-z0-9_]{8}'
        owned = []
        for path in directory.iterdir():
            if path.name not in expected and not re.fullmatch(pattern, path.name):
                raise ImportErrorResponse('INVALID_DRAFT', '草稿含未知文件，停止恢复')
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ImportErrorResponse('INVALID_PATH', '草稿和孤儿临时文件必须为普通文件', 400)
            if path.name not in expected:
                owned.append(path)
        for path in owned:
            path.unlink()
        if owned:
            sync_directory(directory)

    def _draft_files(self, job_id):
        """读取所有约定草稿文件；缺失或未知 JSON 文件不能被静默忽略。"""
        directory = self.job_dir(job_id) / 'draft'
        expected = {name + '.json' for name in DRAFT_NAMES}
        if {p.name for p in directory.iterdir()} != expected:
            raise ImportErrorResponse('INVALID_DRAFT', '草稿文件集合不符合约定')
        for path in directory.iterdir():
            if path.is_symlink() or not path.resolve().is_relative_to(self.job_dir(job_id)):
                raise ImportErrorResponse('INVALID_PATH', '草稿不允许符号链接', 400)
        return {name: json.loads((directory / (name + '.json')).read_bytes()) for name in DRAFT_NAMES}

    def draft(self, job_id):
        """拒绝读取未完成修订中的混合五文件；GET不执行恢复。"""
        from .revision import pending
        if pending(self, job_id):
            raise ImportErrorResponse('REVISION_PENDING', '草稿修订待恢复，请先运行worker', 409)
        return self._draft_files(job_id)

    def revise(self, job_id, draft, expected_digest, comment=''):
        """审核者通过完整摘要修订草稿，正式数据不变，审批失效。"""
        from .revision import revise
        return revise(self, job_id, draft, expected_digest, comment)

    def draft_digest(self, job_id):
        """将完整 draft 内容绑定到一次审批，不使用客户端提供的摘要。"""
        return digest_bytes(json_bytes(self.draft(job_id)))

    def mappings_only(self, job_id):
        """按实际内容判断自动门闸，禁止混入新点、边、题或课标修改。"""
        meta, draft = self.get(job_id), self.draft(job_id)
        return (meta['type'] == 'textbook' and meta['mode'] == 'mappings_only'
                and all(draft[name] == [] for name in DRAFT_NAMES if name != 'mappings')
                and isinstance(draft['mappings'], dict) and bool(draft['mappings']))

    def approve(self, job_id, comment='', automatic=False, expected_digest=None):
        """在任务锁内绑定审批与草稿；自动审批另须实际纯 mappings 且校验通过。"""
        with file_lock(self.job_dir(job_id) / '.lock'):
            return self._approve_locked(job_id, comment, automatic, expected_digest)

    def _approve_locked(self, job_id, comment='', automatic=False, expected_digest=None):
        """worker 已持有任务锁时复用相同门闸，不重复获取 flock。"""
        meta = self.get(job_id)
        if automatic and (not self.mappings_only(job_id) or not meta['flags']['autoApplyMappings']
                          or not read_json(self.job_dir(job_id) / 'reports/validate.json').get('ok')):
            raise ImportErrorResponse('REVIEW_REQUIRED', '仅校验成功的纯 mappings 可自动审批', 409)
        allowed = ('running',) if automatic else ('needs_review',)
        if meta['status'] not in allowed:
            raise ImportErrorResponse('INVALID_STATE', '当前状态不允许审批', 409)
        self.source(job_id)
        current_digest = self.draft_digest(job_id)
        if expected_digest is not None and expected_digest != current_digest:
            raise ImportErrorResponse('DRAFT_CHANGED', '审核所见草稿已过期，请重新读取', 409)
        decision = dict(decision='approve', actor='system:auto-mappings' if automatic else 'ops',
                        comment=comment, at=now().isoformat(timespec='seconds'), draftSha256=current_digest)
        atomic_json(self.job_dir(job_id) / 'review/decision.json', decision)
        meta['status'], meta['error'] = 'approved', None
        self.save(meta)
        return meta

    def reject(self, job_id, reason):
        """驳回只适用于待审 job，必须保留非空理由。"""
        if not isinstance(reason, str) or not reason.strip():
            raise ImportErrorResponse('REASON_REQUIRED', '驳回原因不能为空', 400)
        with file_lock(self.job_dir(job_id) / '.lock'):
            meta = self.get(job_id)
            if meta['status'] != 'needs_review':
                raise ImportErrorResponse('INVALID_STATE', '只有待审任务可驳回', 409)
            atomic_json(self.job_dir(job_id) / 'review/decision.json',
                        dict(decision='reject', actor='ops', reason=reason, at=now().isoformat(timespec='seconds')))
            meta['status'] = 'rejected'
            self.save(meta)
            return meta

    def requeue(self, job_id):
        """仅重试同一不可变输入；保留上次报告及决策快照以供审计。"""
        with file_lock(self.job_dir(job_id) / '.lock'):
            meta = self.get(job_id)
            if meta['status'] not in ('failed', 'rejected'):
                raise ImportErrorResponse('INVALID_STATE', '仅失败或驳回的任务可重试', 409)
            self.source(job_id)
            directory = self.job_dir(job_id)
            attempt = directory / 'review' / ('attempt-' + uuid.uuid4().hex[:8])
            attempt.mkdir()
            for path in list((directory / 'reports').glob('*.json')) + list((directory / 'review').glob('*.json')):
                atomic_bytes(attempt / path.name, path.read_bytes())
            decision = directory / 'review/decision.json'
            if decision.exists():
                decision.unlink()
            meta['status'], meta['error'] = 'queued', None
            self.save(meta)
            atomic_json(self.imports / '_queue' / job_id, {'jobId': job_id})
            return meta

    def _diff_locked(self, job_id, draft=None):
        """返回草稿与当前正式数据的只读对比，不生成新的解析结果。"""
        draft = self.draft(job_id) if draft is None else draft
        result = {}
        for name in ('topics', 'dependencies', 'questions'):
            current = read_json(self.data / (name + '.json'))
            if name == 'dependencies':
                result[name] = {'count': len(draft[name]), 'after': draft[name]}
            else:
                index = {row['id']: row for row in current}
                result[name] = [{'id': row.get('id'), 'before': index.get(row.get('id')), 'after': row} for row in draft[name]]
        result['mappings'] = {'before': None, 'after': draft['mappings']}
        if draft['mappings']:
            # 延迟导入避免持久化层与校验适配初始化循环，展示与 apply 相同的合并结果。
            from .validation import mapping_target, merged_mapping
            try:
                path = self.data / mapping_target(self.get(job_id), draft['mappings'])
                if path.is_symlink():
                    raise ImportErrorResponse('INVALID_PATH', '正式 mappings 不允许符号链接')
                before = read_json(path) if path.exists() else None
                result['mappings'] = {'before': before, 'after': merged_mapping(before, draft['mappings'])}
            except ImportErrorResponse as exc:
                result['mappings']['error'] = {'code': exc.code, 'error': str(exc)}
        return result

    def diff(self, job_id):
        """全库/任务锁保护正式快照，只读路径不修复正式数据。"""
        from .transactions import assert_clean
        with file_lock(self.imports / '_apply.lock'):
            assert_clean(self)
            with file_lock(self.job_dir(job_id) / '.lock'):
                return self._diff_locked(job_id)

    def snapshot(self, job_id):
        """返回同一锁内的meta/draft/diff/reports/摘要，供审核版本绑定。"""
        from .transactions import assert_clean
        with file_lock(self.imports / '_apply.lock'):
            assert_clean(self)
            with file_lock(self.job_dir(job_id) / '.lock'):
                meta, draft = self.get(job_id), self.draft(job_id)
                meta['draft'], meta['draftSha256'] = draft, digest_bytes(json_bytes(draft))
                meta['diff'] = self._diff_locked(job_id, draft)
                meta['reports'] = {p.stem: read_json(p) for p in (self.job_dir(job_id) / 'reports').glob('*.json')}
                original = self.read_file(job_id, meta['sourceFiles'][0])
                if meta['sourceFiles'][0].endswith('.pdf'):
                    normalized = self.job_dir(job_id) / 'source/normalized.md'
                    meta['sourcePreview'] = normalized.read_text(encoding='utf-8')[:65536] if normalized.exists() else 'PDF原文已保留；暂无成功OCR文本'
                else:
                    meta['sourcePreview'] = original[:65536].decode('utf-8', errors='replace')
                return meta
