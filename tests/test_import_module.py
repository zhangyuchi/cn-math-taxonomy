"""导入骨架的真实文件系统、HTTP 与 CLI 验收；正式库仅使用临时副本。"""
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from import_module.store import Service, ImportErrorResponse, atomic_json, read_json, file_lock
    from import_module.worker import run_once
    from import_module.apply import apply_job
    from import_module.api import make_server
except ModuleNotFoundError:
    Service = None

TOC = b'grade: 7\nterm: \xe4\xb8\x8a\n# \xe7\xac\xac\xe4\xb8\x80\xe7\xab\xa0\n## \xe5\xaf\xbc\xe5\x85\xa5\xe7\xa4\xba\xe4\xbe\x8b A\n## \xe5\xaf\xbc\xe5\x85\xa5\xe7\xa4\xba\xe4\xbe\x8b B\n'
MAPPING = 'topicId,grade,term,chapter\ncnm_001,7,上,第一章\n'.encode()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Service, '导入模块尚未实现')
        self.temp = tempfile.TemporaryDirectory(prefix='cn-math-import-test-')
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        data = self.workspace / 'data'
        data.mkdir()
        for name in ('topics.json', 'dependencies.json', 'questions.json',
                     'cn-curriculum-standards.json', 'id-aliases.json'):
            shutil.copy2(ROOT / 'data' / name, data / name)
        shutil.copytree(ROOT / 'data/mappings', data / 'mappings')
        self.service = Service(self.workspace)
        self.baseline = self.formal_hashes()

    def formal_hashes(self):
        return {str(p.relative_to(self.workspace)): digest(p)
                for p in (self.workspace / 'data').glob('*.json')}

    def mapping(self, auto=True, content=MAPPING, **extra):
        params = dict(mode='mappings_only', subjectPrefix='cnm', edition='importdemo_math',
                      label='导入测试版本', source='人工提供目录；测试夹具', autoApplyMappings=auto)
        params.update(extra)
        return self.service.create_job('textbook', 'toc.csv', content, params)['jobId']

    def expand(self):
        return self.service.create_job('textbook', 'toc.md', TOC,
                                       dict(mode='expand', subjectPrefix='cnm'))['jobId']

    def assert_code(self, code, callback):
        with self.assertRaises(ImportErrorResponse) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)


