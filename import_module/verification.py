"""隔离调用现有题目核验逻辑，不写正式verification_report。"""
import json
import os
from pathlib import Path
import subprocess
import sys

from .store import CODE_ROOT, ImportErrorResponse


def verify_questions(questions):
    """在有内存/CPU/墙钟上限的子进程中核验草稿表达式。"""
    if not questions:
        return dict(auto_verified=[], mismatch=[], unverifiable=[], expression_errors=[])
    try:
        result = subprocess.run([sys.executable, '-m', 'import_module.verification'],
                                input=json.dumps(questions), capture_output=True, text=True,
                                cwd=CODE_ROOT, env=os.environ.copy(), timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise ImportErrorResponse('VERIFY_TIMEOUT', '题目核验超时，未写入正式库') from exc
    if result.returncode:
        raise ImportErrorResponse('VERIFY_FAILED', '核验进程失败或资源超限，未写入正式库')
    return json.loads(result.stdout)


def main():
    """内部受限进程入口；逐题捕获格式错误并标记mismatch。"""
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (384*1024*1024, 384*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    sys.path.insert(0, str(CODE_ROOT / 'scripts'))
    import verify_questions as verifier
    report = dict(auto_verified=[], mismatch=[], unverifiable=[], expression_errors=[])
    for question in json.load(sys.stdin):
        try:
            result = verifier.verify_all([question])
        except (KeyError, TypeError, ValueError) as exc:
            result = dict(auto_verified=[], mismatch=[{'id': question.get('id'), 'detail': '题结构错误：' + str(exc)}],
                          unverifiable=[], expression_errors=[])
        for key in report:
            report[key].extend(result[key])
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
