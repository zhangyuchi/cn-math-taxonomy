"""文本卷切题与确定性挂点建议；建议只写报告，绝不自动选定挂点。"""
import csv
import io
import json
import re

from .store import ImportErrorResponse, read_json

FIELDS = {'topicId', 'difficulty', 'type', 'prompt', 'answer', 'explanation', 'verify', 'choices', 'unordered'}
HEADER = re.compile(r'^\s*(?:#{1,6}\s+Q\d+|Q\d+|\d+[.、])\s*(.*)$', re.I)


def _blocks(text, suffix):
    """读取CSV行或编号块，保留无法解释的原文以供审核。"""
    if suffix == '.csv':
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ImportErrorResponse('PARSE_FAILED', 'CSV需要不重复的列名')
        return list(reader)
    blocks, current, field = [], None, None
    preamble = []
    for line in text.splitlines():
        heading = HEADER.fullmatch(line)
        if heading:
            current, field = {}, None
            blocks.append(current)
            if heading[1]:
                current['prompt'], field = heading[1], 'prompt'
            continue
        if current is None:
            if line.strip():
                preamble.append(line)
            continue
        match = re.match(r'^\s*([A-Za-z]+)\s*[:：]\s*(.*)$', line)
        if match and match[1] in FIELDS:
            field = match[1]
            if field in current:
                current['_error'] = '字段重复：' + field
            current[field] = match[2].strip()
        elif line.strip():
            field = field or 'prompt'
            current[field] = (current.get(field, '') + '\n' + line).strip()
    if preamble:
        blocks.insert(0, {'_error': '未编号的前置文本', '_raw': '\n'.join(preamble)})
    return blocks


def _choices(value):
    """接受JSON选项或规范A/B/C行，保留现有choice schema。"""
    if isinstance(value, list):
        return value
    value = value.strip()
    if value.startswith('['):
        return json.loads(value)
    result = []
    for line in re.split(r'\n|\s*;\s*', value):
        match = re.fullmatch(r'\s*[-*]?\s*([A-D])\s*[:：]\s*(.+)', line)
        if not match:
            raise ValueError('选项须为JSON或A:文本格式')
        result.append({'key': match[1], 'text': match[2]})
    return result


def _suggest(prompt, topics):
    """按名称重合及二字词重合排序，同分按id稳定排序，最多五项。"""
    def score(topic):
        name = topic['name']
        grams = {name[i:i+2] for i in range(max(0, len(name)-1))}
        return (20 if name in prompt else 0) + sum(word in prompt for word in grams)
    ranked = [(score(t), t['id']) for t in topics]
    return [tid for points, tid in sorted(ranked, key=lambda row: (-row[0], row[1])) if points > 0][:5]


def parse_paper(service, meta, content, suffix=None):
    """将成功块转换为待审题数组，未知挂点保持null；零题明确失败。

    match/suggestedTopicIds等机器推断位于parse报告，不污染正式题schema。
    缺答案/题干的块进入unparsed；有题即可待审，结构问题由候选校验报告呈现。
    """
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ImportErrorResponse('ENCODING_REQUIRED', '试卷文本须为UTF-8') from exc
    suffix = suffix or '.' + meta['sourceFiles'][0].rsplit('.', 1)[-1]
    topics = [t for t in read_json(service.data / 'topics.json')
              if t['id'].startswith(meta['subjectPrefix'] + '_') and not t.get('external')]
    index = {t['id']: t for t in topics}
    questions, unparsed, matches = [], [], []
    for number, row in enumerate(_blocks(text, suffix), 1):
        try:
            if None in row or any(v is None for v in row.values()):
                raise ValueError('CSV行列不一致')
            if row.get('_error'):
                raise ValueError(row['_error'])
            if set(row) - FIELDS:
                raise ValueError('未知试卷字段：' + ','.join(sorted(set(row)-FIELDS)))
            if not row.get('prompt', '').strip() or not row.get('answer', '').strip():
                raise ValueError('缺少题干或答案')
            supplied = row.get('topicId', '').strip()
            if supplied and not supplied.startswith(meta['subjectPrefix'] + '_'):
                raise ImportErrorResponse('SUBJECT_CONFLICT', '题挂点与任务学科冲突')
            tid = supplied if supplied in index else None
            qid = (tid + '_qdraft' if tid else '_import_' + meta['jobId'] + '_') + str(number)
            q = dict(id=qid, topicId=tid, type=row.get('type') or ('choice' if row.get('choices') else 'numeric'),
                     difficulty=row.get('difficulty') or 'standard', prompt=row['prompt'].strip(),
                     answer=row['answer'].strip(), explanation=row.get('explanation') or '导入草稿，待审：请补充解析')
            if row.get('choices'):
                q['choices'] = _choices(row['choices'])
            if row.get('verify'):
                q['verify'] = row['verify'].strip()
            if row.get('unordered'):
                if row['unordered'].lower() != 'true':
                    raise ValueError('unordered仅可为true')
                q['unordered'] = True
            questions.append(q)
            matches.append(dict(id=qid, match='explicit' if tid else 'unresolved', suppliedTopicId=supplied or None,
                                suggestedTopicIds=[] if tid else _suggest(q['prompt'], topics)))
        except (ValueError, TypeError) as exc:
            unparsed.append(dict(block=number, reason=str(exc), source=row))
    if not questions:
        raise ImportErrorResponse('EMPTY_PAPER', '0题成功解析；请按编号块或CSV规范提供题干与答案')
    draft = dict(topics=[], dependencies=[], questions=questions, mappings={}, standards_suggestions=[])
    return text, draft, dict(unparsed=unparsed, matches=matches)
