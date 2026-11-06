"""服务端错题本契约：真实SQLite/HTTP、身份隔离、组卷与恢复。"""
from contextlib import closing
import importlib.util
import http.client
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


class WrongbookCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('wrongbook.store'), '缺少服务端持久错题本')
        from wrongbook.store import Service, WrongbookError
        self.Service, self.Error = Service, WrongbookError
        self.temp = tempfile.TemporaryDirectory(prefix='wrongbook-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = self.root / 'catalog'
        self.catalog.mkdir()
        self.questions = [
            {'id': 'cnm_p002_q1', 'topicId': 'cnm_p002', 'type': 'choice', 'prompt': '<script>原题</script>',
             'answer': 'A', 'explanation': '隐藏解析', 'verify': 'secret', 'hideChoiceText': True,
             'figure': SVG, 'choices': [{'key': 'A', 'text': '隐藏答案名称', 'figure': SVG}, {'key': 'B', 'text': '普通选项'}]},
            {'id': 'cnm_p002_q2', 'topicId': 'cnm_p002', 'type': 'numeric', 'prompt': '同点新题', 'answer': '2'},
            {'id': 'cnm_p002_q3', 'topicId': 'cnm_p002', 'type': 'numeric', 'prompt': '已答对题', 'answer': '3'},
            {'id': 'cnm_p002_q4', 'topicId': 'cnm_p002', 'type': 'numeric', 'prompt': '较早错题', 'answer': '4'},
            {'id': 'cnm_p001_q1', 'topicId': 'cnm_p001', 'type': 'numeric', 'prompt': '先修新题', 'answer': '1'},
            {'id': 'cnm_p003_q1', 'topicId': 'cnm_p003', 'type': 'numeric', 'prompt': '无关题', 'answer': '3'},
            {'id': 'cne_p001_q1', 'topicId': 'cne_p001', 'type': 'choice', 'prompt': '英语', 'answer': 'A'}]
        self.write_catalog()
        self.workspace = self.root / 'private'
        self.service = Service(self.workspace, self.catalog)
        self.token = self.service.provision('family-a', self.workspace / 'a.token')
        self.token_b = self.service.provision('family-b', self.workspace / 'b.token')

    def write_catalog(self):
        topics = [{'id': x} for x in ['cnm_p001', 'cnm_p002', 'cnm_p003', 'cne_p001']]
        for name, value in [('questions', self.questions), ('topics', topics),
                            ('dependencies', [{'topicId': 'cnm_p002', 'prerequisiteId': 'cnm_p001'}])]:
            (self.catalog / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False))

    def attempt(self, eid='a1', qid='cnm_p002_q1', **kwargs):
        topic = qid.rsplit('_q', 1)[0]
        return {**dict(eventId=eid, questionId=qid, topicId=topic, subject='math',
                       answeredAt='2026-11-06T13:00:00+08:00', sourceSession='session-1', result='wrong'), **kwargs}

    def record(self, *attempts, user='family-a', child='child-a'):
        return self.service.record(user, child, {'attempts': list(attempts)})

    def assert_error(self, code, fn):
        with self.assertRaises(self.Error) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def seed(self):
        a = self.attempt('older', 'cnm_p002_q4')
        a['answeredAt'] = '2026-11-05T05:00:00Z'
        seen = self.attempt('seen', 'cnm_p002_q3'); seen['result'] = 'correct'
        self.record(a, self.attempt(), seen)

    def diagnosis(self):
        return {'sourceSession': 'export-session', 'subject': 'math', 'diagnosis': {
            'tool': 'cn-math-taxonomy/diagnose', 'version': 2, 'generatedAt': '2026-11-06T05:00:00Z',
            'answers': [{'order': 1, 'questionId': 'cnm_p002_q1', 'topicId': 'cnm_p002',
                         'result': 'wrong', 'userAnswer': 'B'},
                        {'order': 2, 'questionId': 'cnm_p002_q3', 'topicId': 'cnm_p002',
                         'result': 'correct', 'userAnswer': '3'}]}}