class ImportAcceptance(WorkspaceCase):
    def test_toc_job_layout_and_real_validation_without_formal_writes(self):
        job = self.expand()
        self.assertRegex(job, r'^\d{8}-\d{6}-[a-f0-9]{8}$')
        self.assertEqual(self.service.get(job)['status'], 'queued')
        self.assertTrue(run_once(self.service))
        meta = self.service.get(job)
        self.assertEqual(meta['status'], 'needs_review')
        self.assertTrue(meta['flags']['requiresHumanReview'])
        self.assertTrue(meta['createdAt'].endswith('+08:00'))
        directory = self.service.job_dir(job)
        for path in ('source/original.md', 'source/normalized.md', 'draft/topics.json',
                     'draft/dependencies.json', 'draft/questions.json', 'draft/mappings.json',
                     'draft/standards_suggestions.json', 'reports/parse.json',
                     'reports/validate.json', 'reports/verify.json', 'reports/coverage.json'):
            self.assertTrue((directory / path).is_file(), path)
        self.assertEqual((directory / 'source/original.md').read_bytes(), TOC)
        topics = read_json(directory / 'draft/topics.json')
        self.assertEqual(len(topics), 2)
        self.assertTrue(all(t['diagnostic_question']['answer'] for t in topics))
        self.assertEqual(read_json(directory / 'draft/dependencies.json')[0]['strength'], 'soft')
        self.assertFalse(read_json(directory / 'reports/validate.json')['ok'])
        self.assertIn('至少需要', read_json(directory / 'reports/validate.json')['output'])
        self.assertEqual(self.formal_hashes(), self.baseline)

    def test_valid_mappings_auto_apply_and_idempotence(self):
        job = self.mapping()
        run_once(self.service)
        self.assertEqual(self.service.get(job)['status'], 'applied')
        path = self.workspace / 'data/mappings/importdemo_math.json'
        self.assertEqual(read_json(path)['entries'][0]['topicId'], 'cnm_001')
        decision = read_json(self.service.job_dir(job) / 'review/decision.json')
        self.assertEqual(decision['actor'], 'system:auto-mappings')
        before = path.read_bytes()
        apply_job(self.service, job)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.formal_hashes(), self.baseline)

    def test_manual_mappings_and_merge_preserves_other_entries(self):
        job = self.mapping(False)
        run_once(self.service)
        self.assertEqual(self.service.get(job)['status'], 'needs_review')
        self.assert_code('REVIEW_REQUIRED', lambda: apply_job(self.service, job))
        self.service.approve(job, comment='已对照原文')
        apply_job(self.service, job)
        job2 = self.mapping(content=MAPPING.replace(b'cnm_001', b'cnm_002'))
        run_once(self.service)
        self.assertEqual(self.service.get(job2)['status'], 'applied')
        entries = read_json(self.workspace / 'data/mappings/importdemo_math.json')['entries']
        self.assertEqual({e['topicId'] for e in entries}, {'cnm_001', 'cnm_002'})

    def test_invalid_mapping_never_applies_even_with_human_approval(self):
        job = self.mapping(content=MAPPING.replace(b'cnm_001', b'cnm_999999'))
        run_once(self.service)
        self.assertEqual(self.service.get(job)['status'], 'needs_review')
        self.service.approve(job)
        self.assert_code('VALIDATION_FAILED', lambda: apply_job(self.service, job))
        self.assertEqual(self.service.get(job)['status'], 'approved')
        self.assertFalse((self.workspace / 'data/mappings/importdemo_math.json').exists())
        self.assertEqual(self.formal_hashes(), self.baseline)

    def test_new_topic_and_question_require_human_and_supported_scope(self):
        job = self.expand()
        run_once(self.service)
        self.assert_code('REVIEW_REQUIRED', lambda: apply_job(self.service, job))
        self.service.approve(job)
        self.assert_code('APPLY_SCOPE_UNSUPPORTED', lambda: apply_job(self.service, job))
        mixed = self.mapping(False)
        run_once(self.service)
        atomic_json(self.service.job_dir(mixed) / 'draft/questions.json', [{'id': 'cnm_001_q999'}])
        self.assert_code('REVIEW_REQUIRED', lambda: self.service.approve(mixed, automatic=True))
        self.assert_code('REVIEW_REQUIRED', lambda: apply_job(self.service, mixed))
        self.service.approve(mixed)
        self.assert_code('APPLY_SCOPE_UNSUPPORTED', lambda: apply_job(self.service, mixed))
        self.assertEqual(self.formal_hashes(), self.baseline)

    def test_approval_is_bound_to_draft(self):
        job = self.mapping(False)
        run_once(self.service)
        self.service.approve(job)
        path = self.service.job_dir(job) / 'draft/mappings.json'
        mapping = read_json(path)
        mapping['entries'][0]['chapter'] = '未审批的改动'
        atomic_json(path, mapping)
        self.assert_code('DRAFT_CHANGED', lambda: apply_job(self.service, job))
        self.assertEqual(self.service.get(job)['status'], 'needs_review')
        self.assertFalse((self.workspace / 'data/mappings/importdemo_math.json').exists())
        self.service.approve(job, comment='重新核对修订')
        apply_job(self.service, job)
        self.assertEqual(self.service.get(job)['status'], 'applied')

    def test_metadata_conflict_and_unsupported_mapping_subject(self):
        meta = self.service.create_job('textbook', 'toc.md', TOC,
                                       dict(mode='expand', subjectPrefix='cnm', grade=8))
        run_once(self.service)
        self.assertEqual(self.service.get(meta['jobId'])['error']['code'], 'METADATA_CONFLICT')
        self.assert_code('MAPPINGS_SUBJECT_UNSUPPORTED',
                         lambda: self.mapping(subjectPrefix='cng', edition='demo_geography'))

    def test_mappings_metadata_conflict_cannot_auto_apply(self):
        job = self.mapping(grade=8, term='下')
        run_once(self.service)
        self.assertEqual(self.service.get(job)['status'], 'failed')
        self.assertEqual(self.service.get(job)['error']['code'], 'METADATA_CONFLICT')
        self.assertFalse((self.workspace / 'data/mappings/importdemo_math.json').exists())

    def test_mapping_diff_shows_before_and_full_merged_after(self):
        first = self.mapping()
        run_once(self.service)
        second = self.mapping(False, content=MAPPING.replace('第一章'.encode(), '第二章'.encode()))
        run_once(self.service)
        diff = self.service.diff(second)['mappings']
        self.assertEqual(diff['before']['entries'][0]['chapter'], '第一章')
        self.assertEqual(diff['after']['entries'][0]['chapter'], '第二章')

    def test_mapping_update_requires_its_own_source(self):
        self.mapping()
        run_once(self.service)
        path = self.workspace / 'data/mappings/importdemo_math.json'
        before = path.read_bytes()
        missing_source = self.mapping(content=MAPPING.replace(b'cnm_001', b'cnm_002'), source=None)
        run_once(self.service)
        self.assertEqual(self.service.get(missing_source)['status'], 'needs_review')
        self.assertFalse(read_json(self.service.job_dir(missing_source) / 'reports/validate.json')['ok'])
        self.assertEqual(path.read_bytes(), before)

    def test_reserved_edition_and_duplicate_entries_never_apply(self):
        for params in ({'edition': 'renjiao_math'}, {'content': MAPPING + MAPPING.split(b'\n')[1] + b'\n'}):
            job = self.mapping(**params)
            run_once(self.service)
            self.assertEqual(self.service.get(job)['status'], 'needs_review')
            self.assertFalse(read_json(self.service.job_dir(job) / 'reports/validate.json')['ok'])
        self.assertEqual(self.formal_hashes(), self.baseline)

    def test_recovery_refuses_unexpected_target_content(self):
        job = self.mapping(False)
        run_once(self.service)
        self.service.approve(job)
        with mock.patch('import_module.apply.atomic_bytes', side_effect=OSError('模拟 replace 前中断')):
            with self.assertRaises(OSError):
                apply_job(self.service, job)
        path = self.workspace / 'data/mappings/importdemo_math.json'
        path.write_text('{"unexpected":true}')
        self.assert_code('RECOVERY_CONFLICT', lambda: apply_job(self.service, job))
        self.assertEqual(path.read_text(), '{"unexpected":true}')

    def test_uncommitted_intent_does_not_block_fresh_human_approval(self):
        job = self.mapping(False)
        run_once(self.service)
        self.service.approve(job)
        with mock.patch('import_module.apply.atomic_bytes', side_effect=OSError('replace 前中断')):
            with self.assertRaises(OSError):
                apply_job(self.service, job)
        draft = self.service.job_dir(job) / 'draft/mappings.json'
        mapping = read_json(draft)
        mapping['entries'][0]['chapter'] = '修订后目录'
        atomic_json(draft, mapping)
        self.assert_code('DRAFT_CHANGED', lambda: apply_job(self.service, job))
        self.service.approve(job)
        apply_job(self.service, job)
        self.assertEqual(self.service.get(job)['status'], 'applied')

    def test_worker_singleton_lock(self):
        self.expand()
        with file_lock(self.service.imports / '_worker.lock'):
            self.assert_code('BUSY', lambda: run_once(self.service))

    def test_apply_revalidates_current_formal_data(self):
        job = self.mapping(False)
        run_once(self.service)
        self.service.approve(job)
        topics = read_json(self.workspace / 'data/topics.json')
        topics.append(topics[0])
        atomic_json(self.workspace / 'data/topics.json', topics)
        self.assert_code('VALIDATION_FAILED', lambda: apply_job(self.service, job))
        self.assertFalse((self.workspace / 'data/mappings/importdemo_math.json').exists())

    def test_global_apply_lock_returns_conflict(self):
        job = self.mapping(False)
        run_once(self.service)
        self.service.approve(job)
        with file_lock(self.service.imports / '_apply.lock'):
            self.assert_code('BUSY', lambda: apply_job(self.service, job))

    def test_recover_after_replace_before_metadata_commit(self):
        job = self.mapping(False)
        run_once(self.service)
        self.service.approve(job)
        original_save = self.service.save

        def crash(meta):
            if meta['status'] == 'applied':
                raise OSError('模拟 replace 后 meta 保存失败')
            return original_save(meta)

        with mock.patch.object(self.service, 'save', side_effect=crash):
            with self.assertRaises(OSError):
                apply_job(self.service, job)
        target = self.workspace / 'data/mappings/importdemo_math.json'
        before = target.read_bytes()
        apply_job(self.service, job)
        self.assertEqual(self.service.get(job)['status'], 'applied')
        self.assertEqual(target.read_bytes(), before)

    def test_reject_requeue_and_source_integrity(self):
        job = self.expand()
        run_once(self.service)
        self.service.reject(job, reason='需补充教材依据')
        self.assertEqual(self.service.get(job)['status'], 'rejected')
        self.service.requeue(job)
        self.assertEqual(self.service.get(job)['status'], 'queued')
        run_once(self.service)
        self.assertEqual(self.service.get(job)['status'], 'needs_review')
        self.service.reject(job, reason='需要新源')
        (self.service.job_dir(job) / 'source/original.md').write_text('被修改的源')
        self.assert_code('SOURCE_CHANGED', lambda: self.service.requeue(job))

    def test_worker_recovers_interrupted_running_job(self):
        job = self.expand()
        meta = self.service.get(job)
        meta['status'] = 'running'
        self.service.save(meta)
        (self.service.imports / '_queue' / job).unlink()
        run_once(self.service)
        self.assertEqual(self.service.get(job)['status'], 'needs_review')

    def test_pdf_and_paper_fail_closed_and_preserve_source(self):
        for filename, content, kind, code in [('toc.pdf', b'%PDF-demo', 'textbook', 'OCR_REQUIRED'),
                                               ('exam.md', b'## Q1\nprompt: 1+1', 'paper', 'PIPELINE_NOT_IMPLEMENTED')]:
            meta = self.service.create_job(kind, filename, content,
                                           dict(mode='expand', subjectPrefix='cnm'))
            run_once(self.service)
            result = self.service.get(meta['jobId'])
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error']['code'], code)
            self.assertEqual((self.service.job_dir(meta['jobId']) / ('source/original' + Path(filename).suffix)).read_bytes(), content)

    def test_all_existing_subjects_create_toc_drafts(self):
        grades = dict(cnm=7, cne=7, cnc=7, cnp=8, cns=3, cnd=3, cnh=7, cng=7, cnb=7, cnk=9)
        for prefix, grade in grades.items():
            content = TOC.replace(b'grade: 7', f'grade: {grade}'.encode())
            job = self.service.create_job('textbook', 'toc.md', content,
                                           dict(mode='expand', subjectPrefix=prefix))['jobId']
            run_once(self.service)
            self.assertEqual(self.service.get(job)['status'], 'needs_review', prefix)
            topics = read_json(self.service.job_dir(job) / 'draft/topics.json')
            self.assertTrue(topics[0]['id'].startswith(prefix + '_'))

    def test_files_reject_traversal_and_symlink_escape(self):
        job = self.expand()
        self.assert_code('INVALID_PATH', lambda: self.service.read_file(job, '../topics.json'))
        outside = self.workspace / 'secret.txt'
        outside.write_text('secret')
        (self.service.job_dir(job) / 'source/escape.txt').symlink_to(outside)
        self.assert_code('INVALID_PATH', lambda: self.service.read_file(job, 'source/escape.txt'))


