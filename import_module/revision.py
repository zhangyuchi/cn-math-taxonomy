"""五文件草稿修订的持久意图与补偿恢复；审批永远先失效。"""
import uuid

from .limits import MAX_DRAFT_BYTES, MIB

from .store import (DRAFT_NAMES, ImportErrorResponse, atomic_json, digest_bytes,
                    file_lock, json_bytes, now, read_json, sync_directory)


def check_shape(draft, *, limit_size=True):
    """检查五份JSON形状；大小限制仅用于入站修订，不拒绝已生成的大备份。"""
    if (not isinstance(draft, dict) or set(draft) != set(DRAFT_NAMES) or
            not isinstance(draft['mappings'], dict) or
            any(not isinstance(draft[n], list) or any(not isinstance(row, dict) for row in draft[n])
                for n in DRAFT_NAMES if n != 'mappings') or (limit_size and len(json_bytes(draft)) > MAX_DRAFT_BYTES)):
        raise ImportErrorResponse('INVALID_DRAFT', f'修订须为约定五份JSON，数组内为对象，总计≤{MAX_DRAFT_BYTES // MIB}MiB')


def pending(service, job_id):
    """仅检查未完成修订；读取混合版本必须失败关闭。"""
    path = service.job_dir(job_id) / 'review/revision-intent.json'
    return path.exists() and read_json(path).get('state') == 'prepared'


def recover_locked(service, job_id):
    """显式恢复prepared修订，先核对全部before/after再回滚；调用方持锁。"""
    path = service.job_dir(job_id) / 'review/revision-intent.json'
    if not pending(service, job_id):
        return
    intent = read_json(path)
    for key in ('before', 'after'):
        # before来自已接受的worker产物，可超过入站修订上限；结构/摘要仍逐项核验。
        check_shape(intent[key], limit_size=key == 'after')
        if digest_bytes(json_bytes(intent[key])) != intent[key+'Sha256']:
            raise ImportErrorResponse('RECOVERY_CONFLICT', '草稿修订备份损坏', 409)
    service.cleanup_draft_temps(job_id)
    current = service._draft_files(job_id)
    if any(current[n] != intent['before'][n] and current[n] != intent['after'][n] for n in DRAFT_NAMES):
        raise ImportErrorResponse('RECOVERY_CONFLICT', '草稿存在第三种内容，停止恢复', 409)
    meta = service.get(job_id)
    meta['status'], meta['error'] = 'needs_review', None
    service.save(meta)
    for name in DRAFT_NAMES:
        atomic_json(service.job_dir(job_id) / 'draft' / (name+'.json'), intent['before'][name])
    intent['state'] = 'rolled_back'
    atomic_json(path, intent)


def revise(service, job_id, draft, expected_digest, comment=''):
    """全库→job锁内检查审核版本，保留审计并可恢复地修订完整草稿。

    意图发布前不改变草稿；发布后先撤销审批。中断期间draft/approve均拒绝，
    显式worker启动恢复旧版。成功后重建校验报告并保留初始匹配证据，任何修订均须重新批准。
    """
    from .transactions import assert_clean
    check_shape(draft)
    if not isinstance(comment, str):
        raise ImportErrorResponse('INVALID_FIELD', '修订说明必须为字符串', 400)
    with file_lock(service.imports / '_apply.lock'):
        assert_clean(service)
        with file_lock(service.job_dir(job_id) / '.lock'):
            meta = service.get(job_id)
            if meta['status'] not in ('needs_review', 'approved'):
                raise ImportErrorResponse('INVALID_STATE', '仅待审/已批准任务允许修订', 409)
            before = service.draft(job_id)
            if expected_digest != digest_bytes(json_bytes(before)):
                raise ImportErrorResponse('DRAFT_CHANGED', '所见草稿已过期，请重新读取后修订', 409)
            service.source(job_id)
            directory = service.job_dir(job_id)
            # 改挂后保留初始机器评分；matchesScope 避免把旧评分解释为修订后挂点的依据。
            parsed = read_json(directory / 'reports/parse.json')
            details = {key: parsed[key] for key in ('matches', 'matchesScope') if key in parsed}
            if 'matches' in details:
                details['matchesScope'] = 'initial_parse'
            details.update(revision=True, unparsed=[])
            intent = dict(state='prepared', before=before, after=draft,
                          beforeSha256=expected_digest, afterSha256=digest_bytes(json_bytes(draft)),
                          comment=comment, actor='ops', at=now().isoformat(timespec='seconds'))
            decision = directory / 'review/decision.json'
            if decision.exists():
                intent['previousDecision'] = read_json(decision)
            archive = directory / 'review' / ('revision-' + uuid.uuid4().hex + '.json')
            atomic_json(archive, intent)
            atomic_json(directory / 'review/revision-intent.json', intent)
            try:
                meta['status'], meta['error'] = 'needs_review', None
                service.save(meta)
                if decision.exists():
                    decision.unlink()
                    sync_directory(decision.parent)
                for name in DRAFT_NAMES:
                    atomic_json(directory / 'draft' / (name+'.json'), draft[name])
                intent['state'] = 'committed'
                atomic_json(directory / 'review/revision-intent.json', intent)
                atomic_json(archive, intent)
            except Exception:
                if pending(service, job_id):
                    recover_locked(service, job_id)
                raise
            from .worker import write_reports
            write_reports(service, job_id, draft, details)
            return meta
