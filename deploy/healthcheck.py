"""静态release的正文、核心资源与JSON健康探针，重定向不能充当成功。"""
import hashlib
import json
from pathlib import Path
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PATHS = ('index.html', 'explore/index.html', 'diagnose/index.html', 'children/index.html',
         'assets/site.css', 'assets/home.js', 'diagnose/engine.js', 'sw.js',
         'data/topics.json', 'data/dependencies.json', 'data/questions.json',
         'data/cn-curriculum-standards.json', 'data/id-aliases.json')


class NoRedirect(HTTPRedirectHandler):
    """健康检查拒绝gate登录页/未知重定向。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def expected_files(root):
    """先检查release完整性，避免把缺文件的目录切成current。"""
    result = {}
    for relative in PATHS:
        path = Path(root) / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError('release缺少非空普通文件：' + relative)
        content = path.read_bytes()
        if relative.endswith('.json'):
            json.loads(content)
        result[relative] = hashlib.sha256(content).hexdigest()
    if (Path(root) / 'data/imports').exists():
        raise RuntimeError('release不允许包含私有imports目录')
    return result


def health_check(root, url, timeout=20, headers=None):
    """有界重试逐一比对HTTP 200及内容摘要，不能用登录页冒充健康。"""
    expected = expected_files(root)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + timeout
    last = ''
    while time.monotonic() < deadline:
        try:
            for relative, digest in expected.items():
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('健康检查超过时间预算')
                request = Request(url.rstrip('/')+'/'+relative, headers=headers or {})
                with opener.open(request, timeout=min(2, remaining)) as response:
                    if response.status != 200 or hashlib.sha256(response.read()).hexdigest() != digest:
                        raise RuntimeError('HTTP状态/正文摘要不符：' + relative)
            return {'ok': True, 'files': len(expected), 'indexSha256': expected['index.html']}
        except Exception as exc:
            last = str(exc)
            time.sleep(min(.15, max(0, deadline-time.monotonic())))
    raise RuntimeError('静态站健康检查失败：' + last)
