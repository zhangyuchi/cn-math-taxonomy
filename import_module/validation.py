"""在隔离数据副本与子进程中调用现有 validator，保持上游规则不变。"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .store import CODE_ROOT, ImportErrorResponse, atomic_json, read_json

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


@contextlib.contextmanager
def candidate(service, job_id):
    """复制正式快照并合并草稿；临时目录退出时删除，不接触正式文件。"""
    draft, meta = service.draft(job_id), service.get(job_id)
    with tempfile.TemporaryDirectory(prefix='_candidate-', dir=service.imports) as temporary:
        root = Path(temporary)
        data = root / 'data'
        data.mkdir()
        for name in FORMAL_FILES:
            shutil.copy2(service.data / name, data / name)
        shutil.copytree(service.data / 'mappings', data / 'mappings')
        for name in ('topics', 'dependencies', 'questions'):
            if not isinstance(draft[name], list):
                raise ImportErrorResponse('INVALID_DRAFT', name + ' 必须为数组')
            if draft[name]:
                atomic_json(data / (name + '.json'), read_json(data / (name + '.json')) + draft[name])
        target = None
        if draft['mappings']:
            target = mapping_target(meta, draft['mappings'])
            path = data / target
            existing = read_json(path) if path.exists() else None
            atomic_json(path, merged_mapping(existing, draft['mappings']))
        yield root, target


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