class WrongbookTests(WrongbookCase):
    def test_record_reopen_permissions_and_identity(self):
        a = self.attempt(userAnswer='5')
        self.assertEqual(self.record(a), {'inserted': 1, 'replayed': 0})
        service = self.Service(self.workspace, self.catalog)
        self.assertEqual(service.authenticate(self.token), 'family-a')
        row = service.list_attempts('family-a', 'child-a')['items'][0]
        self.assertEqual(row['userAnswer'], '5')
        self.assertEqual(row['answeredAt'], '2026-11-06T05:00:00.000000Z')
        self.assertEqual(row['timeSource'], 'client')
        self.assertEqual(self.workspace.stat().st_mode & 0o777, 0o700)
        for p in self.workspace.iterdir():
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.token.encode(), (self.workspace / 'wrongbook.sqlite3').read_bytes())

    def test_idempotency_and_atomic_conflict(self):
        a = self.attempt()
        self.record(a)
        self.assertEqual(self.record(a), {'inserted': 0, 'replayed': 1})
        changed = {**a, 'result': 'correct'}
        self.assert_error('EVENT_CONFLICT', lambda: self.record(self.attempt('new'), changed))
        self.assertEqual(self.service.list_attempts('family-a', 'child-a')['total'], 1)
        self.record(self.attempt('another'))
        self.assertEqual(self.service.list_attempts('family-a', 'child-a')['total'], 2)

    def test_master_reopen_delete_and_new_wrong(self):
        self.record(self.attempt(), self.attempt('repeat'))
        self.service.mark('family-a', 'child-a', 'cnm_p002_q1', {'mastered': True})
        self.assertEqual(self.service.list_attempts('family-a', 'child-a')['total'], 0)
        self.assertEqual(self.service.list_attempts('family-a', 'child-a', status='mastered')['total'], 2)
        self.record(self.attempt('new-wrong'))
        self.assertEqual(self.service.list_attempts('family-a', 'child-a')['total'], 1)
        self.service.mark('family-a', 'child-a', 'cnm_p002_q1', {'mastered': False})
        self.assertEqual(self.service.list_attempts('family-a', 'child-a')['total'], 3)
        self.service.delete('family-a', 'child-a', 'cnm_p002_q1')
        self.assertEqual(self.service.list_attempts('family-a', 'child-a', status='all')['total'], 0)

    def test_validation_batch_types_and_pagination(self):
        for bad in [{'subject': 'english'}, {'topicId': 'cnm_p001'}, {'result': 'bad'},
                    {'answeredAt': '2026-11-06'}, {'answeredAt': 12}, {'timeSource': 'made-up'},
                    {'userAnswer': {}}, {'userId': 'family-b'}, {'eventId': '../../etc'}]:
            self.assert_error('INVALID_INPUT', lambda bad=bad: self.record({**self.attempt(), **bad}))
        self.assert_error('INVALID_INPUT', lambda: self.record(self.attempt(), {**self.attempt('bad'), 'questionId': 'missing'}))
        self.assertEqual(self.service.list_attempts('family-a', 'child-a')['total'], 0)
        self.record(self.attempt('a'), self.attempt('b'), self.attempt('c', result='skip'))
        page = self.service.list_attempts('family-a', 'child-a', limit=1, offset=1)
        self.assertEqual(page['total'], 3)
        self.assertEqual(len(page['items']), 1)
        for kw in [{'limit': 201}, {'offset': -1}, {'status': 'bad'}, {'limit': True}, {'offset': 2**63}]:
            self.assert_error('INVALID_INPUT', lambda kw=kw: self.service.list_attempts('family-a', 'child-a', **kw))

    def test_diagnosis_repeat_export_preserves_first_time_and_atomicity(self):
        body = self.diagnosis()
        self.assertEqual(self.service.import_diagnosis('family-a', 'child-a', body)['inserted'], 2)
        body['diagnosis']['generatedAt'] = '2026-11-07T05:00:00Z'
        self.assertEqual(self.service.import_diagnosis('family-a', 'child-a', body)['replayed'], 2)
        row = self.service.list_attempts('family-a', 'child-a')['items'][0]
        self.assertEqual(row['timeSource'], 'export')
        self.assertEqual(row['answeredAt'], '2026-11-06T05:00:00.000000Z')
        body['diagnosis']['answers'][0]['result'] = 'correct'
        self.assert_error('EVENT_CONFLICT', lambda: self.service.import_diagnosis('family-a', 'child-a', body))
        body = self.diagnosis(); body['diagnosis']['answers'][1]['questionId'] = None
        self.assert_error('INVALID_INPUT', lambda: self.service.import_diagnosis('family-a', 'other-child', body))
        self.assertEqual(self.service.list_attempts('family-a', 'other-child')['total'], 0)

    def test_mixed_paper_wrong_same_topic_prerequisite_seen_and_fallback(self):
        self.seed()
        p = self.service.create_paper('family-a', 'child-a', {'subject': 'math', 'count': 4, 'wrongCount': 1})
        qs = p['student']['questions']
        self.assertEqual([q['questionId'] for q in qs], ['cnm_p002_q1', 'cnm_p002_q2', 'cnm_p001_q1', 'cnm_p002_q4'])
        self.assertEqual([q['source'] for q in qs], ['wrong', 'same-topic', 'prerequisite', 'wrong'])
        self.assertEqual(p['stats']['new'], 2)
        self.assertEqual(p['stats']['shortfall'], 0)
        self.assertEqual(len(set(q['questionId'] for q in qs)), 4)
        self.assertNotIn('answer', qs[0]); self.assertNotIn('verify', qs[0]); self.assertNotIn('explanation', qs[0])
        self.assertTrue(qs[0]['hideChoiceText'])
        self.assertEqual(p['answerKey'][0]['answer'], 'A')
        self.assertEqual(self.service.get_paper('family-a', 'child-a', p['paperId']), p)
        self.assert_error('NOT_FOUND', lambda: self.service.get_paper('family-b', 'child-a', p['paperId']))
        self.assert_error('NOT_FOUND', lambda: self.service.get_paper('family-a', 'child-b', p['paperId']))

    def test_short_paper_mastered_seen_and_no_wrong(self):
        self.assert_error('NO_WRONG_QUESTIONS', lambda: self.service.create_paper('family-a', 'child-a', {'subject': 'math'}))
        self.seed()
        self.service.mark('family-a', 'child-a', 'cnm_p002_q4', {'mastered': True})
        p = self.service.create_paper('family-a', 'child-a', {'subject': 'math', 'count': 10})
        self.assertEqual(p['stats']['actual'], 3)
        self.assertEqual(p['stats']['shortfall'], 7)
        self.assertNotIn('cnm_p002_q4', [q['questionId'] for q in p['student']['questions']])
        for b in [{'count': 0}, {'count': 101}, {'wrongCount': 0}, {'count': 2, 'wrongCount': 3}, {'count': True}]:
            self.assert_error('INVALID_INPUT', lambda b=b: self.service.create_paper('family-a', 'child-a', {'subject': 'math', **b}))

    def test_partially_missing_wrong_still_contributes_related_new_questions(self):
        self.questions = [self.questions[0], self.questions[5],
                          dict(id='cnm_p003_q2', topicId='cnm_p003', type='numeric', prompt='部分缺失点新题', answer='2')]
        self.write_catalog()
        service = self.Service(self.workspace, self.catalog)
        service.record('family-a', 'child-a', {'attempts': [self.attempt(), self.attempt('missing-topic', 'cnm_p003_q1')]})
        self.questions = [q for q in self.questions if q['id'] != 'cnm_p003_q1']
        self.write_catalog()
        service = self.Service(self.workspace, self.catalog)
        p = service.create_paper('family-a', 'child-a', {'subject': 'math', 'count': 2, 'wrongCount': 1})
        self.assertEqual([q['questionId'] for q in p['student']['questions']], ['cnm_p002_q1', 'cnm_p003_q2'])
        self.assertEqual(p['stats']['missingQuestionIds'], ['cnm_p003_q1'])
        self.assertEqual(p['stats']['shortfall'], 0)

    def test_cli_paper_output_respects_additional_static_roots(self):
        from wrongbook.cli import save_paper
        self.record(self.attempt())
        paper = self.service.create_paper('family-a', 'child-a', {'subject': 'math', 'count': 1})
        static = self.root / 'configured-static'; static.mkdir(mode=0o700)
        with mock.patch.dict(os.environ, WRONGBOOK_STATIC_ROOTS=str(static)):
            self.assert_error('INVALID_PATH', lambda: save_paper(paper, static / 'paper.json', None))
            self.assert_error('INVALID_PATH', lambda: save_paper(paper, self.root / 'paper.json', static / 'paper.html'))
        self.assertFalse((static / 'paper.json').exists())
        self.assertFalse((static / 'paper.html').exists())

    def test_snapshot_survives_catalog_removal_but_retest_is_rejected(self):
        self.record(self.attempt())
        p = self.service.create_paper('family-a', 'child-a', {'subject': 'math', 'count': 2})
        self.questions = [q for q in self.questions if q['id'] != 'cnm_p002_q1']
        self.write_catalog()
        newer = self.Service(self.workspace, self.catalog)
        self.assertEqual(newer.get_paper('family-a', 'child-a', p['paperId']), p)
        self.assert_error('NO_WRONG_QUESTIONS', lambda: newer.create_paper('family-a', 'child-a', {'subject': 'math'}))
        self.assert_error('INVALID_INPUT', lambda: newer.record('family-a', 'child-a', {'attempts': [self.attempt('retest')]}))
        self.assert_error('INVALID_INPUT', lambda: newer.record('family-a', 'child-a', {'attempts': [self.attempt()]}))
        self.assertEqual(newer.list_attempts('family-a', 'child-a')['total'], 1)

    def test_print_is_student_only_escaped_and_preserves_isolated_figures(self):
        from wrongbook.paper import render_html
        self.record(self.attempt())
        p = self.service.create_paper('family-a', 'child-a', {'subject': 'math', 'count': 1})
        html = render_html(p)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('隐藏答案名称', html)
        self.assertNotIn('隐藏解析', html)
        self.assertIn('普通选项', html)
        self.assertEqual(html.count('data:image/svg+xml;base64,'), 2)
        self.assertIn('@page', html)

    def test_workspace_does_not_change_existing_public_directory_permissions(self):
        public = self.root / 'public-directory'; public.mkdir(); public.chmod(0o755)
        self.assert_error('INVALID_PATH', lambda: self.Service(public, self.catalog))
        self.assertEqual(public.stat().st_mode & 0o777, 0o755)

    def test_skips_correct_seen_cross_subject_and_batch_limit(self):
        self.record(self.attempt('skip', result='skip'))
        self.assertEqual(self.service.list_attempts('family-a', 'child-a')['items'][0]['result'], 'skip')
        english = self.attempt('english', 'cne_p001_q1', subject='english')
        self.record(english)
        p = self.service.create_paper('family-a', 'child-a', {'subject': 'math', 'count': 100})
        self.assertTrue(all(q['subject'] == 'math' for q in p['student']['questions']))
        self.assert_error('INVALID_INPUT', lambda: self.record(*[self.attempt(str(i)) for i in range(501)]))
        self.assert_error('INVALID_INPUT', lambda: self.record())

    def test_backup_restore_and_schema_guard(self):
        self.seed()
        p = self.service.create_paper('family-a', 'child-a', {'subject': 'math', 'count': 2})
        backup = self.root / 'backup.sqlite3'
        self.service.backup(backup)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assert_error('INVALID_PATH', lambda: self.service.backup(backup))
        restore = self.root / 'restored'; restore.mkdir(mode=0o700)
        shutil.copyfile(backup, restore / 'wrongbook.sqlite3')
        service = self.Service(restore, self.catalog)
        self.assertEqual(service.authenticate(self.token), 'family-a')
        self.assertEqual(service.get_paper('family-a', 'child-a', p['paperId']), p)
        self.assertEqual(service.list_attempts('family-a', 'child-a')['total'], 2)
        with closing(sqlite3.connect(restore / 'wrongbook.sqlite3')) as db, db:
            db.execute('PRAGMA user_version=999')
        self.assert_error('SCHEMA_VERSION', lambda: self.Service(restore, self.catalog))

    def test_private_paths_refuse_repo_static_root_symlinks_and_existing_token(self):
        self.assert_error('INVALID_PATH', lambda: self.Service(REPO / 'data/private', self.catalog))
        static = self.root / 'www'; static.mkdir()
        link = self.root / 'linked'; link.symlink_to(static, target_is_directory=True)
        self.assert_error('INVALID_PATH', lambda: self.Service(link / 'private', self.catalog, static_roots=[static]))
        self.assert_error('INVALID_PATH', lambda: self.service.backup(REPO / 'public-backup.sqlite3'))
        self.assert_error('INVALID_PATH', lambda: self.service.provision('new-user', self.workspace / 'a.token'))


