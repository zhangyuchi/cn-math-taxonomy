"""确定性 TOC/显式 mappings 解析；所有生成内容仍为机器待审草稿。"""
import csv
import io
import re

from .store import ImportErrorResponse, SUBJECTS

NOISE = {'目录', '编者的话', '前言', '序言', '附录'}


def metadata_value(key, row, metadata, params):
    """统一检查目录行、源头部与 CLI 元数据，避免 mappings 静默忽略冲突。"""
    values = [str(value) for value in (row.get(key), metadata.get(key), params.get(key)) if value not in (None, '')]
    if len(set(values)) > 1:
        raise ImportErrorResponse('METADATA_CONFLICT', '目录行与元数据/参数冲突：' + key)
    return values[0] if values else None


def text_rows(text, suffix):
    """读取 CSV、Markdown 表格或章/节目录，保留源中的显式元数据。"""
    metadata, lines = {}, []
    for line in text.splitlines():
        match = re.fullmatch(r'\s*(grade|term|prefix|subjectPrefix)\s*[:：]\s*(.*?)\s*', line)
        if match:
            key, value = match.groups()
            if key in metadata and metadata[key] != value:
                raise ImportErrorResponse('METADATA_CONFLICT', '源元数据重复且冲突：' + key)
            metadata[key] = value
        elif line.strip() and line.strip() != '---':
            lines.append(line.strip())
    if suffix == '.csv':
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ImportErrorResponse('PARSE_FAILED', 'CSV 需要不重复的列名')
        rows = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise ImportErrorResponse('PARSE_FAILED', 'CSV 行列数不一致')
        return metadata, rows
    if lines and '|' in lines[0] and 'topicId' in lines[0]:
        table = [line.strip('|').split('|') for line in lines if not re.fullmatch(r'[|:\-\s]+', line)]
        header = [cell.strip() for cell in table[0]]
        if len(set(header)) != len(header) or any(len(row) != len(header) for row in table[1:]):
            raise ImportErrorResponse('PARSE_FAILED', 'Markdown 表格列不一致')
        return metadata, [dict(zip(header, (cell.strip() for cell in row))) for row in table[1:]]
    rows, chapter = [], '目录导入'
    for line in lines:
        title = re.sub(r'^(?:#{1,6}\s+|[-*+]\s+)', '', line).strip()
        if title in NOISE:
            continue
        if line.startswith('# ') or re.match(r'^第[一二三四五六七八九十百\d]+[章单元]', title):
            chapter = title
        else:
            rows.append({'chapter': chapter, 'topic_name': title})
    return metadata, rows


def parse_textbook(service, meta, content):
    """生成与现库字段兼容的候选；不推断完整题目或虚构正式先修边。

    mappings 必须来自明确 topicId；expand 的学科/学段由配置与现库存量双重确认。
    默认 domain 仅为待审建议，不代表课程专家分类。
    """
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ImportErrorResponse('ENCODING_REQUIRED', '目录文本须为 UTF-8') from exc
    suffix = meta['sourceFiles'][0].rsplit('.', 1)[-1]
    metadata, rows = text_rows(text, '.' + suffix)
    if not rows:
        raise ImportErrorResponse('EMPTY_TOC', '目录中没有可解析条目')
    prefix = meta['subjectPrefix']
    for key in ('prefix', 'subjectPrefix'):
        if metadata.get(key) and metadata[key] != prefix:
            raise ImportErrorResponse('METADATA_CONFLICT', '源学科与 CLI 参数冲突')
    params = meta['parameters']
    for key in ('grade', 'term'):
        if key in metadata and key in params and str(params[key]) != metadata[key]:
            raise ImportErrorResponse('METADATA_CONFLICT', '源目录与 CLI 参数冲突：' + key)
    if meta['mode'] == 'mappings_only':
        entries = []
        for row in rows:
            if not row.get('topicId'):
                raise ImportErrorResponse('TOPIC_ID_REQUIRED', 'mappings 需要显式 topicId 列')
            entry = {'topicId': row['topicId']}
            for key in ('grade', 'term', 'unit', 'chapter', 'note'):
                value = metadata_value(key, row, metadata, params) if key in ('grade', 'term') else row.get(key)
                if value is not None and value != '':
                    if key == 'grade':
                        try:
                            value = int(value)
                        except (TypeError, ValueError) as exc:
                            raise ImportErrorResponse('METADATA_REQUIRED', 'grade 必须为整数') from exc
                    entry[key] = value
            entries.append(entry)
        mapping = dict(edition=meta['edition'], label=params.get('label'), status='draft',
                       source=params.get('source'), entries=entries)
        return text, dict(topics=[], dependencies=[], questions=[], mappings=mapping, standards_suggestions=[])

    config = SUBJECTS[prefix]
    current = service.data / 'topics.json'
    from .store import read_json
    existing = read_json(current)
    topics = []
    counters = {}
    for row in rows:
        resolved = {}
        for key in ('grade', 'term'):
            value = metadata_value(key, row, metadata, params)
            if value is None:
                raise ImportErrorResponse('METADATA_REQUIRED', 'expand 需要明确 grade 和 term')
            resolved[key] = value
        try:
            grade = int(resolved['grade'])
        except ValueError as exc:
            raise ImportErrorResponse('METADATA_REQUIRED', 'grade 必须为整数') from exc
        stems = [stem for low, high, stem in config['stages'] if low <= grade <= high]
        if not stems:
            raise ImportErrorResponse('GRADE_UNSUPPORTED', '该学科没有对应学段 ID 规则')
        stem = stems[0]
        if stem not in counters:
            ids = [int(t['id'][len(stem):]) for t in existing
                   if re.fullmatch(re.escape(stem) + r'\d{3,}', t['id'])]
            if not ids:
                raise ImportErrorResponse('ID_RULE_UNCONFIRMED', '现库无法确认配置中的 ID 续号规则')
            counters[stem] = max(ids)
        counters[stem] += 1
        name = row.get('topic_name', '').strip()
        if not name:
            raise ImportErrorResponse('PARSE_FAILED', '目录缺少 topic_name')
        topics.append(dict(id=stem + f'{counters[stem]:03d}', name=name, subject=config['subject'],
                           grade=grade, semester=resolved['term'], chapter=row.get('chapter') or row.get('unit') or '目录导入',
                           domain=params.get('domain') or config['domain'], type='conceptual',
                           description='导入草稿，待审：' + name,
                           mastery_criteria=['导入草稿，待审：补充掌握要求', '导入草稿，待审：补充判断依据'],
                           diagnostic_question={'question': '导入草稿，待审：请补充诊断题（' + name + '）',
                                                'answer': '导入草稿，待审：请补充参考答案'},
                           standards=[], external=False))
    dependencies = [dict(topicId=after['id'], prerequisiteId=before['id'], strength='soft',
                         reason='目录顺序推断，待审') for before, after in zip(topics, topics[1:])
                    if (before['grade'], before['semester']) == (after['grade'], after['semester'])]
    suggestions = [dict(topicId=t['id'], suggestedStandardIds=[], confidence='inferred',
                        note='导入草稿，待审：未自动挂靠课标；domain 仅为建议') for t in topics]
    return text, dict(topics=topics, dependencies=dependencies, questions=[], mappings={}, standards_suggestions=suggestions)
