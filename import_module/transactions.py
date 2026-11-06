"""私有工作区的可恢复多文件事务；不承诺无锁读者的瞬时原子可见。"""
from pathlib import Path
import re
import uuid

from .store import (JOB_RE, ImportErrorResponse, atomic_bytes, atomic_json, digest_bytes,
                    now, read_json, sync_directory)

TARGET_RE = re.compile(r'(?:topics|dependencies|questions)\.json|mappings/[a-z][a-z0-9_]*\.json')


def target_digest(path):
    """摘要None表示未创建；正式目标禁止符号链接。"""
    if path.is_symlink():
        raise ImportErrorResponse('INVALID_PATH', '正式目标不允许符号链接')
    return digest_bytes(path.read_bytes()) if path.exists() else None


def unfinished(service):
    """只读扫描未终结intent；已applied不因后续合法写入重新判断。"""
    result = []
    for directory in sorted(service.imports.iterdir()):
        if not JOB_RE.fullmatch(directory.name):
            continue
        path = directory / 'review/apply-intent.json'
        if not path.exists() or service.get(directory.name)['status'] == 'applied':
            continue
        intent = read_json(path)
        if intent.get('state') != 'rolled_back':
            result.append((directory.name, intent))
    return result


def assert_clean(service):
    """GET/解析只拒绝混合正式快照，不在读路径偷偷修复正式数据。"""
    if unfinished(service):
        raise ImportErrorResponse('TRANSACTION_PENDING', '存在待恢复事务，请先运行worker或显式恢复apply', 409)


def _checked_entries(service, job_id, intent):
    """恢复前一次核对全部目标/备份/候选摘要；歧义时一个文件也不覆盖。"""
    directory = service.job_dir(job_id)
    if (intent.get('version') != 2 or intent.get('jobId') != job_id or
            intent.get('state') not in ('prepared', 'committed', 'rolled_back') or
            not re.fullmatch(r'review/transaction-[a-f0-9]{32}', intent.get('transactionDir', '')) or
            not isinstance(intent.get('targets'), list) or not intent['targets']):
        raise ImportErrorResponse('RECOVERY_CONFLICT', '多文件事务记录不合法', 409)
    tx = directory / intent['transactionDir']
    if tx.is_symlink() or not tx.resolve().is_relative_to(directory):
        raise ImportErrorResponse('RECOVERY_CONFLICT', '事务备份路径不合法', 409)
    checked, seen = [], set()
    for number, entry in enumerate(intent['targets']):
        relative = entry.get('target')
        if not isinstance(relative, str) or not TARGET_RE.fullmatch(relative) or relative in seen:
            raise ImportErrorResponse('RECOVERY_CONFLICT', '事务目标不合法或重复', 409)
        seen.add(relative)
        target = service.data / relative
        actual = target_digest(target)
        if actual not in (entry.get('beforeSha256'), entry.get('afterSha256')):
            raise ImportErrorResponse('RECOVERY_CONFLICT', '事务目标已出现第三种摘要，停止所有写入', 409)
        backup, staged = tx / f'{number}.before', tx / f'{number}.after'
        if staged.is_symlink() or target_digest(staged) != entry.get('afterSha256'):
            raise ImportErrorResponse('RECOVERY_CONFLICT', '事务候选损坏，停止恢复', 409)
        if entry.get('beforeSha256') is not None and (backup.is_symlink() or target_digest(backup) != entry['beforeSha256']):
            raise ImportErrorResponse('RECOVERY_CONFLICT', '事务备份损坏，停止恢复', 409)
        checked.append((entry, target, backup, staged, actual))
    return checked


def rollback(service, job_id, intent):
    """未commit事务按备份回滚；回滚再次中断仍保留prepared以便重入。"""
    checked = _checked_entries(service, job_id, intent)
    for entry, target, backup, _, actual in reversed(checked):
        if actual == entry['beforeSha256']:
            continue
        if entry['beforeSha256'] is None:
            target.unlink()
            sync_directory(target.parent)
        else:
            atomic_bytes(target, backup.read_bytes())
    intent['state'] = 'rolled_back'
    intent['rolledBackAt'] = now().isoformat(timespec='seconds')
    atomic_json(service.job_dir(job_id) / 'review/apply-intent.json', intent)


def recover(service, job_id, intent):
    """调用方持全库与任务锁；prepared回滚，committed验证后完成applied。"""
    if intent.get('state') == 'prepared':
        rollback(service, job_id, intent)
        return
    checked = _checked_entries(service, job_id, intent)
    if any(actual != entry['afterSha256'] for entry, _, _, _, actual in checked):
        raise ImportErrorResponse('RECOVERY_CONFLICT', 'committed事务目标不完整，停止恢复', 409)
    decision = read_json(service.job_dir(job_id) / 'review/decision.json')
    if decision.get('draftSha256') != intent.get('draftSha256') or service.draft_digest(job_id) != intent.get('draftSha256'):
        raise ImportErrorResponse('RECOVERY_CONFLICT', '已提交事务的审批/草稿被改动，停止状态恢复', 409)
    meta = service.get(job_id)
    meta['status'], meta['error'] = 'applied', None
    service.save(meta)


def execute(service, meta, root, targets, draft_digest, id_map):
    """先持久化所有备份/stage，再prepared→替换→committed，异常补偿回滚。

    commit记录是多文件事务的唯一提交点；不捕获进程终止类BaseException，
    真实中断由下次显式恢复入口处理。调用方持续持有全库锁。
    """
    job_id = meta['jobId']
    directory = service.job_dir(job_id)
    tx = directory / 'review' / ('transaction-' + uuid.uuid4().hex)
    tx.mkdir(mode=0o700)
    entries = []
    for number, relative in enumerate(targets):
        if not TARGET_RE.fullmatch(relative):
            raise ImportErrorResponse('INVALID_PATH', '不支持的事务目标')
        target = service.data / relative
        before = target_digest(target)
        after = (Path(root) / 'data' / relative).read_bytes()
        if before is not None:
            atomic_bytes(tx / f'{number}.before', target.read_bytes())
        atomic_bytes(tx / f'{number}.after', after)
        entries.append(dict(target=relative, beforeSha256=before, afterSha256=digest_bytes(after)))
    sync_directory(tx)
    sync_directory(tx.parent)
    intent = dict(version=2, jobId=job_id, state='prepared', transactionDir=str(tx.relative_to(directory)),
                  targets=entries, draftSha256=draft_digest, idMap=id_map,
                  preparedAt=now().isoformat(timespec='seconds'))
    intent_path = directory / 'review/apply-intent.json'
    atomic_json(intent_path, intent)
    try:
        for entry, target, _, staged, _ in _checked_entries(service, job_id, intent):
            atomic_bytes(target, staged.read_bytes())
        intent['state'], intent['committedAt'] = 'committed', now().isoformat(timespec='seconds')
        atomic_json(intent_path, intent)
    except Exception:
        # commit可能已落盘而目录fsync报告失败；不把已提交事务反向回滚。
        persisted = read_json(intent_path)
        if persisted['state'] == 'prepared':
            rollback(service, job_id, persisted)
        raise
    meta['status'], meta['error'] = 'applied', None
    service.save(meta)
    return meta
