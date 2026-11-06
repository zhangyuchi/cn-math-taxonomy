"""两段解析格式与离线生成补答转换；不求解、不写服务或正式库。"""
import argparse
import copy
import json
from pathlib import Path
import re
import sys

from .limits import MAX_DRAFT_BYTES
from .store import digest_bytes, json_bytes, atomic_json

HINT_MISSING = '原文未提供思路提示，待人工补充。'
DETAIL_MISSING = '导入草稿，待审：请补充解析'
GENERATED = '【生成补答，待人工复核】'
UNCERTAIN = '【答案不确定】'
ORIGINAL = '【依赖原图/需对照原卷】'
PARTS = re.compile(r'\A### 思路提示\s*\n(.*?)\n+### 详细解答\s*\n(.*)\Z', re.S)


def format_explanation(explanation='', hint='', detail=''):
    """合成唯一的两段字符串；旧解析保留为详解，冲突不静默覆盖。"""
    explanation, hint, detail = (s.strip() for s in (explanation, hint, detail))
    match = PARTS.fullmatch(explanation)
    old_hint, old_detail = match.groups() if match else ('', explanation)
    if (hint and old_hint and hint != old_hint.strip()) or (detail and old_detail and detail != old_detail.strip()):
        raise ValueError('两段解析与 explanation 内容冲突')
    return ('### 思路提示\n' + (hint or old_hint.strip() or HINT_MISSING) +
            '\n\n### 详细解答\n' + (detail or old_detail.strip() or DETAIL_MISSING))


def build_backfill(snapshot, manifest):
    """把绑定快照的七年级补答转为既有 revise 输入，绝不覆盖已写答案。

    清单必须完整、唯一并声明不确定性；原文依赖或不确定时禁止非空答案。
    这里只验证声明与格式，数学正确性和学段由审核者负责。服务提交时仍须
    传 expectedDraftSha256，防止离线转换后的并发修订被覆盖。
    """
    required = {'jobId', 'sourceSha256', 'expectedDraftSha256', 'knowledgeScope', 'solutions'}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError('补答清单字段不符合约定')
    if snapshot.get('type') != 'paper' or snapshot.get('subjectPrefix') != 'cnm' or snapshot.get('status') != 'needs_review':
        raise ValueError('仅支持待审数学试卷')
    digest = digest_bytes(json_bytes(snapshot['draft']))
    if (snapshot.get('draftSha256') != digest or manifest['expectedDraftSha256'] != digest or
            any(manifest[key] != snapshot.get(key) for key in ('jobId', 'sourceSha256'))):
        raise ValueError('补答清单与 job/source/draft 摘要不匹配')
    if manifest['knowledgeScope'] != 'grade7':
        raise ValueError('本补答路径仅接受 grade7 知识范围声明')
    rows = manifest['solutions']
    fields = {'id', 'answer', 'hint', 'detail', 'answerUncertain', 'originalRequired'}
    if not isinstance(rows, list) or any(not isinstance(r, dict) or set(r) != fields for r in rows):
        raise ValueError('补答行字段不符合约定')
    for row in rows:
        if (any(not isinstance(row[k], str) for k in ('id', 'answer', 'hint', 'detail')) or
                any(type(row[k]) is not bool for k in ('answerUncertain', 'originalRequired')) or
                not row['hint'].strip() or not row['detail'].strip()):
            raise ValueError('补答须有提示/详解及布尔风险声明')
        if (row['answerUncertain'] or row['originalRequired']) and row['answer'].strip():
            raise ValueError('不确定或依赖原卷的补答须保留空 answer')
        if not row['answer'].strip() and not (row['answerUncertain'] or row['originalRequired']):
            raise ValueError('空答案须声明不确定性或原卷依赖')
    indexed = {r['id']: r for r in rows}
    questions = snapshot['draft']['questions']
    if (len(indexed) != len(rows) or len(questions) != len(rows) or
            set(indexed) != {q['id'] for q in questions}):
        raise ValueError('补答题 ID 必须完整且唯一')
    draft = copy.deepcopy(snapshot['draft'])
    for q in draft['questions']:
        if (q.get('answer', '').strip() or
                q.get('explanation', '').strip() not in ('', DETAIL_MISSING, format_explanation())):
            raise ValueError('仅允许回填空答案与占位解析，已有内容请人工 revise')
        row = indexed[q['id']]
        answer = row['answer'].strip()
        if q.get('type') == 'choice' and answer and answer not in {c['key'] for c in q.get('choices', [])}:
            raise ValueError('选择题答案必须为原有选项键')
        markers = [GENERATED + '（generated；仅限七年级知识；需人工审核）']
        if row['answerUncertain']:
            markers.append(UNCERTAIN)
        if row['originalRequired']:
            markers.append(ORIGINAL)
        q['answer'] = answer
        q['explanation'] = format_explanation(hint='\n'.join(markers) + '\n' + row['hint'].strip(), detail=row['detail'])
    return dict(draft=draft, expectedDraftSha256=digest)


def main():
    """有界读取离线快照与补答清单，输出可审 JSON；不执行 revise。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--solutions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        inputs = []
        for path in (args.snapshot, args.solutions):
            with path.open('rb') as stream:
                content = stream.read(MAX_DRAFT_BYTES + 1)
            if len(content) > MAX_DRAFT_BYTES:
                raise ValueError('离线输入超过64MiB')
            inputs.append(json.loads(content))
        result = build_backfill(*inputs)
        atomic_json(args.output, result)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
