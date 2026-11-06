"""经审批和完整校验后应用；兼容单文件mappings与可恢复多文件扩库事务。"""
from pathlib import Path
import re
import uuid

from .store import (ImportErrorResponse, atomic_bytes, atomic_json, digest_bytes,
                    file_lock, now, read_json, sync_directory)
from .validation import candidate, mapping_target, validate_root


def _target_digest(path):
    """用 None 表示目标尚不存在，拒绝通过目标符号链接写入。"""
    if path.is_symlink():
        raise ImportErrorResponse('INVALID_PATH', '正式 mappings 文件不允许符号链接')
    return digest_bytes(path.read_bytes()) if path.exists() else None


def _finish(service, meta, intent):
    """替换完成后保存状态；若保存失败，保留 intent 供下次调用恢复。"""
    meta['status'], meta['error'] = 'applied', None
    service.save(meta)
    intent['committedAt'] = now().isoformat(timespec='seconds')
    atomic_json(service.job_dir(meta['jobId']) / 'review/apply-intent.json', intent)
    return meta


def _retire_uncommitted_intent(service, meta, decision):
    """草稿失效时仅归档尚未写入的 intent，避免新审批被旧摘要永久阻塞。

    调用方持有全库锁。若旧写入已发生或目标被其他写入改变，不能把事务
    当成未提交重新审批，保留原记录并要求先恢复原草稿/状态。
    """
    path = service.job_dir(meta['jobId']) / 'review/apply-intent.json'
    if not path.exists():
        return
    intent = read_json(path)
    relative = intent.get('target')
    if (intent.get('jobId') != meta['jobId'] or intent.get('draftSha256') != decision.get('draftSha256')
            or not isinstance(relative, str) or not re.fullmatch(r'mappings/[a-z][a-z0-9_]*\.json', relative)):
        raise ImportErrorResponse('RECOVERY_CONFLICT', '旧事务记录不合法，停止重新审批', 409)
    actual = _target_digest(service.data / relative)
    if actual == intent['afterSha256']:
        raise ImportErrorResponse('RECOVERY_CONFLICT', '原审批已写入；请恢复原草稿并完成状态恢复，修订另建 job', 409)
    if actual != intent['beforeSha256']:
        raise ImportErrorResponse('RECOVERY_CONFLICT', '旧事务目标已有其他改动，停止重新审批', 409)
    atomic_json(path.with_name('stale-intent-' + uuid.uuid4().hex[:8] + '.json'), intent)
    path.unlink()
    sync_directory(path.parent)


def _recover_legacy(service, job_id, intent, keep_unwritten=False):
    """兼容骨架单文件intent；新摘要完成状态，旧摘要安全归档，第三值停止。"""
    relative = intent.get('target')
    if (intent.get('jobId') != job_id or not isinstance(relative, str)
            or not re.fullmatch(r'mappings/[a-z][a-z0-9_]*\.json', relative)):
        raise ImportErrorResponse('RECOVERY_CONFLICT', '历史单文件事务记录不合法', 409)
    actual = _target_digest(service.data / relative)
    if actual == intent['afterSha256']:
        decision = read_json(service.job_dir(job_id) / 'review/decision.json')
        if decision.get('draftSha256') != intent.get('draftSha256') or service.draft_digest(job_id) != intent.get('draftSha256'):
            raise ImportErrorResponse('RECOVERY_CONFLICT', '原审批已写入但草稿变化，请先恢复原草稿', 409)
        return _finish(service, service.get(job_id), intent)
    if actual != intent['beforeSha256']:
        raise ImportErrorResponse('RECOVERY_CONFLICT', '历史事务目标出现第三种摘要', 409)
    if not keep_unwritten:
        path = service.job_dir(job_id) / 'review/apply-intent.json'
        atomic_json(path.with_name('aborted-' + uuid.uuid4().hex + '.json'), intent)
        path.unlink()
        sync_directory(path.parent)


def recover_all_locked(service, current_job=None):
    """显式恢复入口；调用方只持全库锁，逐个获取job锁避免反向加锁。"""
    from .transactions import unfinished, recover
    from .revision import pending, recover_locked
    from .store import JOB_RE
    for directory in sorted(service.imports.iterdir()):
        if JOB_RE.fullmatch(directory.name) and pending(service, directory.name):
            with file_lock(directory / '.lock'):
                recover_locked(service, directory.name)
    for job_id, intent in unfinished(service):
        with file_lock(service.job_dir(job_id) / '.lock'):
            if intent.get('version') == 2:
                recover(service, job_id, intent)
            else:
                _recover_legacy(service, job_id, intent, keep_unwritten=job_id == current_job)


def recover_pending(service):
    """worker启动/运维显式恢复所有事务；GET不调用此写入入口。"""
    with file_lock(service.imports / '_apply.lock'):
        recover_all_locked(service)