class WrongbookHTTPTests(WrongbookCase):
    def setUp(self):
        super().setUp()
        from wrongbook.api import make_server
        self.server = make_server(self.service, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.base = '/wrongbook/v1/children/child-a'

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, method, path, body=None, token='a', headers=None, raw=None):
        headers = dict(headers or {})
        if token:
            headers['Authorization'] = 'Bearer ' + (self.token if token == 'a' else self.token_b)
        if body is not None:
            raw = json.dumps(body).encode(); headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.url + path, data=raw, method=method, headers=headers)
        try:
            response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5)
        except urllib.error.HTTPError as e:
            response = e
        with response:
            data = json.loads(response.read())
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            return response.status, data

    def test_http_authentication_isolation_mutation_and_paper(self):
        self.assertEqual(self.request('GET', self.base + '/attempts', token=None)[0], 401)
        self.assertEqual(self.request('POST', self.base + '/attempts', {'attempts': [self.attempt()]})[0], 200)
        self.assertEqual(self.request('GET', self.base + '/attempts', token='b')[1]['total'], 0)
        self.assertEqual(self.request('GET', self.base.replace('child-a','child-b') + '/attempts')[1]['total'], 0)
        for method, body in [('PATCH', {'mastered': True}), ('DELETE', None)]:
            self.assertEqual(self.request(method, self.base + '/questions/cnm_p002_q1', body, token='b')[0], 404)
        status, paper = self.request('POST', self.base + '/papers', {'subject': 'math', 'count': 2})
        self.assertEqual(status, 201)
        self.assertEqual(self.request('GET', self.base + '/papers/' + paper['paperId'], token='b')[0], 404)
        self.assertEqual(self.request('GET', self.base + '/papers/' + paper['paperId'])[1], paper)
        self.assertEqual(self.request('PATCH', self.base + '/questions/cnm_p002_q1', {'mastered': True})[0], 200)
        self.assertEqual(self.request('DELETE', self.base + '/questions/cnm_p002_q1')[0], 200)

    def test_oversized_length_header_returns_input_error(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.putrequest('POST', self.base + '/attempts')
            connection.putheader('Authorization', 'Bearer ' + self.token)
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Content-Length', '9' * 5000)
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            connection.close()

    def test_http_errors_origin_bounds_and_diagnosis(self):
        for data in [[], {'attempts': 'bad'}, {'attempts': [self.attempt()], 'userId': 'family-b'}]:
            self.assertEqual(self.request('POST', self.base + '/attempts', data)[0], 400)
        self.assertEqual(self.request('POST', self.base + '/attempts', raw=b'{', headers={'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request('POST', self.base + '/attempts', {'attempts': [self.attempt()]}, headers={'Origin': 'https://other.invalid'})[0], 403)
        # 只发送超限长度头：服务应在读请求体前拒绝，避免sendall与关闭连接竞态。
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.putrequest('POST', self.base + '/attempts')
            connection.putheader('Authorization', 'Bearer ' + self.token)
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Content-Length', str(2*1024*1024+1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()
        self.assertEqual(self.request('GET', self.base + '/attempts?limit=NaN')[0], 400)
        self.assertEqual(self.request('GET', self.base + '/attempts?userId=family-b')[0], 400)
        self.assertEqual(self.request('POST', self.base + '/diagnoses', self.diagnosis())[1]['inserted'], 2)


if __name__ == '__main__':
    unittest.main()
