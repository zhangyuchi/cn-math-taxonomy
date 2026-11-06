#!/usr/bin/env python3
"""以独立API/worker/CLI进程验证完整导入链路，临时工作副本退出即清理。"""
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

                def worker(extra=None):
                    subprocess.run([sys.executable, str(ROOT / 'import-worker'), '--once'],
                                   env={**env, **(extra or {})}, cwd=ROOT, check=True, timeout=90, capture_output=True)

                topic_job = cli('textbook', '--mode', 'expand', '--prefix', 'cnm', '--file',
                                str(ROOT / 'import-module/examples/toc.md'))['jobId']
                worker()
                assert cli('status', topic_job)['status'] == 'needs_review'
                assert cli('apply', topic_job, expected=2)['code'] == 'REVIEW_REQUIRED'
                cli('approve', topic_job, '--comment', '仅验证人工门闸')
                assert cli('apply', topic_job, expected=2)['code'] == 'VALIDATION_FAILED'

                mapping_job = cli('textbook', '--mode', 'mappings_only', '--prefix', 'cnm', '--file',
                                  str(ROOT / 'import-module/examples/mappings.csv'), '--edition', 'importdemo_math',
                                  '--label', '导入演示版本', '--source', '仓库原创管道测试示例，不是真实教材对照')['jobId']
                worker()
                assert cli('status', mapping_job)['status'] == 'applied'
                assert len(json.loads((data / 'mappings/importdemo_math.json').read_text())['entries']) == 2
                assert all(path.read_bytes() == baseline[path.name] for path in data.glob('*.json'))
                paper = workspace / 'exam.md'
                paper.write_text('## Q1\ntopicId: cnm_001\ndifficulty: standard\ntype: numeric\n'
                                 'prompt: 负数-2的相反数是多少？\nanswer: 2\nexplanation: 相反数之和为零。\nverify: 2\n')
                paper_job = cli('paper', '--prefix', 'cnm', '--file', str(paper))['jobId']
                worker()
                assert cli('status', paper_job)['status'] == 'needs_review'
                assert cli('apply', paper_job, expected=2)['code'] == 'REVIEW_REQUIRED'
                cli('approve', paper_job, '--comment', '原创隔离测试题核对')
                cli('apply', paper_job)
                assert cli('status', paper_job)['status'] == 'applied'

                snapshot = cli('draft', topic_job)
                draft = snapshot['draft']
                for topic in draft['topics']:
                    tid = topic['id']
                    topic.update(description='原创管道演示：通过相反数练习认识负数。',
                         mastery_criteria=['能识别负数', '能求相反数'],
                         diagnostic_question={'question': '负数-2的相反数是多少？', 'answer': '2'},
                         standards=[{'id': 'cms-2022:4.NA.01', 'confidence': 'inferred'}])
                    draft['questions'].extend(dict(id=tid+'_q'+str(i), topicId=tid,
                       difficulty='basic' if i == 1 else 'standard', type='numeric',
                       prompt='负数-2的相反数是多少？' if i == 1 else f'负数-{i+1}的相反数是多少？',
                       answer=str(2 if i == 1 else i+1), explanation='相反数之和为零。',
                       verify=str(2 if i == 1 else i+1)) for i in range(1, 4))
                draft['dependencies'].insert(0, dict(topicId=draft['topics'][0]['id'], prerequisiteId='cnm_001',
                                              strength='soft', reason='人工校对：以已有正负数概念为基础。'))
                revised = workspace/'reviewed-draft.json'
                revised.write_text(json.dumps(draft, ensure_ascii=False))
                cli('revise', topic_job, '--file', str(revised), '--expected-digest', snapshot['draftSha256'],
                    '--comment', '原创演示补齐题目、课标及先修关系')
                cli('approve', topic_job)
                cli('apply', topic_job)
                assert cli('status', topic_job)['status'] == 'applied'
                # 用现有候选适配运行未放宽的正式validator，结果仍在隔离工作副本。
                checked = subprocess.run([sys.executable, '-m', 'import_module.validation', '--root', str(workspace)],
                                         cwd=ROOT, capture_output=True, text=True, check=True)
                assert json.loads(checked.stdout)['ok']
                pdf = workspace/'scan.pdf'
                pdf.write_bytes(b'%PDF-fixture')
                no_ocr = cli('paper', '--prefix', 'cnm', '--file', str(pdf))['jobId']
                worker({'IMPORT_OCR_COMMAND': ''})
                assert cli('status', no_ocr)['error']['code'] == 'OCR_REQUIRED'
                with_ocr = cli('paper', '--prefix', 'cnm', '--file', str(pdf))['jobId']
                worker({'IMPORT_OCR_COMMAND': json.dumps([sys.executable, str(ROOT/'import-module/ocr_fixture.py')])})
                assert cli('status', with_ocr)['status'] == 'needs_review'
                print(json.dumps(dict(ok=True, textbookJob=topic_job, paperJob=paper_job, mappingsJob=mapping_job,
                                      textbookStatus='approved → 完整修订 → 再审核 → applied', paperStatus='applied',
                                      mappingsStatus='applied', ocrDisabled='OCR_REQUIRED', ocrFixture='needs_review',
                                      validate='passed', nonApplyLargeFilesUnchanged=True,
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