def apply_job(service, job_id):
    """全库锁→恢复历史事务→任务锁→核准快照→验证→提交。

    新点/题始终要求人工审批；单文件mappings沿用兼容路径，其他内容使用
    持久prepared/committed多文件事务。恢复歧义阻止全部新apply。
    """
    with file_lock(service.imports / '_apply.lock'):
        recover_all_locked(service, job_id)
        with file_lock(service.job_dir(job_id) / '.lock'):
            meta = service.get(job_id)
            if meta['status'] == 'applied':
                return meta
            if meta['status'] != 'approved':
                raise ImportErrorResponse('REVIEW_REQUIRED', '必须先经人工批准或合法mappings自动批准', 409)
            try:
                return _apply_locked(service, meta)
            except ImportErrorResponse as exc:
                if exc.code == 'DRAFT_CHANGED':
                    decision = service.job_dir(job_id) / 'review/decision.json'
                    if decision.exists():
                        atomic_bytes(decision.with_name('stale-' + uuid.uuid4().hex[:8] + '.json'), decision.read_bytes())
                    meta['status'] = 'needs_review'
                meta['error'] = {'code': exc.code, 'error': str(exc)}
                service.save(meta)
                raise


def _apply_locked(service, meta):
    """一次读取草稿并核对审批；候选构造不重新读取另一版草稿。"""
    from .transactions import execute
    from .verification import verify_questions
    from .store import json_bytes
    job_id = meta['jobId']
    service.source(job_id)
    draft = service.draft(job_id)
    draft_hash = digest_bytes(json_bytes(draft))
    decision_path = service.job_dir(job_id) / 'review/decision.json'
    if not decision_path.exists():
        raise ImportErrorResponse('REVIEW_REQUIRED', '缺少审批记录', 409)
    decision = read_json(decision_path)
    if decision.get('decision') != 'approve' or decision.get('draftSha256') != draft_hash:
        _retire_uncommitted_intent(service, meta, decision)
        raise ImportErrorResponse('DRAFT_CHANGED', '草稿与审批摘要不一致，须重新提交审核', 409)
    pure_mapping = (meta['type'] == 'textbook' and meta['mode'] == 'mappings_only'
                    and all(draft[n] == [] for n in ('topics', 'dependencies', 'questions', 'standards_suggestions'))
                    and bool(draft['mappings']))
    if decision.get('actor') != 'ops' and not (decision.get('actor') == 'system:auto-mappings' and pure_mapping):
        raise ImportErrorResponse('REVIEW_REQUIRED', '新点/边/题必须经人工审批', 409)
    from .paper_assets import question_risks
    risks = question_risks(service, job_id, draft['questions'])
    for code in ('PAPER_ASSET_MISSING', 'PAPER_ASSET_REVIEW_REQUIRED'):
        if code in risks['riskCodes']:
            raise ImportErrorResponse(code, '题目仍含图片引用；须人工转写为完整文字等正式库支持的形式后重新审核')
    # 兼容旧单文件intent，当前草稿已核准，未写入时允许重新验证后安全重试。
    intent_path = service.job_dir(job_id) / 'review/apply-intent.json'
    if intent_path.exists():
        old = read_json(intent_path)
        if old.get('version') != 2:
            relative = mapping_target(meta, draft['mappings'])
            if old.get('target') != relative or old.get('draftSha256') != draft_hash:
                raise ImportErrorResponse('RECOVERY_CONFLICT', '历史intent与审批不一致', 409)
            if _target_digest(service.data / relative) == old['afterSha256']:
                return _finish(service, meta, old)
    with candidate(service, job_id, draft) as (root, prepared):
        verify = verify_questions(prepared['draft']['questions'])
        atomic_json(service.job_dir(job_id) / 'reports/verify.json', verify)
        if verify['mismatch']:
            raise ImportErrorResponse('VERIFY_MISMATCH', '存在核验不符的题目，须修正并重新审核')
        report = validate_root(root)
        report['phase'] = 'apply'
        atomic_json(service.job_dir(job_id) / 'reports/validate.json', report)
        if not report['ok']:
            raise ImportErrorResponse('VALIDATION_FAILED', '合并候选未通过完整校验，详见reports/validate.json')
        targets = prepared['targets']
        if not targets:
            raise ImportErrorResponse('INVALID_DRAFT', '草稿没有可应用内容')
        if pure_mapping:
            relative = targets[0]
            target = service.data / relative
            content = (Path(root) / 'data' / relative).read_bytes()
            intent = dict(jobId=job_id, target=relative, beforeSha256=_target_digest(target),
                          afterSha256=digest_bytes(content), draftSha256=draft_hash,
                          preparedAt=now().isoformat(timespec='seconds'))
            atomic_json(intent_path, intent)
            atomic_bytes(target, content)
            return _finish(service, meta, intent)
        return execute(service, meta, root, targets, draft_hash, prepared['idMap'])
