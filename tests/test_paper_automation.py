"""大源文件及普通 OCR 版式的端到端回归，不调用真实重 OCR。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.request
import urllib.error
from unittest import mock

import test_import_module as base
from import_module.store import ImportErrorResponse
from import_module.api import make_server
from import_module.worker import run_once
from import_module.apply import apply_job

ROOT = base.ROOT
SCAN = (ROOT / 'tests/fixtures/paper-mineru.md').read_text()


class PaperAutomationTests(base.WorkspaceCase):
    def scan(self, text):
        """通过外部 OCR argv 写入未经结构化的原始文本。"""
        source = self.workspace / 'ocr-result.md'
        source.write_text(text, encoding='utf-8')
        command = [sys.executable, '-c',
                   'import shutil,sys; shutil.copyfile(sys.argv[1],sys.argv[sys.argv.index("--output")+1])',
                   str(source)]
        meta = self.service.create_job('paper', 'scan.pdf', b'%PDF-fixture', {'subjectPrefix': 'cnm'})
        with mock.patch.dict(os.environ, IMPORT_OCR_COMMAND=json.dumps(command)):
            run_once(self.service)
        return self.service.snapshot(meta['jobId'])

    def test_scan_is_split_without_rewriting_markdown(self):
        result = self.scan(SCAN)
        self.assertEqual(result['status'], 'needs_review', result['error'])
        qs = result['draft']['questions']
        self.assertEqual(len(qs), 3)
        self.assertEqual([q['answer'] for q in qs], ['B', '2', '两数之和为零。'])
        self.assertEqual([q['type'] for q in qs], ['choice', 'numeric', 'short'])
        self.assertEqual([q['choices'][1] for q in qs[:1]], [{'key': 'B', 'text': '$2$'}])
        self.assertIn('（2）说明理由', qs[2]['prompt'])
        self.assertNotIn('参考答案', qs[2]['prompt'])
        self.assertIn('$-2$', qs[0]['prompt'])
        self.assertTrue(all(isinstance(q['topicId'], str) and q['topicId'].startswith('cnm_')
                            and q['difficulty'] == 'standard' for q in qs))
        self.assertTrue(result['reports']['parse']['matches'][0]['suggestedTopicIds'])
        for q, match in zip(qs, result['reports']['parse']['matches']):
            self.assertEqual(q['topicId'], match['candidates'][0]['topicId'])
            self.assertEqual(match['score'], match['candidates'][0]['score'])
        self.assertEqual(result['reports']['coverage']['unresolved'], 0)
        self.assertTrue(result['reports']['validate']['ok'])
        self.assertEqual(result['reports']['parse']['unparsed'], [])
        self.assertEqual(result['reports']['parse']['structure']['questionNumbers'], ['1', '2', '3'])
        self.assertEqual(result['sourcePreview'], SCAN)
        self.assertEqual(self.formal_hashes(), self.baseline)

    def test_inline_answers_multiline_choices_and_latex(self):
        result = self.scan('''# 一、选择题
1. 哪个数是正数？
A. $-1$
B. $2$
C. $0$
D. $-2$
【答案】B
【解析】2 大于零。
# 二、填空题
2．计算一半：【答案】$\\frac{1}{2}$【解析】结果为二分之一。
''')
        self.assertEqual(result['status'], 'needs_review', result['error'])
        qs = result['draft']['questions']
        self.assertEqual(len(qs), 2)
        self.assertEqual(len(qs[0]['choices']), 4)
        self.assertEqual(qs[0]['explanation'], '2 大于零。')
        self.assertEqual(qs[1]['answer'], '$\\frac{1}{2}$')
        self.assertEqual(qs[1]['type'], 'short')

    def test_large_ocr_output_and_vertical_answer_table(self):
        text = '# ' + '页眉说明' * 100000 + '\n1. 计算1+1。\n2. 计算2+2。\n# 参考答案\n'
        text += '| 题号 | 答案 | 解析 |\n| --- | --- | --- |\n| 1 | 2 | 加法 |\n| 2 | 4 | 加法 |\n'
        result = self.scan(text)
        self.assertEqual(result['status'], 'needs_review', result['error'])
        self.assertGreater(len(self.service.read_file(result['jobId'], 'source/normalized.md')), 1024 * 1024)
        self.assertEqual([q['answer'] for q in result['draft']['questions']], ['2', '4'])

    def test_large_draft_can_receive_business_revision(self):
        result = self.scan(SCAN)
        draft = result['draft']
        draft['questions'][0]['explanation'] = '依据' * 200000
        self.service.revise(result['jobId'], draft, result['draftSha256'], '业务审核补充依据')
        self.assertEqual(self.service.draft(result['jobId']), draft)

    def test_mineru_html_answer_table(self):
        result = self.scan('''1. 计算1+1。
# 参考答案
<table><tr><td>题号</td><td>1</td></tr>
<tr><td>答案</td><td>2</td></tr></table>
''')
        self.assertEqual(result['status'], 'needs_review', result['error'])
        self.assertEqual(result['draft']['questions'][0]['answer'], '2')

    def test_structured_and_chinese_blocks_share_complete_question_checks(self):
        result = self.scan('''## Q1
prompt: 第一题
answer: 2
explanation: 解析。
2）第二题
【答案】4
''')
        self.assertEqual(result['status'], 'needs_review', result['error'])
        self.assertEqual([q['answer'] for q in result['draft']['questions']], ['2', '4'])
        self.assertEqual(result['draft']['questions'][0]['explanation'], '解析。')
        result = self.scan('''## Q1
prompt: 选择正数。
type: choice
choices: A:-1;B:2;C:0
answer: B
''')
        self.assertEqual(result['status'], 'needs_review', result['error'])
        self.assertEqual(len(result['draft']['questions'][0]['choices']), 3)

    def test_bad_scan_never_publishes_partial_draft_and_preserves_diagnostics(self):
        cases = [
            ('1. 题干\n【答案】2\n# 二、填空题\n1. 另一题\n【答案】3', 'PAPER_STRUCTURE_FAILED'),
            ('1. 题干\n【答案】2\n# 参考答案\n1. 3', 'PAPER_STRUCTURE_FAILED'),
            ('1. 题干\n【答案】2\n# 参考答案\n2. 3', 'PAPER_STRUCTURE_FAILED'),
            ('1. 选择题\nA. 1 B. 2\n【答案】D', 'PAPER_STRUCTURE_FAILED'),
            ('1. 选择题\nA. 1 B. 2\n【答案】B', 'PAPER_STRUCTURE_FAILED'),
            ('1. 题\n【答案】1\n3. 题\n【答案】3', 'PAPER_STRUCTURE_FAILED'),
            ('1. 计算1+1\n【答案】2\n二、填空题\n（2）计算2+2\n【答案】4', 'PAPER_STRUCTURE_FAILED'),
            ('1. 选择正确答案\n【题型】choice\n【答案】A', 'PAPER_STRUCTURE_FAILED'),
            ('## Q1\nprompt: 第一题\nanswer: 2\n## Q1\nprompt: 第二题\nanswer: 3', 'PAPER_STRUCTURE_FAILED'),
            ('## Q1\nprompt: 第一题\ntype: choice\nanswer: A', 'PAPER_STRUCTURE_FAILED'),
            ('Q1\nprompt: 选择正确答案\nchoices: []\nanswer: A', 'PAPER_STRUCTURE_FAILED'),
            ('Q1\nprompt: 选择正确答案\nchoices:\nanswer: A', 'PAPER_STRUCTURE_FAILED'),
            ('Q1\nprompt: 选择正确答案\nchoices: A: ;B:2;C:3\nanswer: A', 'PAPER_STRUCTURE_FAILED'),
            ('Q1\nprompt: 选择正确答案\nchoices: [{"key":"A","text":" "},{"key":"B","text":"2"},{"key":"C","text":"3"}]\nanswer: A', 'PAPER_STRUCTURE_FAILED'),
            ('Q1\nprompt: 选择正数 A. -1 B. 2 C. -3\nchoices: A:9;B:-2;C:-3\nanswer: B', 'PAPER_STRUCTURE_FAILED'),
            ('只有标题，没有可识别的题号', 'PAPER_STRUCTURE_FAILED'),
        ]
        for text, code in cases:
            with self.subTest(text=text):
                result = self.scan(text)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['error']['code'], code)
                self.assertEqual(result['draft']['questions'], [])
                self.assertTrue(result['reports']['parse']['unparsed'])
                self.assertIn('structure', result['reports']['parse'])

    def test_section_rubric_after_q1_is_audited_and_not_a_question(self):
        note = '解答应写出文字说明、演算步骤或证明过程.'
        result = self.scan('1. 第一题\n【答案】2\n# 第二部分 非选择题\n'
                           '# 二、解答题\n' + note + '\n本大题共20分，每小题10分。\n'
                           '2. ' + note + '\n请计算2+2。\n【答案】4')
        self.assertEqual(result['status'], 'needs_review', result['error'])
        self.assertEqual(len(result['draft']['questions']), 2)
        self.assertIn(note, result['draft']['questions'][1]['prompt'])
        notes = result['reports']['parse']['structure']['ignoredNotes']
        self.assertEqual([n['text'] for n in notes], [note, '本大题共20分，每小题10分。'])
        self.assertEqual(notes[0]['line'], 5)
        self.assertEqual(notes[0]['section'], '二、解答题')
        unknown = self.scan('1. 第一题\n【答案】2\n二、解答题\n未知未归属内容\n2. 第二题\n【答案】4')
        self.assertEqual(unknown['error']['code'], 'PAPER_STRUCTURE_FAILED')

    def test_scoring_note_prefix_does_not_discard_unassigned_math(self):
        result = self.scan('1. 第一题\n【答案】2\n二、解答题\n'
                           '本大题共2个未知数，求它们的和。\n2. 第二题\n【答案】4')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'PAPER_STRUCTURE_FAILED')

    def test_mixed_option_labels_are_not_silently_truncated(self):
        result = self.scan('一、选择题\n1. 选择\nA. 1 B. 2 C. 3 (D)4\n【答案】A')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'PAPER_STRUCTURE_FAILED')

    def test_incomplete_content_is_reviewable_with_current_risks_and_topics(self):
        for text, answers, codes in [
            ('1. 计算1+1\n2. 计算2+2\n【答案】4', ['', '4'], {'PAPER_ANSWER_MISSING'}),
            ('1. 看图\n![](images/a.jpg)\n【答案】2', ['2'], {'PAPER_ASSET_MISSING'}),
            ('1. 看图\n![](images/a.jpg)', [''], {'PAPER_ANSWER_MISSING', 'PAPER_ASSET_MISSING'}),
        ]:
            with self.subTest(text=text):
                result = self.scan(text)
                self.assertEqual(result['status'], 'needs_review', result['error'])
                self.assertTrue(result['flags']['requiresHumanReview'])
                qs, report = result['draft']['questions'], result['reports']['parse']
                self.assertEqual([q['answer'] for q in qs], answers)
                self.assertTrue(all(q['topicId'].startswith('cnm_') for q in qs))
                self.assertEqual(set(report['riskCodes']), codes)
                self.assertTrue(report['requiresHumanReview'])
                self.assertEqual(report['questionRisks'][0]['id'], qs[0]['id'])
                self.assertEqual(report['questionRisks'][0]['answerMissing'], not answers[0])
                self.assertEqual(report['unparsed'], [])
                self.service.approve(result['jobId'])
                with self.assertRaises(ImportErrorResponse):
                    apply_job(self.service, result['jobId'])
                self.assertEqual(self.formal_hashes(), self.baseline)

    def test_revision_recomputes_risks_and_preserves_initial_structure(self):
        result = self.scan('1. 看图\n![](images/a.jpg)')
        self.assertEqual(result['status'], 'needs_review', result['error'])
        draft = result['draft']
        draft['questions'][0].update(prompt='计算1+1。', answer='2')
        self.service.revise(result['jobId'], draft, result['draftSha256'])
        revised = self.service.snapshot(result['jobId'])
        report = revised['reports']['parse']
        self.assertEqual(report['riskCodes'], [])
        self.assertEqual(report['questionRisks'], [])
        self.assertEqual(report['structure']['questionNumbers'], ['1'])
        self.assertTrue(report['initialRisks'])
        self.assertTrue(revised['reports']['validate']['ok'])
        self.service.approve(result['jobId'])
        apply_job(self.service, result['jobId'])
        self.assertEqual(self.service.get(result['jobId'])['status'], 'applied')

    def test_parenthesized_latex_options_are_normalized_with_evidence(self):
        result = self.scan(r'''# 一、选择题
1. 选择正数
$\left( \mathbf { A } \right) -1$ $\mathrm { ( B ) 2 }$ (D）-3 (C)0
# 第二部分 非选择题
2. 矩阵(A)与(B)的关系。
''')
        self.assertEqual(result['status'], 'needs_review', result['error'])
        qs = result['draft']['questions']
        self.assertEqual([c['key'] for c in qs[0]['choices']], list('ABCD'))
        self.assertNotIn('第二部分', qs[0]['choices'][-1]['text'])
        self.assertIn('(A)', qs[1]['prompt'])
        self.assertNotIn('choices', qs[1])
        evidence = result['reports']['parse']['structure']['choiceNormalizations'][0]
        self.assertEqual(evidence['originalOrder'], list('ABDC'))
        self.assertTrue(evidence['requiresHumanReview'])
        self.assertIn('\\mathbf', evidence['source'])
        for options in ['(A)1 (A)2 (C)3', '(A)1 (B)2', '(A) (B)2 (C)3', '(A)1 (B)2 (D)3']:
            bad = self.scan('一、选择题\n1. 选择\n' + options)
            self.assertEqual(bad['error']['code'], 'PAPER_STRUCTURE_FAILED')

    def test_parentheses_inside_complete_dotted_options_do_not_replace_labels(self):
        result = self.scan('一、选择题\n1. 选择正数\nA. 点(A)的数为-1\nB. 2\nC. 0\nD. -2\n【答案】B')
        self.assertEqual(result['status'], 'needs_review', result['error'])
        choices = result['draft']['questions'][0]['choices']
        self.assertEqual([c['key'] for c in choices], list('ABCD'))
        self.assertEqual(choices[0]['text'], '点(A)的数为-1')
        self.assertEqual(result['reports']['parse']['structure']['choiceNormalizations'], [])

    def test_unsupported_reference_image_cannot_bypass_asset_gate(self):
        for image in ['![图][missing]\n[missing]: images/a.png', '![图][]', '![图]']:
            result = self.scan('1. 看图\n' + image + '\n【答案】2')
            self.assertEqual(result['status'], 'needs_review', result['error'])
            self.assertIn('PAPER_ASSET_MISSING', result['reports']['parse']['riskCodes'])
            self.service.approve(result['jobId'])
            self.assert_code('PAPER_ASSET_MISSING', lambda: apply_job(self.service, result['jobId']))

    def test_asset_apply_gate_checks_current_fields_without_trusting_reports(self):
        for field in ('prompt', 'answer', 'explanation', 'choices'):
            with self.subTest(field=field):
                result = self.scan('1. 计算1+1\n【答案】2')
                draft = result['draft']
                if field == 'choices':
                    draft['questions'][0].update(type='choice', answer='A', choices=[
                        {'key': key, 'text': '![](x.png)' if key == 'A' else key} for key in 'ABC'])
                else:
                    draft['questions'][0][field] = '![](x.png)'
                self.service.revise(result['jobId'], draft, result['draftSha256'])
                self.service.approve(result['jobId'])
                self.assert_code('PAPER_ASSET_MISSING', lambda: apply_job(self.service, result['jobId']))
                self.assertEqual(self.formal_hashes(), self.baseline)


class LargeUploadTests(base.WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.service, 'test-import-token', port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def test_private_asset_download_is_authenticated_bounded_and_not_text(self):
        job = self.service.create_job('paper', 'exam.pdf', b'%PDF-test', {'subjectPrefix': 'cnm'})['jobId']
        assets = self.service.job_dir(job) / 'source/assets'
        assets.mkdir()
        payload = b'\x89PNG\r\n\x1a\nfixture'
        (assets / 'a.png').write_bytes(payload)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = self.url + '/import/v1/jobs/' + job + '/files/source/assets/a.png'
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(url, timeout=5)
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer test-import-token'})
        with opener.open(request, timeout=5) as response:
            self.assertEqual(response.headers['Content-Type'], 'application/octet-stream')
            self.assertEqual(response.read(), payload)
        (assets / 'a.png').unlink()
        (assets / 'a.png').symlink_to(self.service.data / 'questions.json')
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(request, timeout=5)
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        (assets / 'a.png').unlink()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(request, timeout=5)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_source_type_boundaries(self):
        for filename, limit in [('exam.txt', 16 * 1024 * 1024), ('exam.pdf', 64 * 1024 * 1024)]:
            with self.subTest(filename=filename):
                content = b'x' * limit
                meta = self.service.create_job('paper', filename, content, {'subjectPrefix': 'cnm'})
                self.assertEqual(len(self.service.source(meta['jobId'])), limit)
                with self.assertRaises(ImportErrorResponse) as caught:
                    self.service.create_job('paper', filename, content + b'x', {'subjectPrefix': 'cnm'})
                self.assertEqual(caught.exception.code, 'SOURCE_SIZE')

    def test_http_rejects_new_request_limit_before_reading_body(self):
        request = urllib.request.Request(self.url + '/import/v1/jobs/paper', data=b'', headers={
            'Authorization': 'Bearer test-import-token', 'Content-Length': str(65 * 1024 * 1024 + 1)})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(request, timeout=5)
        self.assertEqual(caught.exception.code, 413)
        self.assertEqual(json.load(caught.exception)['code'], 'REQUEST_TOO_LARGE')
        caught.exception.close()

    def test_cli_and_source_reread_refuse_new_limit_overflow(self):
        path = self.workspace / 'oversized.txt'
        with path.open('wb') as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
        env = dict(os.environ, IMPORT_TOKEN='test-import-token', IMPORT_API_URL=self.url)
        result = subprocess.run([sys.executable, str(ROOT / 'import-cli'), 'paper', '--prefix', 'cnm',
                                 '--file', str(path)], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['code'], 'SOURCE_SIZE')
        self.assertEqual(self.service.list_jobs(), [])
        meta = self.service.create_job('paper', 'exam.txt', b'original', {'subjectPrefix': 'cnm'})
        original = self.service.job_dir(meta['jobId']) / meta['sourceFiles'][0]
        with original.open('wb') as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
        with self.assertRaises(ImportErrorResponse) as caught:
            self.service.source(meta['jobId'])
        self.assertEqual(caught.exception.code, 'SOURCE_SIZE')

    def test_cli_http_text_and_pdf_above_old_limits(self):
        env = dict(os.environ, IMPORT_TOKEN='test-import-token', IMPORT_API_URL=self.url)
        for filename, content in [
            ('large.md', ('## Q1\nprompt: ' + '题' * 750000 + '\nanswer: 2\n').encode()),
            ('large.pdf', b'%PDF-fixture\n' + b'0' * (2 * 1024 * 1024)),
        ]:
            with self.subTest(filename=filename):
                path = self.workspace / filename
                path.write_bytes(content)
                command = [sys.executable, str(ROOT / 'import-cli'), 'paper', '--prefix', 'cnm', '--file', str(path)]
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                meta = json.loads(result.stdout)
                self.assertEqual(self.service.source(meta['jobId']), content)


if __name__ == '__main__':
    import unittest
    unittest.main()
