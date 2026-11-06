#!/usr/bin/env python3
"""在VPS本机通过既有签名机制验证真实nginx路径；不输出cookie或密钥。"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import ssl
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('release', type=Path)
parser.add_argument('--capture', action='store_true', help='切流前保存其他站点正文摘要')
parser.add_argument('--baseline', type=Path, default=Path('/srv/cn-math-taxonomy/operations/gate-before.json'))
args = parser.parse_args()
spec = importlib.util.spec_from_file_location('gate_probe', '/opt/gate/gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
# 只在受信任VPS本机访问自签名证书；不改gate密码/签名key，不将短期cookie落盘。
opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl._create_unverified_context()))
headers = {'Cookie': gate.COOKIE_NAME+'='+gate.issue_token()}


def fetch(path):
    """请求真实nginx，登录重定向不算受保护站点成功。"""
    with opener.open(Request('https://127.0.0.1'+path, headers=headers), timeout=8) as response:
        if response.status != 200 or response.url != 'https://127.0.0.1'+path:
            raise RuntimeError('nginx路径未返回预期200：'+path)
        return hashlib.sha256(response.read()).hexdigest()


others = {path: fetch(path) for path in ('/english-dictation/', '/jiangshan-map/', '/login', '/portal')}
if args.capture:
    args.baseline.parent.mkdir(parents=True, exist_ok=True)
    args.baseline.write_text(json.dumps(others, indent=2))
else:
    if others != json.loads(args.baseline.read_text()):
        raise RuntimeError('其他站点或gate正文发生变化')
checked = {}
for relative in ('', 'explore/', 'diagnose/', 'children/', 'assets/site.css', 'data/topics.json'):
    file = args.release/(relative+'index.html' if relative.endswith('/') or not relative else relative)
    actual = fetch('/k12-knowledge-graph/'+relative)
    if actual != hashlib.sha256(file.read_bytes()).hexdigest():
        raise RuntimeError('线上正文与release不符：'+relative)
    checked[relative or '/'] = 200
print(json.dumps({'ok': True, 'nginx': checked, 'otherSitesUnchanged': not args.capture,
                  'otherSiteHashes': others}, ensure_ascii=False))
