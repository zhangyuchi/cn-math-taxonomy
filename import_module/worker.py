"""单进程目录队列 worker；解析只写草稿，正式写入委托 apply。"""
import argparse
import logging
import os
from pathlib import Path
import time

from .apply import apply_job
from .parsers import parse_textbook
from .store import (DRAFT_NAMES, JOB_RE, ImportErrorResponse, Service, atomic_bytes,
                    atomic_json, file_lock, read_json)
from .validation import validate_candidate

LOG = logging.getLogger('import-worker')


def _recover_queue(service):
    """持有 worker 独占锁时修复 queued/running 的丢失指针，不重解析已审任务。"""
    for directory in sorted(service.imports.iterdir()):
        if not JOB_RE.fullmatch(directory.name):
            continue
        meta = service.get(directory.name)
        marker = service.imports / '_queue' / directory.name
        if meta['status'] == 'running':
            with file_lock(directory / '.lock'):
                meta['status'] = 'queued'
                meta['error'] = {'code': 'WORKER_INTERRUPTED', 'error': '单 worker 已恢复中断解析'}
                service.save(meta)
        if meta['status'] == 'queued' and not marker.exists():
            atomic_json(marker, {'jobId': directory.name})
        if meta['status'] == 'approved':
            decision_path = directory / 'review/decision.json'
            automatic = decision_path.exists() and read_json(decision_path).get('actor') == 'system:auto-mappings'
            if (directory / 'review/apply-intent.json').exists() or automatic:
                try:
                    apply_job(service, directory.name)
                except ImportErrorResponse as exc:
                    LOG.warning('恢复 apply %s：%s', directory.name, exc.code)


def _process(service, job_id):
    """生成完整草稿/报告；PDF 和未实现 paper 明确失败且原始字节保留。"""
    automatic = False
    with file_lock(service.job_dir(job_id) / '.lock'):
        meta = service.get(job_id)
        if meta['status'] != 'queued':
            return
        meta['status'], meta['error'] = 'running', None
        service.save(meta)
        directory = service.job_dir(job_id)
        try:
            content = service.source(job_id)
            if meta['sourceFiles'][0].endswith('.pdf'):
                raise ImportErrorResponse('OCR_REQUIRED', '请先导出目录文本，或待 v1.1 OCR')
            if meta['type'] == 'paper':
                raise ImportErrorResponse('PIPELINE_NOT_IMPLEMENTED', '本次骨架尚未实现文本卷解析；原文已保留')
            text, draft = parse_textbook(service, meta, content)
            atomic_bytes(directory / 'source/normalized.md', text.encode())
            for name in DRAFT_NAMES:
                atomic_json(directory / 'draft' / (name + '.json'), draft[name])
            atomic_json(directory / 'reports/parse.json', dict(ok=True, counts={name: len(value.get('entries', []))
                        if isinstance(value, dict) else len(value) for name, value in draft.items()},
                        warnings=['机器生成草稿，无教师背书；domain/目录顺序仅为待审建议'] if draft['topics'] else [], unparsed=[]))
            atomic_json(directory / 'reports/verify.json', dict(status='not_applicable',
                        reason='本次骨架不解析新题，不调用会写正式 verification_report 的默认入口', mismatch=[]))
            atomic_json(directory / 'reports/coverage.json', dict(needs_more=[t['id'] for t in draft['topics']],
                        note='正式覆盖率、难度和 q1 约束由完整 validator 报告'))
            report = validate_candidate(service, job_id)
            if meta['mode'] == 'mappings_only' and report['ok'] and meta['flags']['autoApplyMappings']:
                service._approve_locked(job_id, automatic=True)
                automatic = True
            else:
                meta['status'] = 'needs_review'
                service.save(meta)
        except ImportErrorResponse as exc:
            meta['status'] = 'failed'
            meta['error'] = {'code': exc.code, 'error': str(exc)}
            atomic_json(directory / 'reports/parse.json', dict(ok=False, **meta['error']))
            service.save(meta)
    if automatic:
        try:
            apply_job(service, job_id)
        except ImportErrorResponse as exc:
            LOG.warning('自动 apply %s 被拒绝：%s', job_id, exc.code)


def run_once(service):
    """消费至多一个 job；全程 worker 独占，启动时恢复持久状态。"""
    with file_lock(service.imports / '_worker.lock'):
        _recover_queue(service)
        queue = service.imports / '_queue'
        for marker in sorted(queue.iterdir()):
            if not JOB_RE.fullmatch(marker.name):
                continue
            if service.get(marker.name)['status'] != 'queued':
                marker.unlink()
                continue
            _process(service, marker.name)
            marker.unlink(missing_ok=True)
            return True
    return False


def main():
    """运行单次处理或本地 worker 循环，配置缺失时拒绝隐式使用静态根。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default=os.environ.get('IMPORT_WORKSPACE'), type=Path)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not args.workspace:
        parser.error('必须设置 IMPORT_WORKSPACE 或 --workspace（位于静态根之外）')
    logging.basicConfig(level=logging.INFO)
    try:
        service = Service(args.workspace)
        while True:
            run_once(service)
            if args.once:
                return 0
            time.sleep(0.5)
    except (OSError, ValueError, ImportErrorResponse) as exc:
        LOG.error('%s: %s', getattr(exc, 'code', 'WORKER_ERROR'), exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
