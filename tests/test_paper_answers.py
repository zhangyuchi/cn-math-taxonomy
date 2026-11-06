"""两段解析、绑定补答与既有审核/正式库兼容性。"""
import copy
import csv
import io
import json
import subprocess
import sys
from unittest import mock

import test_import_module as base
from import_module.paper import parse_paper
from import_module.store import ImportErrorResponse, digest_bytes, json_bytes
from import_module.validation import candidate
from import_module.worker import run_once
from import_module.apply import apply_job


class PaperAnswerTests(base.WorkspaceCase):
    def parse(self, content, suffix='.md', ocr=False):
        meta = dict(subjectPrefix='cnm', sourceFiles=['source/original' + suffix])
        return parse_paper(self.service, meta, content.encode(), suffix, ocr=ocr)[1]['questions'][0]

    def test_text_and_csv_parts_are_stored_in_existing_explanation(self):
        text = 'Q1\nprompt: 1+1\nanswer: 2\nsolutionHint: 使用加法。\nsolutionDetail: 1+1=2。'
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=['prompt', 'answer', 'solutionHint', 'solutionDetail'])
        writer.writeheader()
        writer.writerow(dict(prompt='1+1', answer='2', solutionHint='使用加法。', solutionDetail='1+1=2。'))
        for content, suffix in ((text, '.md'), (text, '.txt'), (output.getvalue(), '.csv')):
            with self.subTest(suffix=suffix):
                q = self.parse(content, suffix)
                self.assertEqual(q['explanation'], '### 思路提示\n使用加法。\n\n### 详细解答\n1+1=2。')
                self.assertNotIn('solutionHint', q)
                self.assertNotIn('solutionDetail', q)
                self.assertEqual(q['answer'], '2')

    def test_ocr_chinese_parts_and_legacy_explanation(self):
        q = self.parse('1. 计算1+1。\n【答案】2\n【思路提示】使用加法。\n【详细解答】1+1=2。', ocr=True)
        self.assertEqual(q['explanation'], '### 思路提示\n使用加法。\n\n### 详细解答\n1+1=2。')
        old = self.parse('Q1\nprompt: 1+1\nanswer: 2\nexplanation: 原解析。')
        self.assertIn('原文未提供思路提示，待人工补充', old['explanation'])
        self.assertTrue(old['explanation'].endswith('### 详细解答\n原解析。'))
        canonical = self.parse('Q1\nprompt: 1+1\nanswer: 2\nexplanation: ' + q['explanation'])
        self.assertEqual(canonical['explanation'], q['explanation'])

    def test_ocr_canonical_and_standalone_headings_keep_both_parts(self):
        expected = '### 思路提示\n使用加法。\n\n### 详细解答\n1+1=2。'
        for suffix in ('explanation: ' + expected, expected, '思路提示\n使用加法。\n详细解答\n1+1=2。'):
            with self.subTest(suffix=suffix):
                q = self.parse('Q1\nprompt: 1+1\nanswer: 2\n' + suffix, ocr=True)
                self.assertEqual(q['explanation'], expected)

    def test_conflicting_parts_do_not_overwrite_original(self):
        for text, ocr in (
            ('Q1\nprompt: 1+1\nanswer: 2\nexplanation: 旧详解\nsolutionDetail: 不同详解', False),
            ('1. 1+1\n【答案】2\n【解析】旧详解\n【详细解答】不同详解', True)):
            with self.subTest(ocr=ocr), self.assertRaises(ImportErrorResponse):
                self.parse(text, ocr=ocr)

    def empty_job(self):
        meta = self.service.create_job('paper', 'scan.pdf', b'%PDF-fixture', dict(subjectPrefix='cnm'))
        from import_module import worker
        with mock.patch.object(worker, 'normalize_pdf', return_value=b'1. 1+1\n'):
            run_once(self.service)
        return self.service.snapshot(meta['jobId'])

    def solutions(self, snapshot):
        return dict(jobId=snapshot['jobId'], sourceSha256=snapshot['sourceSha256'],
                    expectedDraftSha256=snapshot['draftSha256'], knowledgeScope='grade7',
                    solutions=[dict(id=q['id'], answer='', hint='先做加法。', detail='题干可得2；选项需核对。',
                                    answerUncertain=True, originalRequired=True)
                               for q in snapshot['draft']['questions']])

    def test_backfill_revision_risks_and_empty_answer_gate(self):
        from import_module.answers import build_backfill
        snapshot = self.empty_job()
        self.assertEqual(snapshot['flags']['riskCodes'], ['PAPER_ANSWER_MISSING'])
        manifest = self.solutions(snapshot)
        result = build_backfill(snapshot, manifest)
        self.assertEqual(snapshot['draftSha256'], result['expectedDraftSha256'])
        self.assertEqual(snapshot['draft']['questions'][0]['answer'], '')
        q = result['draft']['questions'][0]
        self.assertIn('### 思路提示', q['explanation'])
        self.assertIn('### 详细解答', q['explanation'])
        self.assertEqual(q['answer'], '')
        meta = self.service.revise(snapshot['jobId'], result['draft'], snapshot['draftSha256'])
        self.assertEqual(meta['status'], 'needs_review')
        updated = self.service.snapshot(snapshot['jobId'])
        codes = {'PAPER_ANSWER_GENERATED', 'PAPER_ANSWER_UNCERTAIN', 'PAPER_ORIGINAL_REVIEW_REQUIRED', 'PAPER_ANSWER_MISSING'}
        self.assertEqual(set(updated['reports']['parse']['riskCodes']), codes)
        self.assertEqual(set(updated['flags']['riskCodes']), codes)
        risk = updated['reports']['parse']['questionRisks'][0]
        self.assertTrue(risk['generated'] and risk['answerUncertain'] and risk['originalRequired'])
        self.assertFalse(updated['reports']['validate']['ok'])
        self.service.approve(snapshot['jobId'], expected_digest=updated['draftSha256'])
        with self.assertRaises(ImportErrorResponse):
            apply_job(self.service, snapshot['jobId'])
        self.assertEqual(self.formal_hashes(), self.baseline)

    def test_manifest_binding_and_overwrite_rejections(self):
        from import_module.answers import build_backfill
        snapshot = self.empty_job()
        manifest = self.solutions(snapshot)
        for key in ('jobId', 'sourceSha256', 'expectedDraftSha256', 'knowledgeScope'):
            bad = copy.deepcopy(manifest)
            bad[key] = 'wrong'
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_backfill(snapshot, bad)
        for rows in ([], manifest['solutions'] * 2, [dict(manifest['solutions'][0], id='unknown')],
                     [dict(manifest['solutions'][0], answer='2')],
                     [dict(manifest['solutions'][0], answerUncertain='false')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                build_backfill(snapshot, dict(manifest, solutions=rows))
        stale = copy.deepcopy(snapshot)
        stale['draft']['questions'][0]['prompt'] = '3+3'
        with self.assertRaises(ValueError):
            build_backfill(stale, manifest)
        for field, value in (('answer', '2'), ('explanation', '教师已写的解析')):
            edited = copy.deepcopy(snapshot)
            edited['draft']['questions'][0][field] = value
            edited['draftSha256'] = digest_bytes(json_bytes(edited['draft']))
            payload = dict(manifest, expectedDraftSha256=edited['draftSha256'])
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_backfill(edited, payload)

    def test_resolved_generated_answer_exports_without_extra_fields(self):
        from import_module.answers import build_backfill
        snapshot = self.empty_job()
        manifest = self.solutions(snapshot)
        manifest['solutions'][0].update(answer='2', answerUncertain=False, originalRequired=False)
        result = build_backfill(snapshot, manifest)
        self.service.revise(snapshot['jobId'], result['draft'], snapshot['draftSha256'])
        updated = self.service.snapshot(snapshot['jobId'])
        self.assertEqual(updated['flags']['riskCodes'], ['PAPER_ANSWER_GENERATED'])
        self.assertTrue(updated['reports']['validate']['ok'], updated['reports']['validate'])
        with candidate(self.service, snapshot['jobId']) as (_, prepared):
            q = prepared['draft']['questions'][0]
            self.assertEqual(set(q), {'id', 'topicId', 'type', 'difficulty', 'prompt', 'answer', 'explanation'})
            self.assertEqual(q['explanation'], result['draft']['questions'][0]['explanation'])
        self.assertEqual(self.service.get(snapshot['jobId'])['status'], 'needs_review')
        corrected = self.service.draft(snapshot['jobId'])
        corrected['questions'][0]['explanation'] = '### 思路提示\n使用加法。\n\n### 详细解答\n1+1=2。'
        self.service.revise(snapshot['jobId'], corrected, self.service.draft_digest(snapshot['jobId']))
        final = self.service.snapshot(snapshot['jobId'])
        self.assertEqual(final['reports']['parse']['riskCodes'], [])
        self.assertEqual(final['flags']['riskCodes'], [])
        history = list((self.service.job_dir(snapshot['jobId']) / 'review').glob('revision-*.json'))
        self.assertTrue(any('【生成补答，待人工复核】' in p.read_text() for p in history))

    def test_offline_cli_writes_revise_payload_without_touching_job(self):
        snapshot = self.empty_job()
        src, solutions, output = (self.workspace / name for name in ('snapshot.json', 'solutions.json', 'revised.json'))
        src.write_text(json.dumps(snapshot), encoding='utf-8')
        solutions.write_text(json.dumps(self.solutions(snapshot)), encoding='utf-8')
        result = subprocess.run([sys.executable, '-m', 'import_module.answers', '--snapshot', str(src),
                                 '--solutions', str(solutions), '--output', str(output)],
                                cwd=base.ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('### 思路提示', json.loads(output.read_text())['draft']['questions'][0]['explanation'])
        self.assertEqual(self.service.draft_digest(snapshot['jobId']), snapshot['draftSha256'])
