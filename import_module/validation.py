"""在隔离数据副本与子进程中调用现有 validator，保持上游规则不变。"""
import argparse
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .store import CODE_ROOT, SUBJECTS, DRAFT_NAMES, ImportErrorResponse, atomic_json, read_json

FORMAL_FILES = ('topics.json', 'dependencies.json', 'questions.json',
                'cn-curriculum-standards.json', 'id-aliases.json')


def mapping_target(meta, mapping):
    """在构建候选路径前校验 edition 及学科，避免草稿控制写入路径。"""
    prefix, edition = meta['subjectPrefix'], mapping.get('edition')
    if prefix not in ('cnm', 'cne'):
        raise ImportErrorResponse('MAPPINGS_SUBJECT_UNSUPPORTED', 'mappings 仅支持数学和英语')
    if not isinstance(edition, str) or not re.fullmatch(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*', edition):
        raise ImportErrorResponse('INVALID_EDITION', 'edition 格式不合法')
    if edition != meta.get('edition') or edition in ('renjiao_math', 'pep_math'):
        raise ImportErrorResponse('INVALID_EDITION', 'edition 与 job 不一致或为保留的人教版本')
    expected = '_math' if prefix == 'cnm' else '_english'
    if not edition.endswith(expected):
        raise ImportErrorResponse('INVALID_EDITION', 'edition 后缀与学科不一致')
    if any(not isinstance(mapping.get(key), str) or not mapping[key].strip() for key in ('label', 'source')):
        raise ImportErrorResponse('INVALID_MAPPING', '本次导入必须提供非空 label 和 source，不能借用旧依据')
    entries = mapping.get('entries')
    if not isinstance(entries, list) or not entries:
        raise ImportErrorResponse('INVALID_MAPPING', 'entries 必须为非空数组')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('topicId'), str):
            raise ImportErrorResponse('INVALID_MAPPING', '条目缺少 topicId')
        if not entry['topicId'].startswith(prefix + '_') or entry['topicId'] in seen:
            raise ImportErrorResponse('INVALID_MAPPING', 'topicId 重复或学科不一致')
        seen.add(entry['topicId'])
    return 'mappings/' + edition + '.json'


def merged_mapping(current, incoming):
    """按 topicId 更新本次条目，保留其他条目与历史依据，交全量校验把关。"""
    if not current:
        return incoming
    result = dict(incoming)
    entries = {entry['topicId']: entry for entry in current['entries']}
    entries.update({entry['topicId']: entry for entry in incoming['entries']})
    result['entries'] = list(entries.values())
    provenance = [current.get('source'), incoming.get('source')]
    result['source'] = '\n'.join(dict.fromkeys(value for value in provenance if isinstance(value, str) and value.strip()))
    return result


def prepared_draft(service, meta, draft):
    """在全库锁内为一次草稿快照续号，仅重映射新增实体及其引用。

    不覆盖已有topic/question；机器建议保留报告，正式候选只允许现有题字段。
    返回候选草稿和可审计ID映射，parse与apply均使用同一规则。
    """
    if (not isinstance(draft, dict) or set(draft) != set(DRAFT_NAMES) or
            any(not isinstance(draft[n], list) for n in DRAFT_NAMES if n != 'mappings') or
            not isinstance(draft['mappings'], dict)):
        raise ImportErrorResponse('INVALID_DRAFT', '草稿必须包含约定的五份JSON')
    if meta['mode'] == 'mappings_only' and any(draft[n] for n in DRAFT_NAMES if n != 'mappings'):
        raise ImportErrorResponse('INVALID_DRAFT', 'mappings_only不能混入点、边、题或课标建议')
    result = copy.deepcopy(draft)
    prefix = meta['subjectPrefix']
    topics = read_json(service.data / 'topics.json')
    index = {t['id']: t for t in topics}
    remap, counters = {}, {}
    for topic in result['topics']:
        if not isinstance(topic, dict) or not isinstance(topic.get('id'), str) or topic['id'] in remap:
            raise ImportErrorResponse('INVALID_DRAFT', '新增点需要唯一id')
        original = topic['id']
        grade = topic.get('grade')
        stems = [stem for low, high, stem in SUBJECTS[prefix]['stages']
                 if isinstance(grade, int) and not isinstance(grade, bool) and low <= grade <= high]
        if not stems or not re.fullmatch(re.escape(stems[0]) + r'\d{3,}', original):
            raise ImportErrorResponse('INVALID_DRAFT', '新点id与任务学科/学段不一致')
        stem = stems[0]
        if stem not in counters:
            counters[stem] = max((int(t['id'][len(stem):]) for t in topics
                                 if re.fullmatch(re.escape(stem)+r'\d{3,}', t['id'])), default=0)
        counters[stem] += 1
        topic['id'] = stem + f'{counters[stem]:03d}'
        remap[original] = topic['id']
        index[topic['id']] = topic
    for topic in result['topics']:
        if isinstance(topic.get('mathLinks'), list):
            # 现库mathLinks为{id, reason}，不能把它当成裸ID数组。
            for link in topic['mathLinks']:
                if isinstance(link, dict) and isinstance(link.get('id'), str):
                    link['id'] = remap.get(link['id'], link['id'])
    for edge in result['dependencies']:
        if not isinstance(edge, dict):
            raise ImportErrorResponse('INVALID_DRAFT', '依赖须为对象')
        for key in ('topicId', 'prerequisiteId'):
            edge[key] = remap.get(edge.get(key), edge.get(key))
    for suggestion in result['standards_suggestions']:
        if not isinstance(suggestion, dict):
            raise ImportErrorResponse('INVALID_DRAFT', '课标建议须为对象')
        suggestion['topicId'] = remap.get(suggestion.get('topicId'), suggestion.get('topicId'))
    # expand也可带版本对照，引用必须随新增点一起重编号，不能挂到竞争job的旧ID。
    entries = result['mappings'].get('entries')
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get('topicId'), str):
                entry['topicId'] = remap.get(entry['topicId'], entry['topicId'])
    questions = read_json(service.data / 'questions.json')
    q_counters = {}
    for q in questions:
        match = re.fullmatch(re.escape(q['topicId']) + r'_q(\d+)', q['id'])
        if match:
            q_counters[q['topicId']] = max(q_counters.get(q['topicId'], 0), int(match[1]))
    seen = set()
    for question in result['questions']:
        if not isinstance(question, dict) or not isinstance(question.get('id'), str) or question['id'] in seen:
            raise ImportErrorResponse('INVALID_DRAFT', '题目须为含唯一id的对象')
        seen.add(question['id'])
        tid = question.get('topicId')
        if tid is None:
            raise ImportErrorResponse('TOPIC_ID_REQUIRED', '请审核并指定每题topicId')
        if not isinstance(tid, str) or not tid.startswith(prefix + '_'):
            raise ImportErrorResponse('SUBJECT_CONFLICT', '题目挂点与任务学科冲突')
        tid = remap.get(tid, tid)
        if tid not in index or index[tid].get('external'):
            raise ImportErrorResponse('INVALID_DRAFT', '题目必须挂到存在的正式非external点')
        q_counters[tid] = q_counters.get(tid, 0)+1
        old = question['id']
        question.update(id=tid+'_q'+str(q_counters[tid]), topicId=tid)
        remap[old] = question['id']
    return result, remap


