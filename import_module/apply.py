"""仅支持经过审批和完整校验的 mappings 单文件事务。"""
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


def apply_job(service, job_id):
    """按任务锁→全库锁顺序应用；任何审批/校验拒绝均不修改正式库。

    仅单文件替换具有原子性。先持久化 intent，再 replace，最后写 applied。
    中断后用目标摘要识别已提交结果；目标既非旧值也非新值则拒绝恢复。
    """
    with file_lock(service.job_dir(job_id) / '.lock'):
        meta = service.get(job_id)
        if meta['status'] == 'applied':
            return meta
        if meta['status'] != 'approved':
            raise ImportErrorResponse('REVIEW_REQUIRED', '必须先经人工批准或合法 mappings 自动批准', 409)
        with file_lock(service.imports / '_apply.lock'):
            try:
                return _apply_locked(service, meta)
            except ImportErrorResponse as exc:
                if exc.code == 'DRAFT_CHANGED':
                    # 摘要变化必须重新进入可审批状态，保留失效决策供审计。
                    decision = service.job_dir(job_id) / 'review/decision.json'
                    if decision.exists():
                        atomic_bytes(decision.with_name('stale-' + uuid.uuid4().hex[:8] + '.json'), decision.read_bytes())
                    meta['status'] = 'needs_review'
                meta['error'] = {'code': exc.code, 'error': str(exc)}
                service.save(meta)
                raise


def _apply_locked(service, meta):
    """锁内重新读取源、审批与当前正式库，不信任 flags 或旧校验成功报告。"""
    job_id = meta['jobId']
    service.source(job_id)
    decision_path = service.job_dir(job_id) / 'review/decision.json'
    if not decision_path.exists():
        raise ImportErrorResponse('REVIEW_REQUIRED', '缺少审批记录', 409)
    decision = read_json(decision_path)
    if decision.get('decision') != 'approve' or decision.get('draftSha256') != service.draft_digest(job_id):
        _retire_uncommitted_intent(service, meta, decision)
        raise ImportErrorResponse('DRAFT_CHANGED', '草稿与审批摘要不一致，须重新提交审核', 409)
    if not service.mappings_only(job_id):
        raise ImportErrorResponse('APPLY_SCOPE_UNSUPPORTED', '本次骨架仅可应用纯 mappings；新点/边/题仍需完整事务实现', 409)
    if decision.get('actor') not in ('ops', 'system:auto-mappings'):
        raise ImportErrorResponse('REVIEW_REQUIRED', '未知审批主体', 409)
    target_relative = mapping_target(meta, service.draft(job_id)['mappings'])
    target = service.data / target_relative
    intent_path = service.job_dir(job_id) / 'review/apply-intent.json'
    if intent_path.exists():
        intent = read_json(intent_path)
        if (intent.get('jobId') != job_id or intent.get('target') != target_relative
                or intent.get('draftSha256') != decision['draftSha256']):
            raise ImportErrorResponse('RECOVERY_CONFLICT', '事务意图与本次审批不一致', 409)
        actual = _target_digest(target)
        if actual == intent['afterSha256']:
            return _finish(service, meta, intent)
        if actual != intent['beforeSha256']:
            raise ImportErrorResponse('RECOVERY_CONFLICT', '目标既非事务旧值也非新值，停止恢复', 409)
    with candidate(service, job_id) as (root, relative):
        report = validate_root(root)
        report['phase'] = 'apply'
        atomic_json(service.job_dir(job_id) / 'reports/validate.json', report)
        if not report['ok']:
            raise ImportErrorResponse('VALIDATION_FAILED', '合并候选未通过完整校验，详见 reports/validate.json')
        content = (Path(root) / 'data' / relative).read_bytes()
        intent = dict(jobId=job_id, target=relative, beforeSha256=_target_digest(target),
                      afterSha256=digest_bytes(content), draftSha256=decision['draftSha256'],
                      preparedAt=now().isoformat(timespec='seconds'))
        atomic_json(intent_path, intent)
        atomic_bytes(target, content)
        return _finish(service, meta, intent)
