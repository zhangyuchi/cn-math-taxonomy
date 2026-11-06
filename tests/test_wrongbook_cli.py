"""完整CLI/真实服务进程验收；包括SIGKILL重启、备份恢复与再测。"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]


class WrongbookProcessTests(unittest.TestCase):
    def test_cli_process_restart_backup_print_and_retest(self):
        self.assertTrue((REPO / 'wrongbook/cli.py').is_file(), '缺少最小可运行CLI')
        with tempfile.TemporaryDirectory(prefix='wrongbook-process-') as tmp:
            root = Path(tmp)
            work = root / 'workspace'
            token = work / 'family.token'
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            env = {**os.environ, 'WRONGBOOK_WORKSPACE': str(work), 'WRONGBOOK_TOKEN_FILE': str(token),
                   'WRONGBOOK_URL': f'http://127.0.0.1:{port}'}
            def cli(*args, ok=True):
                result = subprocess.run([sys.executable, '-m', 'wrongbook.cli', *args], cwd=REPO,
                                        env=env, capture_output=True, text=True, timeout=20)
                if ok:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    return json.loads(result.stdout)
                self.assertNotEqual(result.returncode, 0)
                return result
            cli('provision', '--user', 'family', '--token-file', str(token))
            def start():
                log = open(root / 'server.log', 'ab')
                proc = subprocess.Popen([sys.executable, '-m', 'wrongbook.api', '--port', str(port)],
                                        cwd=REPO, env=env, stdout=log, stderr=log)
                log.close()
                self.addCleanup(lambda: proc.poll() is None and (proc.kill(), proc.wait()))
                for _ in range(100):
                    if proc.poll() is not None:
                        self.fail((root / 'server.log').read_text())
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.1):
                            return proc
                    except OSError:
                        time.sleep(.05)
                self.fail('服务未在5秒内启动')
            server = start()
            event = {'eventId': 'actual-1', 'questionId': 'cnm_p014_q1', 'topicId': 'cnm_p014',
                     'subject': 'math', 'result': 'wrong', 'sourceSession': 'actual-session',
                     'answeredAt': '2026-11-06T08:00:00Z', 'userAnswer': 'B'}
            incoming = root / 'attempts.json'
            incoming.write_text(json.dumps({'attempts': [event]}))
            self.assertEqual(cli('record', '--child', 'kid', '--file', str(incoming))['inserted'], 1)
            server.send_signal(signal.SIGKILL); server.wait(timeout=5)
            server = start()
            self.assertEqual(cli('list', '--child', 'kid')['total'], 1)
            output, html = root / 'paper.json', root / 'paper.html'
            result = cli('paper', '--child', 'kid', '--subject', 'math', '--count', '6', '--wrong-count', '1',
                         '--output', str(output), '--html', str(html))
            paper = json.loads(output.read_text())
            self.assertEqual(paper['paperId'], result['paperId'])
            self.assertEqual(paper['student']['questions'][0]['questionId'], event['questionId'])
            self.assertGreater(paper['stats']['new'], 0)
            self.assertIn('data:image/svg+xml;base64,', html.read_text())
            self.assertNotIn('长方体', html.read_text().split('class="choices"')[1].split('answer-space')[0])
            first_new = next(q for q in paper['student']['questions'] if q['source'] != 'wrong')
            event.update(eventId='retest-1', sourceSession='paper-' + paper['paperId'],
                         questionId=first_new['questionId'], topicId=first_new['topicId'])
            incoming.write_text(json.dumps({'attempts': [event]}))
            self.assertEqual(cli('record', '--child', 'kid', '--file', str(incoming))['inserted'], 1)
            self.assertEqual(cli('list', '--child', 'kid')['total'], 2)
            copy_path = root / 'copy.json'
            cli('get-paper', '--child', 'kid', '--paper', paper['paperId'], '--output', str(copy_path))
            self.assertEqual(json.loads(copy_path.read_text()), paper)
            backup = root / 'backup.sqlite3'
            cli('backup', '--output', str(backup))
            cli('delete', '--child', 'kid', '--question', event['questionId'])
            self.assertEqual(cli('list', '--child', 'kid')['total'], 1)
            server.terminate(); server.wait(timeout=5)
            import shutil
            shutil.copyfile(backup, work / 'wrongbook.sqlite3')
            server = start()
            self.assertEqual(cli('list', '--child', 'kid')['total'], 2)
            restored = root / 'restored-paper.json'
            cli('get-paper', '--child', 'kid', '--paper', paper['paperId'], '--output', str(restored))
            self.assertEqual(json.loads(restored.read_text()), paper)
            cli('master', '--child', 'kid', '--question', event['questionId'])
            self.assertEqual(cli('list', '--child', 'kid', '--status', 'mastered')['total'], 1)
            cli('master', '--child', 'kid', '--question', event['questionId'], '--undo')
            self.assertEqual(cli('list', '--child', 'kid')['total'], 2)
            # 重定向永远不能把Bearer密钥发往第二个origin。
            from http.server import BaseHTTPRequestHandler, HTTPServer
            import threading
            class Redirect(BaseHTTPRequestHandler):
                def log_message(self, *args): pass
                def do_GET(self):
                    self.send_response(302); self.send_header('Location', 'https://example.invalid/stolen'); self.end_headers()
            redirect = HTTPServer(('127.0.0.1', 0), Redirect)
            thread = threading.Thread(target=redirect.serve_forever, daemon=True); thread.start()
            previous = env['WRONGBOOK_URL']; env['WRONGBOOK_URL'] = f'http://127.0.0.1:{redirect.server_port}'
            try:
                error = cli('list', '--child', 'kid', ok=False)
                self.assertNotIn(token.read_text().strip(), error.stderr)
                self.assertIn('302', error.stderr)
            finally:
                env['WRONGBOOK_URL'] = previous
                redirect.shutdown(); redirect.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
