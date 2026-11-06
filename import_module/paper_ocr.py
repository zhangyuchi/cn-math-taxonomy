"""从 OCR 自动切题；结构歧义失败，缺答案和题图交给草稿风险报告。"""
from fractions import Fraction
from html.parser import HTMLParser
import json
import re

from .store import ImportErrorResponse

QUESTION = re.compile(r'^(?:Q\s*)?(\d+)\s*(?:\.(?!\d)|[．、:：)）])\s*(.*)$', re.I)
SECTION = re.compile(r'^[一二三四五六七八九十百]+[、.．]\s*.+$')
PART = re.compile(r'^第\s*[一二三四五六七八九十百\d]+\s*部分\s*.*$')
RUBRIC_NUMBER = r'[\d一二三四五六七八九十百两]+'
RUBRIC = re.compile(r'^(?:解答应写出文字说明[、，,]演算步骤或证明过程|'
                    r'本(?:大题|卷|试卷)共\s*' + RUBRIC_NUMBER + r'\s*(?:分|页|道?题)'
                    r'(?:[，,、；;]\s*(?:(?:共|满分)\s*' + RUBRIC_NUMBER + r'\s*(?:分|页|道?题)|'
                    r'每(?:小)?题\s*' + RUBRIC_NUMBER + r'\s*分))*|'
                    r'每(?:小)?题\s*' + RUBRIC_NUMBER + r'\s*分)[。．.]?$')
ANSWER_HEADING = re.compile(r'^(?:参考答案(?:及解析|与解析)?|答案及解析|答案与解析|答案解析|参考解答|答案)\s*[:：]?\s*$')
LABEL = re.compile(r'【\s*(答案|参考答案|解析|分析|详解|解答|难度|题型)\s*】\s*[:：]?|'
                   r'(?:^|\n)\s*(答案|参考答案|解析|分析|详解|解答|难度|题型|'
                   r'prompt|answer|explanation|type|difficulty|topicId|choices|verify|unordered)\s*[:：]')
KEYS = {'答案': 'answer', '参考答案': 'answer', '解答': 'answer', '解析': 'explanation',
        '分析': 'explanation', '详解': 'explanation', '难度': 'difficulty', '题型': 'type'}
KEYS.update({key: key for key in ('prompt', 'answer', 'explanation', 'type', 'difficulty', 'topicId',
                                 'choices', 'verify', 'unordered')})
OPTION = re.compile(r'(?:^|\s)([A-DＡ-Ｄ])\s*[.．、:：)）]\s*')
BRACKET_OPTION = re.compile(r'(?:\\left\s*)?[(（]\s*'
                            r'(?:(?:\\(?:mathbf|mathrm|text|mathit))\s*\{\s*)?'
                            r'([A-DＡ-Ｄ])\s*\$?\s*(?:\}\s*)?(?:\\right\s*)?[)）]\s*')
NUMBER = re.compile(r'-?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?(?:\s*[,，]\s*-?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?)*')