@contextlib.contextmanager
def candidate(service, job_id, draft=None):
    """调用方持全库锁；复制快照并合并一次核准草稿，不接触正式文件。"""
    meta = service.get(job_id)
    draft, remap = prepared_draft(service, meta, service.draft(job_id) if draft is None else draft)
    with tempfile.TemporaryDirectory(prefix='_candidate-', dir=service.imports) as temporary:
        root = Path(temporary)
        data = root / 'data'
        data.mkdir()
        for name in FORMAL_FILES:
            shutil.copy2(service.data / name, data / name)
        shutil.copytree(service.data / 'mappings', data / 'mappings')
        targets = []
        for name in ('topics', 'dependencies', 'questions'):
            if draft[name]:
                target = name + '.json'
                atomic_json(data / target, read_json(data / target) + draft[name])
                targets.append(target)
        if draft['mappings']:
            target = mapping_target(meta, draft['mappings'])
            path = data / target
            existing = read_json(path) if path.exists() else None
            atomic_json(path, merged_mapping(existing, draft['mappings']))
            targets.append(target)
        yield root, dict(targets=targets, idMap=remap, draft=draft)


def validate_candidate(service, job_id, phase='parse'):
    """每次以新进程运行全量规则，报告写在 job 下；失败不会改正式库。"""
    try:
        with candidate(service, job_id) as (root, _):
            report = validate_root(root)
    except ImportErrorResponse as exc:
        report = dict(ok=False, code=exc.code, output=str(exc))
    report['phase'] = phase
    atomic_json(service.job_dir(job_id) / 'reports/validate.json', report)
    return report


def validate_root(root):
    """用代码仓库内原 validator 校验指定候选根；超时或子进程故障明确失败。"""
    try:
        completed = subprocess.run([sys.executable, '-m', 'import_module.validation', '--root', str(root)],
                                   cwd=CODE_ROOT, env=os.environ.copy(), capture_output=True,
                                   text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise ImportErrorResponse('VALIDATION_TIMEOUT', '候选库全量校验超时', 500) from exc
    if completed.returncode != 0:
        raise ImportErrorResponse('VALIDATOR_ERROR', '校验进程失败：' + completed.stderr[-2000:], 500)
    return json.loads(completed.stdout)


def main():
    """内部子进程入口：重定向原脚本路径并返回真实输出，不修改其源码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(CODE_ROOT / 'scripts'))
    import validate as validator
    data = args.root / 'data'
    for attribute, filename in [('TOPICS_PATH', 'topics.json'), ('DEPS_PATH', 'dependencies.json'),
                                ('QUESTIONS_PATH', 'questions.json'), ('STANDARDS_PATH', 'cn-curriculum-standards.json'),
                                ('ALIASES_PATH', 'id-aliases.json'), ('MAPPINGS_DIR', 'mappings')]:
        setattr(validator, attribute, str(data / filename))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = validator.main()
    print(json.dumps(dict(ok=code == 0, output=output.getvalue(), errors=validator.errors,
                          warnings=validator.warnings), ensure_ascii=False))


if __name__ == '__main__':
    main()
