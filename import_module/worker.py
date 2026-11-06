"""单进程目录队列 worker；解析只写草稿，正式写入委托 apply。"""
import argparse
import logging
import os
from pathlib import Path
import time

from .apply import apply_job, recover_pending
from .parsers import parse_textbook
from .paper import parse_paper
from .paper_assets import question_risks
from .ocr import normalize_pdf
from .verification import verify_questions
from .transactions import assert_clean
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
            if automatic:
                try:
                    apply_job(service, directory.name)
                except ImportErrorResponse as exc:
                    LOG.warning('恢复 apply %s：%s', directory.name, exc.code)


def write_reports(service, job_id, draft, parse_details=None):
    """只写job报告；调用方持全库/job锁，正式快照不会混入部分事务。"""
    directory = service.job_dir(job_id)
    parse = dict(ok=True, counts={name: len(value.get('entries', [])) if isinstance(value, dict)
                 else len(value) for name, value in draft.items()},
                 warnings=['机器生成草稿，无教师背书；目录顺序和挂点建议均须审核'], unparsed=[])
    parse.update(parse_details or {})
    if service.get(job_id)['type'] == 'paper':
        parse.update(question_risks(service, job_id, draft['questions']))
        parse.setdefault('initialRisks', parse['questionRisks'])
        # 风险与当前草稿同源；调用方后续保存状态时须重读，避免旧 meta 覆盖。
        meta = service.get(job_id)
        meta['flags'].update(riskCodes=parse['riskCodes'], requiresHumanReview=True)
        service.save(meta)
    atomic_json(directory / 'reports/parse.json', parse)
    verify = verify_questions(draft['questions'])
    atomic_json(directory / 'reports/verify.json', verify)
    questions = read_json(service.data / 'questions.json') + draft['questions']
    affected = {t['id'] for t in draft['topics']} | {q.get('topicId') for q in draft['questions']}
    coverage = []
    for tid in sorted(t for t in affected if isinstance(t, str)):
        qs = [q for q in questions if q.get('topicId') == tid]
        if len(qs) < 3 or not {'basic', 'standard'} <= {q.get('difficulty') for q in qs}:
            coverage.append(tid)
    atomic_json(directory / 'reports/coverage.json', dict(needs_more=coverage,
                unresolved=sum(q.get('topicId') is None for q in draft['questions']),
                note='完整validator另行检查覆盖率、q1、图和学科约束；禁止override绕过'))
    return validate_candidate(service, job_id)


def _process(service, job_id):
    """文本/OCR进入同一草稿管道；解析只写job，新点/题必须人工审核。"""
    automatic = False
    with file_lock(service.imports / '_apply.lock'), file_lock(service.job_dir(job_id) / '.lock'):
        assert_clean(service)
        meta = service.get(job_id)
        if meta['status'] != 'queued':
            return
        meta['status'], meta['error'] = 'running', None
        service.save(meta)
        directory = service.job_dir(job_id)
        try:
            service.cleanup_draft_temps(job_id)
            content = service.source(job_id)
            pdf = meta['sourceFiles'][0].endswith('.pdf')
            if pdf:
                content = normalize_pdf(service, meta)
            if meta['type'] == 'paper':
                text, draft, details = parse_paper(service, meta, content, '.md' if pdf else None, ocr=pdf)
            else:
                # OCR统一生成Markdown，不按原PDF后缀尝试解析二进制。
                parsed_meta = dict(meta, sourceFiles=['source/normalized.md']) if pdf else meta
                text, draft = parse_textbook(service, parsed_meta, content)
                details = {}
            atomic_bytes(directory / 'source/normalized.md', text.encode())
            for name in DRAFT_NAMES:
                atomic_json(directory / 'draft' / (name + '.json'), draft[name])
            report = write_reports(service, job_id, draft, details)
            if service.mappings_only(job_id) and report['ok'] and meta['flags']['autoApplyMappings']:
                service._approve_locked(job_id, automatic=True)
                automatic = True
            else:
                meta = service.get(job_id)
                meta['status'] = 'needs_review'
                service.save(meta)
        except ImportErrorResponse as exc:
            meta['status'] = 'failed'
            meta['error'] = {'code': exc.code, 'error': str(exc)}
            atomic_json(directory / 'reports/parse.json', dict(ok=False, **meta['error'], **(exc.details or {})))
            service.save(meta)
    if automatic:
        try:
            apply_job(service, job_id)
        except ImportErrorResponse as exc:
            LOG.warning('自动apply %s被拒绝：%s', job_id, exc.code)


def run_once(service):
    """消费至多一个 job；全程 worker 独占，启动时恢复持久状态。"""
    with file_lock(service.imports / '_worker.lock'):
        recover_pending(service)
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
            try:
                run_once(service)
            except ImportErrorResponse as exc:
                if exc.code != 'BUSY' or args.once:
                    raise
                LOG.info('工作区忙，保留队列稍后重试')
            if args.once:
                return 0
            time.sleep(0.5)
    except (OSError, ValueError, ImportErrorResponse) as exc:
        LOG.error('%s: %s', getattr(exc, 'code', 'WORKER_ERROR'), exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
