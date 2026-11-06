#!/usr/bin/env python3
"""以独立 API/worker/CLI 进程验证教材链路，临时工作副本退出即清理。"""
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def main():
    """验证实际包装入口、两条教材状态路径、审批与正式文件隔离。"""
    with tempfile.TemporaryDirectory(prefix='cn-math-import-smoke-') as temporary:
        workspace = Path(temporary)
        data = workspace / 'data'
        data.mkdir()
        for name in ('topics.json', 'dependencies.json', 'questions.json',
                     'cn-curriculum-standards.json', 'id-aliases.json'):
            shutil.copy2(ROOT / 'data' / name, data / name)
        shutil.copytree(ROOT / 'data/mappings', data / 'mappings')
        baseline = {path.name: path.read_bytes() for path in data.glob('*.json')}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        url = 'http://127.0.0.1:' + str(port)
        env = {**os.environ, 'IMPORT_WORKSPACE': str(workspace), 'IMPORT_TOKEN': secrets.token_urlsafe(32),
               'IMPORT_API_URL': url}
        with open(workspace / 'api.log', 'w') as log:
            api = subprocess.Popen([sys.executable, str(ROOT / 'import-api'), '--port', str(port)],
                                   cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                opener = build_opener(ProxyHandler({}))
                deadline = time.monotonic() + 10
                while True:
                    if api.poll() is not None:
                        raise RuntimeError('API 提前退出，请检查 ' + str(workspace / 'api.log'))
                    try:
                        req = Request(url + '/import/v1/jobs', headers={'Authorization': 'Bearer ' + env['IMPORT_TOKEN']})
                        with opener.open(req, timeout=1):
                            break
                    except URLError:
                        if time.monotonic() > deadline:
                            raise RuntimeError('API 启动超时')
                        time.sleep(0.05)

                def cli(*args, expected=0):
                    result = subprocess.run([sys.executable, str(ROOT / 'import-cli'), *args],
                                            env=env, cwd=ROOT, capture_output=True, text=True, timeout=90)
                    if result.returncode != expected:
                        raise RuntimeError('CLI 退出码不符：' + result.stderr)
                    return json.loads(result.stdout if expected == 0 else result.stderr)

                def worker():
                    subprocess.run([sys.executable, str(ROOT / 'import-worker'), '--once'],
                                   env=env, cwd=ROOT, check=True, timeout=90, capture_output=True)

                topic_job = cli('textbook', '--mode', 'expand', '--prefix', 'cnm', '--file',
                                str(ROOT / 'import-module/examples/toc.md'))['jobId']
                worker()
                assert cli('status', topic_job)['status'] == 'needs_review'
                assert cli('apply', topic_job, expected=2)['code'] == 'REVIEW_REQUIRED'
                cli('approve', topic_job, '--comment', '仅验证人工门闸')
                assert cli('apply', topic_job, expected=2)['code'] == 'APPLY_SCOPE_UNSUPPORTED'

                mapping_job = cli('textbook', '--mode', 'mappings_only', '--prefix', 'cnm', '--file',
                                  str(ROOT / 'import-module/examples/mappings.csv'), '--edition', 'importdemo_math',
                                  '--label', '导入演示版本', '--source', '仓库原创管道测试示例，不是真实教材对照')['jobId']
                worker()
                assert cli('status', mapping_job)['status'] == 'applied'
                assert len(json.loads((data / 'mappings/importdemo_math.json').read_text())['entries']) == 2
                assert all(path.read_bytes() == baseline[path.name] for path in data.glob('*.json'))
                print(json.dumps(dict(ok=True, textbookJob=topic_job, mappingsJob=mapping_job,
                                      textbookStatus='needs_review → approved → APPLY_SCOPE_UNSUPPORTED',
                                      mappingsStatus='applied', formalLargeFilesUnchanged=True,
                                      isolatedWorkspaceCleanedOnExit=True), ensure_ascii=False, indent=2))
            finally:
                api.terminate()
                try:
                    api.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    api.kill()
                    api.wait()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