class HTTPAcceptance(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.service, 'test-import-token', port=0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        # 本地 API 测试不经过开发机的外部 HTTP_PROXY。
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def test_real_cli_http_worker_and_unauthorized_requests(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opener.open(self.url + '/import/v1/jobs')
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()
        path = self.workspace / 'toc.csv'
        path.write_bytes(MAPPING)
        env = {**os.environ, 'IMPORT_TOKEN': 'test-import-token', 'IMPORT_API_URL': self.url}
        command = [sys.executable, str(ROOT / 'import-cli'), 'textbook', '--mode', 'mappings_only',
                   '--prefix', 'cnm', '--file', str(path), '--edition', 'importdemo_math',
                   '--label', '测试版本', '--source', '测试目录']
        created = subprocess.run(command, env=env, text=True, capture_output=True)
        self.assertEqual(created.returncode, 0, created.stderr)
        job = json.loads(created.stdout)['jobId']
        run_once(self.service)
        status = subprocess.run([sys.executable, str(ROOT / 'import-cli'), 'status', job],
                                env=env, text=True, capture_output=True)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)['status'], 'applied')
        req = urllib.request.Request(self.url + '/import/v1/jobs/' + job + '/diff',
                                     headers={'Authorization': 'Bearer test-import-token'})
        with self.opener.open(req) as response:
            self.assertIn('mappings', json.load(response))

    def test_review_requires_authentication_and_rejects_cross_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opener.open(self.url + '/import-review/')
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()
        req = urllib.request.Request(self.url + '/import/v1/session', data=b'{}',
                                     headers={'Authorization': 'Bearer test-import-token',
                                              'Content-Type': 'application/json', 'Origin': 'https://evil.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opener.open(req)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_cookie_review_approve_reject_and_csrf_gate(self):
        cookies = http.cookiejar.CookieJar()
        browser = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies))
        session = urllib.request.Request(self.url + '/import/v1/session', data=b'{}',
                                         headers={'Authorization': 'Bearer test-import-token',
                                                  'Content-Type': 'application/json', 'Origin': self.url})
        with browser.open(session) as response:
            self.assertIn('HttpOnly', response.headers['Set-Cookie'])
            self.assertIn('SameSite=Strict', response.headers['Set-Cookie'])
        with browser.open(self.url + '/import-review/') as response:
            self.assertIn('导入审核台', response.read().decode())
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        job = self.mapping(False)
        run_once(self.service)
        approve = self.url + '/import/v1/jobs/' + job + '/approve'
        bad = urllib.request.Request(approve, data=b'{}', headers={'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            browser.open(bad)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        good = urllib.request.Request(approve, data=b'{}', headers={'Content-Type': 'application/json',
                                       'Origin': self.url, 'X-Import-Review': '1'})
        with browser.open(good) as response:
            self.assertEqual(json.load(response)['status'], 'approved')
        second = self.expand()
        run_once(self.service)
        reject = urllib.request.Request(self.url + '/import/v1/jobs/' + second + '/reject',
                                         data=json.dumps({'reason': '需补充依据'}).encode(),
                                         headers={'Content-Type': 'application/json', 'Origin': self.url,
                                                  'X-Import-Review': '1'})
        with browser.open(reject) as response:
            self.assertEqual(json.load(response)['status'], 'rejected')

    def test_cli_missing_token_and_business_rejection_exit_codes(self):
        env = {key: value for key, value in os.environ.items() if key != 'IMPORT_TOKEN'}
        missing = subprocess.run([sys.executable, str(ROOT / 'import-cli'), 'list'], env=env, capture_output=True)
        self.assertEqual(missing.returncode, 1)
        job = self.expand()
        env.update(IMPORT_TOKEN='test-import-token', IMPORT_API_URL=self.url)
        refusal = subprocess.run([sys.executable, str(ROOT / 'import-cli'), 'apply', job], env=env, capture_output=True)
        self.assertEqual(refusal.returncode, 2)
        self.assertEqual(json.loads(refusal.stderr)['code'], 'REVIEW_REQUIRED')


if __name__ == '__main__':
    unittest.main()
