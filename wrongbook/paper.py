"""只读题库、混合选题及可离线打印的学生卷。"""
import base64
from collections import defaultdict
import copy
import hashlib
from html import escape
import json
from pathlib import Path

from .common import PREFIXES, require


class Catalog:
    """启动时读取一份正式题库快照，更新后重启服务，绝不写回JSON。"""
    def __init__(self, root):
        digest = hashlib.sha256()
        data = {}
        for name in ('topics', 'questions', 'dependencies'):
            raw = (Path(root) / (name + '.json')).read_bytes()
            digest.update(hashlib.sha256(raw).digest())
            data[name] = json.loads(raw)
            require(isinstance(data[name], list), '题库应为数组')
        self.digest = digest.hexdigest()
        topics = {t['id'] for t in data['topics']}
        self.questions = {}
        self.by_topic, self.prereqs = defaultdict(list), defaultdict(set)
        for q in data['questions']:
            require(q['id'] not in self.questions and q['topicId'] in topics, '题库题ID重复或知识点不存在')
            self.questions[q['id']] = q
            self.by_topic[q['topicId']].append(q)
        for group in self.by_topic.values():
            group.sort(key=lambda q: q['id'])
        for edge in data['dependencies']:
            if edge['topicId'] in topics and edge['prerequisiteId'] in topics:
                self.prereqs[edge['topicId']].add(edge['prerequisiteId'])

    @staticmethod
    def subject(topic):
        """按现有十学科知识点前缀确定学科，不信任请求体自报归属。"""
        return PREFIXES.get(topic.split('_', 1)[0])

    @staticmethod
    def usable(question):
        """v1只呈现现有四类独立题干；图片必须为inline SVG，避免远程资源依赖。"""
        if question.get('type') not in ('choice', 'numeric', 'short', 'self') or not question.get('prompt'):
            return False
        figures = [question.get('figure')] + [c.get('figure') for c in question.get('choices', [])]
        return all(not f or (isinstance(f, str) and f.lstrip().startswith('<svg')) for f in figures)


def assemble(catalog, wrongs, topics, seen, subject, count, wrong_count, missing):
    """先错题再同点/直接先修未见题，缺新题才补剩余错题，永不跨点乱补。

    同卷按ID去重；同一孩子的correct/skip/wrong都进入seen。缺额显式返回。
    """
    selected, ids = [], set()
    def add(q, source):
        if len(selected) < count and q['id'] not in ids:
            selected.append((q, source)); ids.add(q['id'])
    for q in wrongs[:wrong_count]:
        add(q, 'wrong')
    prerequisites = set().union(*(catalog.prereqs[t] for t in topics)) - topics
    for pool, source in [(topics, 'same-topic'), (prerequisites, 'prerequisite')]:
        for topic in sorted(pool):
            if catalog.subject(topic) != subject:
                continue
            for q in catalog.by_topic[topic]:
                if q['id'] not in seen and catalog.usable(q):
                    add(q, source)
                if len(selected) == count:
                    break
            if len(selected) == count:
                break
    for q in wrongs[wrong_count:]:
        add(q, 'wrong')
    student, keys = [], []
    for i, (q, source) in enumerate(selected, 1):
        row = {k: copy.deepcopy(q[k]) for k in ('type', 'prompt', 'difficulty', 'figure', 'hideChoiceText') if k in q}
        if 'choices' in q:
            row['choices'] = [{k: c[k] for k in ('key', 'text', 'figure') if k in c} for c in q['choices']]
        row.update(order=i, questionId=q['id'], topicId=q['topicId'], subject=subject, source=source)
        student.append(row)
        key = {k: copy.deepcopy(q[k]) for k in ('answer', 'explanation', 'accept', 'unordered', 'percent') if k in q}
        key.update(questionId=q['id'], order=i)
        keys.append(key)
    wrong_actual = sum(source == 'wrong' for _, source in selected)
    return dict(version=1, subject=subject, strategy={'count': count, 'wrongCount': wrong_count, 'prerequisiteDepth': 1},
                student={'title': '错题复习卷', 'questions': student}, answerKey=keys,
                stats=dict(requested=count, actual=len(selected), wrong=wrong_actual, new=len(selected)-wrong_actual,
                           shortfall=count-len(selected), missingQuestionIds=sorted(set(missing))))


def render_html(paper):
    """将学生卷转为离线A4 HTML；所有文本转义，SVG仅在隔离image上下文解析。

    按现有诊断规则隐藏带图选项名称；不将答案/身份/原始作答嵌入HTML。
    """
    def figure(value):
        if not value:
            return ''
        encoded = base64.b64encode(value.encode()).decode('ascii')
        return '<img alt="题图" src="data:image/svg+xml;base64,' + encoded + '">'
    def safe(value):
        return escape(str(value), quote=True)
    items = []
    for q in paper['student']['questions']:
        item = '<section><p class="prompt">' + safe(q['order']) + '. ' + safe(q['prompt']) + '</p>' + figure(q.get('figure'))
        if q.get('choices'):
            item += '<div class="choices">'
            for c in q['choices']:
                hidden = q.get('hideChoiceText') and c.get('figure')
                item += '<div class="choice"><b>' + safe(c['key']) + '.</b> '
                if not hidden:
                    item += safe(c.get('text', ''))
                item += figure(c.get('figure')) + '</div>'
            item += '</div>'
        item += '<div class="answer-space">作答：________________________________</div></section>'
        items.append(item)
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>错题复习卷</title>
<style>@page{size:A4;margin:16mm}body{font:16px/1.7 system-ui,sans-serif;max-width:180mm;margin:24px auto;color:#111;padding:12px}
h1{text-align:center;font-size:24px}section{break-inside:avoid;border-bottom:1px solid #ddd;padding:10px 0 22px}
.prompt{white-space:pre-wrap;overflow-wrap:anywhere}.choices{display:flex;flex-wrap:wrap;gap:12px}.choice{min-width:120px}
img{display:block;max-width:100%;width:180px;max-height:180px}.answer-space{margin:16px 0;min-height:40px}
@media print{body{margin:0;padding:0}}</style><h1>错题复习卷</h1><p>姓名：____________ 日期：____________</p>''' + ''.join(items) + '</html>'
