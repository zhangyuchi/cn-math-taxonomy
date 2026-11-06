"""文本卷切题与同学科最佳匹配挂点；机器评分保留报告供人工复核。"""
import csv
import io
import re

from .store import SUBJECTS, ImportErrorResponse, read_json
from .paper_ocr import parse_choices, structure_paper

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


def _rank_topics(prompt, topics):
    """按名称包含与不同二字片段打分，同分按 topicId 字典序升序。

    不设置最低分：零分候选也参与兜底。返回全部排名，供选点和报告共用，
    显式点即使不在前五名也能保留其真实规则得分；规则分不是置信概率。
    """
    prompt = prompt.casefold()
    ranked = []
    for topic in topics:
        name = topic['name'].casefold()
        grams = {name[i:i+2] for i in range(max(0, len(name)-1))}
        keywords = sorted(word for word in grams if word in prompt)
        name_matched = bool(name) and name in prompt
        ranked.append(dict(topicId=topic['id'], name=topic['name'],
                           score=(20 if name_matched else 0) + len(keywords),
                           nameMatched=name_matched, matchedKeywords=keywords))
    return sorted(ranked, key=lambda row: (-row['score'], row['topicId']))


def parse_paper(service, meta, content, suffix=None, *, ocr=False):
    """将成功块转换为每题有同科挂点的待审题数组；无候选或零题明确失败。

    保留合法显式点，其余采用规则最高分，零分也兜底；跨科显式点仍拒绝。
    评分与候选仅写入初始解析报告，不污染正式题 schema。
    文本缺答案/题干的块进入unparsed；PDF 自动切题且不发布残缺的部分卷。
    """
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ImportErrorResponse('ENCODING_REQUIRED', '试卷文本须为UTF-8') from exc
    suffix = suffix or '.' + meta['sourceFiles'][0].rsplit('.', 1)[-1]
    structure = None
    if ocr:
        rows, structure = structure_paper(text)
    else:
        rows = _blocks(text, suffix)
    prefix = meta['subjectPrefix']
    subject = SUBJECTS[prefix]['subject']
    topics = [t for t in read_json(service.data / 'topics.json')
              if t['id'].startswith(prefix + '_') and not t.get('external')
              and (t.get('subject') == subject or subject == 'math' and t.get('subject') is None)]
    if not topics:
        raise ImportErrorResponse('NO_TOPIC_CANDIDATES', f'学科 {prefix} 没有可用的现有知识点，无法自动挂点')
    index = {t['id']: t for t in topics}
    questions, unparsed, matches = [], [], []
    for number, row in enumerate(rows, 1):
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
            ranked = _rank_topics(row['prompt'], topics)
            explicit = supplied in index
            selected = next(c for c in ranked if c['topicId'] == supplied) if explicit else ranked[0]
            tid = selected['topicId']
            qid = tid + '_qdraft' + str(number)
            q = dict(id=qid, topicId=tid, type=row.get('type') or ('choice' if row.get('choices') else 'numeric'),
                     difficulty=row.get('difficulty') or 'standard', prompt=row['prompt'].strip(),
                     answer=row['answer'].strip(), explanation=row.get('explanation') or '导入草稿，待审：请补充解析')
            if row.get('choices'):
                q['choices'] = parse_choices(row['choices'])
            if row.get('verify'):
                q['verify'] = row['verify'].strip()
            if row.get('unordered'):
                if row['unordered'].lower() != 'true':
                    raise ValueError('unordered仅可为true')
                q['unordered'] = True
            questions.append(q)
            matches.append(dict(id=qid, match='explicit' if explicit else 'best_match',
                                suppliedTopicId=supplied or None, topicId=tid, score=selected['score'],
                                zeroEvidence=selected['score'] == 0, tieBreak='topicId_ascending',
                                suggestedTopicIds=[] if explicit else [c['topicId'] for c in ranked[:5]],
                                candidates=ranked[:5]))
        except (ValueError, TypeError) as exc:
            unparsed.append(dict(block=number, reason=str(exc), source=row))
    if ocr and (unparsed or not questions):
        raise ImportErrorResponse('PAPER_STRUCTURE_FAILED', 'OCR 试卷存在不完整题块',
                                  details=dict(unparsed=unparsed, structure=structure))
    if not questions:
        raise ImportErrorResponse('EMPTY_PAPER', '0题成功解析；请按编号块或CSV规范提供题干与答案')
    draft = dict(topics=[], dependencies=[], questions=questions, mappings={}, standards_suggestions=[])
    details = dict(unparsed=unparsed, matches=matches, matchesScope='initial_parse')
    if structure is not None:
        details['structure'] = structure
    return text, draft, details