class _Table(HTMLParser):
    """仅提取 MinerU HTML 表的单元格；跨行列合并不能猜测题号归属。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cell = [], None

    def handle_starttag(self, tag, attrs):
        """收集表行和单元格边界，拒绝合并单元格。"""
        if tag in ('td', 'th'):
            if self.cell is not None or any(k in ('rowspan', 'colspan') and v != '1' for k, v in attrs):
                raise ValueError('答案表含嵌套或合并单元格')
            self.cell = ''
        elif tag == 'tr':
            self.rows.append([])
        elif tag == 'br' and self.cell is not None:
            self.cell += ' '

    def handle_data(self, data):
        """保留单元格文本及其内嵌公式。"""
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        """关闭单元格时检查所属行并保留内容。"""
        if tag in ('td', 'th'):
            if self.cell is None or not self.rows:
                raise ValueError('答案表标签不完整')
            self.rows[-1].append(self.cell.strip())
            self.cell = None


def _html_table(match):
    """把简单 HTML 表转换为同一答案表解析入口，保留单元格中的竖线。"""
    table = _Table()
    table.feed(match[0])
    table.close()
    if table.cell is not None or not table.rows or any(not row for row in table.rows):
        raise ValueError('答案表标签不完整')
    return '\n' + '\n'.join('| ' + ' | '.join(c.replace('|', r'\|') for c in row) + ' |' for row in table.rows) + '\n'


def _line(line):
    """移除排版标题和粗体包裹，保留公式与原题正文。"""
    line = re.sub(r'^\s*#{1,6}\s*', '', line).strip()
    return re.sub(r'\*\*([^*]+)\*\*', r'\1', line)


def _question(line):
    """统一普通印刷题号与已有 Q1 插件标题，避免兼容分支绕过切题。"""
    match = QUESTION.fullmatch(line) or re.fullmatch(r'Q(\d+)\s*(.*)', line, re.I)
    return (match[1], match[2]) if match else None


def parse_choices(value):
    """接受已有 CSV/键值格式的 JSON 选项或 A:文本行。"""
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


def _checked_choices(value):
    """OCR 选项必须满足正式形状；空选项不能降级为简答题。"""
    choices = parse_choices(value)
    if not isinstance(choices, list) or not 3 <= len(choices) <= 4 or any(
            not isinstance(c, dict) or set(c) != {'key', 'text'} or
            not isinstance(c['text'], str) or not isinstance(c['key'], str) for c in choices):
        raise ValueError('选择题选项结构非法或数量不足')
    choices = [dict(key=c['key'], text=c['text'].strip()) for c in choices]
    if ([c['key'] for c in choices] != list('ABCD')[:len(choices)] or
            any(not c['text'] for c in choices) or len({c['text'] for c in choices}) != len(choices)):
        raise ValueError('选择题选项重复、不连续或内容缺失')
    return choices


def _fail(code, reason, structure, source):
    """把原文与题号诊断交回 worker，失败不发布部分题目。"""
    raise ImportErrorResponse(code, reason, details={
        'structure': structure, 'unparsed': [{'reason': reason, 'source': source}]})


def _fields(body):
    """提取中文字段标签；重复标签仅在内容一致时允许合并。"""
    matches = list(LABEL.finditer(body))
    prefix = body[:matches[0].start()].strip() if matches else body.strip()
    fields = {'prompt': prefix} if prefix else {}
    for index, match in enumerate(matches):
        key = KEYS[match[1] or match[2]]
        value = body[match.end():matches[index+1].start() if index+1 < len(matches) else len(body)].strip()
        if key in fields and fields[key] != value:
            raise ValueError('相同字段出现冲突：' + key)
        fields[key] = value
    return fields


def _answer_rows(lines):
    """将卷末编号答案及横向/纵向 Markdown 答案表转换为编号、答案记录。"""
    content = re.sub(r'<table\b[^>]*>.*?</table\s*>', _html_table, '\n'.join(lines), flags=re.I | re.S)
    records, current, table = [], None, []

    def flush_table():
        nonlocal table
        if not table:
            return
        header = table[0]
        if header[0] in ('题号', '题目') and len(table) >= 2 and table[1][0] in ('答案', '参考答案'):
            if len(header) != len(table[1]) or len(table) != 2:
                raise ValueError('横向答案表行列不一致')
            records.extend((n, {'answer': a}) for n, a in zip(header[1:], table[1][1:]))
        elif header[:2] in (['题号', '答案'], ['题号', '参考答案']):
            for cells in table[1:]:
                if len(cells) != len(header) or len(cells) not in (2, 3):
                    raise ValueError('纵向答案表行列不一致')
                fields = {'answer': cells[1]}
                if len(cells) == 3:
                    if header[2] != '解析':
                        raise ValueError('答案表包含未知列')
                    fields['explanation'] = cells[2]
                records.append((cells[0], fields))
        else:
            raise ValueError('无法识别的答案表')
        table = []

    for line in content.splitlines() + ['']:
        line = line.strip()
        if line.startswith('|'):
            if current:
                records.append(current)
                current = None
            cells = [cell.strip().replace(r'\|', '|') for cell in re.split(r'(?<!\\)\|', line.strip('|'))]
            if not all(re.fullmatch(r':?-+:?', c) for c in cells):
                table.append(cells)
            continue
        flush_table()
        match = _question(line)
        if match:
            if current:
                records.append(current)
            current = match
        elif current and line:
            current = (current[0], current[1] + '\n' + line)
        elif line and not SECTION.fullmatch(line):
            raise ValueError('卷末答案存在未编号内容：' + line)
    if current:
        records.append(current)
    result = {}
    for number, body in records:
        if not number.isdecimal():
            raise ValueError('答案表题号不是数字：' + number)
        number = str(int(number))
        fields = _fields(body) if isinstance(body, str) else body
        if 'answer' not in fields:
            fields['answer'] = fields.pop('prompt', '')
        fields.pop('prompt', None)
        if number in result and result[number] != fields:
            raise ValueError('同一题号有冲突答案：' + number)
        result[number] = fields
    return result


def structure_paper(text):
    """结构完整时形成草稿字段行；内容缺失留空，结构歧义整卷失败。

    原卷答案只按唯一印刷题号对齐，不猜测缺失答案或重复分节编号的归属。
    已知说明仅在分节标题后的边界跳过，正文同句保留；原文和归一化留证。
    """
    blocks, answer_lines, preamble, unassigned = [], [], [], []
    ignored_notes, normalizations = [], []
    current, section, answers_started = None, '', False
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = _line(raw)
        if re.fullmatch(r'第\s*\d+\s*页(?:\s*[/，,共]\s*\d+\s*页)?', line):
            continue
        if ANSWER_HEADING.fullmatch(line):
            answers_started, current = True, None
            continue
        if SECTION.fullmatch(line) or PART.fullmatch(line):
            section, current = line, None
            continue
        if answers_started:
            answer_lines.append(line)
            continue
        if current is None and section and RUBRIC.fullmatch(line):
            ignored_notes.append(dict(line=line_number, section=section, text=line, source=raw))
            continue
        heading = _question(line)
        if heading:
            current = dict(number=str(int(heading[0])), section=section, lines=[heading[1]])
            blocks.append(current)
        elif current is not None:
            current['lines'].append(line)
        elif line:
            (unassigned if blocks else preamble).append(line)
    structure = dict(method='ocr-numbered-v1', questionNumbers=[b['number'] for b in blocks],
                     defaultDifficulty='standard', preamble=preamble, ignoredNotes=ignored_notes,
                     choiceNormalizations=normalizations)
    if unassigned:
        _fail('PAPER_STRUCTURE_FAILED', '首题之后存在未归属题块，不能当作卷首说明丢弃', structure, unassigned)
    if not blocks:
        _fail('PAPER_STRUCTURE_FAILED', 'OCR 文本没有可识别的题号', structure, text)
    numbers = structure['questionNumbers']
    if len(set(numbers)) != len(numbers) or any(int(b) != int(a)+1 for a, b in zip(numbers, numbers[1:])):
        _fail('PAPER_STRUCTURE_FAILED', '题号重复、不连续或顺序歧义', structure, blocks)
    try:
        answers = _answer_rows(answer_lines)
        if set(answers) - set(numbers):
            raise ValueError('卷末答案包含未匹配题号')
    except ValueError as exc:
        _fail('PAPER_STRUCTURE_FAILED', str(exc), structure, answer_lines)
    rows = []
    for block in blocks:
        try:
            row = _fields('\n'.join(block['lines']))
            for key, value in answers.get(block['number'], {}).items():
                if row.get(key) and row[key] != value:
                    raise ValueError('正文与卷末字段冲突：' + key)
                row[key] = value
            row['answer'] = row.get('answer', '').strip()
            prompt = row.get('prompt', '')
            options = list(OPTION.finditer(prompt))
            choice_context = (('选择题' in block['section'] and '非选择题' not in block['section']) or
                              row.get('type') in ('choice', '选择题'))
            brackets = list(BRACKET_OPTION.finditer(prompt)) if choice_context else []
            if brackets:
                # 完整旧式标签优先，选项正文中的点(A)不能抢占 A. 等标签。
                dotted = [m for m in options if not any(b.start() <= m.start(1) < b.end() for b in brackets)]
                if dotted:
                    if {b[1] for b in brackets} - {m[1] for m in dotted}:
                        raise ValueError('选项混用标签格式，不能确定完整归属')
                    options, brackets = dotted, []
                else:
                    options = brackets
            explicit_choices = _checked_choices(row['choices']) if 'choices' in row else None
            if options:
                row['prompt'] = prompt[:options[0].start()].strip()
                extracted = [dict(key=chr(ord(m[1]) - 0xFEE0) if ord(m[1]) > 127 else m[1],
                                       text=prompt[m.end():options[i+1].start() if i+1 < len(options) else len(prompt)].strip())
                                  for i, m in enumerate(options)]
                if brackets:
                    normalizations.append(dict(number=block['number'], source=prompt,
                                               originalOrder=[c['key'] for c in extracted],
                                               requiresHumanReview=True))
                    extracted.sort(key=lambda c: c['key'])
                row['choices'] = _checked_choices(extracted)
                if explicit_choices is not None and explicit_choices != row['choices']:
                    raise ValueError('正文选项与显式 choices 冲突')
            elif explicit_choices is not None:
                row['choices'] = explicit_choices
            if 'choices' in row:
                keys = [c['key'] for c in row['choices']]
                answer = row['answer'].strip().strip('$').strip().upper()
                if len(answer) == 1 and 'Ａ' <= answer <= 'Ｄ':
                    answer = chr(ord(answer) - 0xFEE0)
                if answer and answer not in keys:
                    raise ValueError('选择题答案不是单一有效选项')
                row['answer'], row['type'] = answer, 'choice'
            elif choice_context:
                raise ValueError('选择题未识别到选项')
            else:
                row['type'] = row.get('type') or ('numeric' if NUMBER.fullmatch(row['answer']) else 'short')
            if not row.get('prompt', '').strip():
                raise ValueError('题干为空')
            row['difficulty'] = {'简单': 'basic', '容易': 'basic', '中等': 'standard', '困难': 'challenge'}.get(
                row.get('difficulty'), row.get('difficulty') or 'standard')
            row['type'] = {'填空题': 'numeric', '解答题': 'short', '简答题': 'short'}.get(row['type'], row['type'])
            if row['type'] == 'numeric':
                # 正式 numeric schema 仅接受数值；公式/证明答案仍完整保留为 short。
                try:
                    if not NUMBER.fullmatch(row['answer']):
                        raise ValueError('非数值答案')
                    for value in re.split('[,，]', row['answer']):
                        Fraction(value.strip())
                except (ValueError, ZeroDivisionError):
                    row['type'] = 'short'
            if row['type'] not in ('choice', 'numeric', 'short', 'self') or row['difficulty'] not in ('basic', 'standard', 'challenge'):
                raise ValueError('未知题型或难度')
            rows.append(row)
        except (ValueError, TypeError) as exc:
            _fail('PAPER_STRUCTURE_FAILED', str(exc), structure, block)
    return rows, structure
