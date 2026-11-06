"""HOME MinerU 适配器的命令协议、输出边界与 worker 集成。"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from import_module.ocr import normalize_pdf
from import_module.store import ImportErrorResponse, Service

ADAPTER = ROOT / 'import-module/ocr_mineru.py'
FAKE = '''import json, os, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
home = Path.home()
with (home / 'argv.jsonl').open('a') as f:
    f.write(json.dumps(args) + '\\n')
mode = os.environ.get('FAKE_MINERU', '')
if mode == 'exit' or (mode == 'second-exit' and args[args.index('-s') + 1] == '2'):
    raise SystemExit(9)
if mode == 'sleep':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    (home / 'pids.json').write_text(json.dumps([os.getpid(), child.pid]))
    time.sleep(60)
output = Path(args[args.index('-o') + 1])
stem = Path(args[args.index('-p') + 1]).stem
path = output / stem / 'ocr' / (stem + '.md')
path.parent.mkdir(parents=True)
text = ('# 目录\\n## 正负数\\n' + str(args[args.index('-s') + 1])
        if '-s' in args else '# 整卷\\n')
if mode == 'missing':
    raise SystemExit(0)
if mode == 'symlink':
    path.symlink_to(home / 'argv.jsonl')
else:
    data = {'empty': b' ', 'invalid': b'\\xff', 'nul': b'a\\x00b',
            'large': b'a' * (1024 * 1024 + 1), 'limit': b'a' * (1024 * 1024),
            'aggregate': b'a' * (600 * 1024)}.get(mode, text.encode())
    path.write_bytes(data)
'''


class MinerUAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mineru-adapter-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.exe = self.home / 'tools/mineru/venv/bin/mineru'
        self.exe.parent.mkdir(parents=True)
        self.exe.write_text('#!' + sys.executable + '\n' + FAKE, encoding='utf-8')
        self.exe.chmod(0o755)
        self.input = self.home / '扫描 input.pdf'
        self.input.write_bytes(b'%PDF-test')
        self.output = self.home / 'result.md'
        self.env = dict(os.environ, HOME=str(self.home), FAKE_MINERU='')

    def invoke(self, pages='', kind='paper', mode=''):
        return subprocess.run([sys.executable, str(ADAPTER), '--input', str(self.input),
                               '--output', str(self.output), '--kind', kind, '--pages', pages],
                              env=dict(self.env, FAKE_MINERU=mode), capture_output=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in (self.home / 'argv.jsonl').read_text().splitlines()]

    def service(self):
        workspace = self.home / 'workspace'
        data = workspace / 'data'
        data.mkdir(parents=True)
        for name in ('topics.json', 'dependencies.json', 'questions.json',
                     'cn-curriculum-standards.json', 'id-aliases.json'):
            shutil.copy2(ROOT / 'data' / name, data / name)
        return Service(workspace)

    def test_home_cli_and_disjoint_pages_preserve_order(self):
        result = self.invoke('5,1-2,2', 'textbook')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        calls = self.calls()
        self.assertEqual([(c[c.index('-s')+1], c[c.index('-e')+1]) for c in calls],
                         [('0', '1'), ('4', '4')])
        for call in calls:
            self.assertEqual(call[call.index('-b')+1], 'pipeline')
            self.assertEqual(call[call.index('-m')+1], 'ocr')
            self.assertEqual(call[call.index('-l')+1], 'ch')
            self.assertEqual(call[call.index('-p')+1], str(self.input))
        self.assertEqual(self.output.read_text(), '# 目录\n## 正负数\n0\n\n# 目录\n## 正负数\n4')
        self.assertFalse(any(self.home.glob('mineru-*')))

    def test_paper_can_read_entire_document(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn('-s', self.calls()[0])
        self.assertNotIn('-e', self.calls()[0])

    def test_invalid_pages_never_start_mineru(self):
        for pages, kind in [('', 'textbook'), ('0', 'paper'), ('3-1', 'paper'),
                            ('1-11', 'textbook'), ('1,3,5,7,9,11,13,15,17,19,21', 'paper'),
                            ('1-1000000000', 'paper'), ('1;2', 'paper')]:
            with self.subTest(pages=pages, kind=kind):
                result = self.invoke(pages, kind)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
                self.assertFalse((self.home / 'argv.jsonl').exists())

    def test_failures_and_invalid_output_never_publish(self):
        for mode in ('exit', 'missing', 'empty', 'invalid', 'nul', 'large', 'symlink', 'aggregate'):
            with self.subTest(mode=mode):
                result = self.invoke('1,3', mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())

    def test_later_page_failure_does_not_publish_first_page(self):
        result = self.invoke('1,3', mode='second-exit')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 2)
        self.assertFalse(self.output.exists())

    def test_output_exactly_one_mib_is_accepted(self):
        result = self.invoke(mode='limit')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.output.stat().st_size, 1024 * 1024)

    def test_missing_home_install_is_explicit_failure(self):
        self.exe.unlink()
        result = self.invoke('1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'tools/mineru/venv/bin/mineru', result.stderr)
        self.assertFalse(self.output.exists())

    def test_worker_accepts_adapter_and_retains_original(self):
        service = self.service()
        meta = service.create_job('textbook', 'scan.pdf', b'%PDF-test',
                                  dict(subjectPrefix='cnm', mode='expand', ocrPages='1,3'))
        env = dict(self.env, IMPORT_OCR_COMMAND=json.dumps([sys.executable, str(ADAPTER)]),
                   IMPORT_OCR_TIMEOUT='10')
        with mock.patch.dict(os.environ, env):
            text = normalize_pdf(service, meta)
        self.assertIn('正负数', text.decode())
        self.assertTrue(json.loads((service.job_dir(meta['jobId']) / 'reports/ocr.json').read_text())['ok'])
        self.assertEqual(service.source(meta['jobId']), b'%PDF-test')

    def test_worker_timeout_kills_mineru_and_grandchild(self):
        service = self.service()
        meta = service.create_job('paper', 'scan.pdf', b'%PDF-test', dict(subjectPrefix='cnm'))
        env = dict(self.env, FAKE_MINERU='sleep',
                   IMPORT_OCR_COMMAND=json.dumps([sys.executable, str(ADAPTER)]), IMPORT_OCR_TIMEOUT='1')
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(ImportErrorResponse) as caught:
                normalize_pdf(service, meta)
        self.assertEqual(caught.exception.code, 'OCR_TIMEOUT')
        pids = json.loads((self.home / 'pids.json').read_text())
        def running(pid):
            try:
                return Path(f'/proc/{pid}/stat').read_text().split(') ')[1].split()[0] != 'Z'
            except FileNotFoundError:
                return False
        for _ in range(50):
            if not any(running(pid) for pid in pids):
                break
            time.sleep(0.02)
        for pid in pids:
            if running(pid):
                os.kill(pid, signal.SIGKILL)
                self.fail(f'OCR timeout left a live process: {pid}')
        self.assertFalse((service.job_dir(meta['jobId']) / 'source/normalized.md').exists())


if __name__ == '__main__':
    unittest.main()
