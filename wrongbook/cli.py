"""错题本管理CLI：离线身份/备份，其他操作经带Bearer的loopback API。"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .common import MAX_BODY, WrongbookError, identifier, json_text, new_private_file, private_path, require
from .paper import render_html
from .store import Service


class NoRedirect(HTTPRedirectHandler):
    """即使响应来自本机也禁止跟随跳转，避免把Authorization送到其他origin。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_input(path):
    """输入文件限2MiB，避免CLI先于服务端读取无限大文件。"""
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BODY + 1)
    require(len(raw) <= MAX_BODY, '输入文件超过2MiB')
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise WrongbookError('INVALID_INPUT', '文件不是合法JSON') from exc


def request(method, path, body=None):
    """从0600 token文件读取凭据，跳过环境代理且拒绝所有HTTP重定向。"""
    url = os.environ.get('WRONGBOOK_URL', 'http://127.0.0.1:8766')
    parsed = urlsplit(url)
    require(parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1') and
            not parsed.username and not parsed.password and parsed.path in ('', '/') and
            not parsed.query and not parsed.fragment, 'WRONGBOOK_URL须为SSH转发或本机loopback HTTP地址')
    token_path = os.environ.get('WRONGBOOK_TOKEN_FILE')
    require(bool(token_path), '必须设置WRONGBOOK_TOKEN_FILE')
    path_token = Path(token_path)
    require(not (path_token.stat().st_mode & 0o077), 'token文件必须为0600')
    with path_token.open() as stream:
        token = stream.read(257).strip()
    require(20 <= len(token) <= 256, 'token文件格式不合法')
    headers = {'Authorization': 'Bearer ' + token}
    raw = None
    if body is not None:
        raw = json_text(body).encode(); headers['Content-Type'] = 'application/json'
        require(len(raw) <= MAX_BODY, '请求超过2MiB')
    req = Request(url.rstrip('/') + path, data=raw, headers=headers, method=method)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        with exc:
            try:
                error = json.load(exc)
                code = error.get('code', 'HTTP_ERROR')
            except (ValueError, AttributeError):
                code = 'HTTP_ERROR'
        # 不回显远端正文、Location或请求头，保护密钥和作答原文。
        raise WrongbookError(code, f'API返回HTTP {exc.code}', exc.code) from None
    except (URLError, TimeoutError, ConnectionError) as exc:
        raise WrongbookError('CONNECTION_FAILED', '无法连接错题本API') from exc


def save_paper(paper, output, html):
    """独占保存私有JSON及可选学生HTML，不覆盖旧文件。"""
    roots = [r for r in os.environ.get('WRONGBOOK_STATIC_ROOTS', '').split(os.pathsep) if r]
    private_path(output, roots)
    if html:
        private_path(html, roots)
    with new_private_file(output, roots) as stream:
        stream.write((json.dumps(paper, ensure_ascii=False, indent=2) + '\n').encode())
    if html:
        with new_private_file(html, roots) as stream:
            stream.write(render_html(paper).encode())
    return {'paperId': paper['paperId'], 'stats': paper['stats'], 'output': str(output), 'html': str(html) if html else None}


def main():
    """执行显式孩子作用域的操作；管理命令只允许在持有私有工作区的主机运行。"""
    os.umask(0o077)
    p = argparse.ArgumentParser(description='服务端永久错题本CLI；token只从文件读取')
    subs = p.add_subparsers(dest='command', required=True)
    provision = subs.add_parser('provision', help='本机管理员创建用户和私有token文件')
    provision.add_argument('--user', required=True)
    provision.add_argument('--token-file', required=True, type=Path)
    backup = subs.add_parser('backup', help='本机管理员在线一致性备份')
    backup.add_argument('--output', required=True, type=Path)
    commands = {}
    for name in ('record', 'import-diagnosis', 'list', 'master', 'delete', 'paper', 'get-paper'):
        sub = subs.add_parser(name)
        sub.add_argument('--child', required=True)
        commands[name] = sub
    for name in ('record', 'import-diagnosis'):
        commands[name].add_argument('--file', required=True, type=Path)
    commands['import-diagnosis'].add_argument('--subject', required=True)
    commands['import-diagnosis'].add_argument('--session', required=True)
    for name in ('list', 'paper'):
        commands[name].add_argument('--subject', default='math' if name == 'paper' else None)
    commands['list'].add_argument('--status', default='active', choices=['active', 'mastered', 'all'])
    commands['list'].add_argument('--limit', type=int, default=100)
    commands['list'].add_argument('--offset', type=int, default=0)
    for name in ('master', 'delete'):
        commands[name].add_argument('--question', required=True)
    commands['master'].add_argument('--undo', action='store_true')
    commands['paper'].add_argument('--count', type=int, default=20)
    commands['paper'].add_argument('--wrong-count', type=int)
    commands['get-paper'].add_argument('--paper', required=True)
    for name in ('paper', 'get-paper'):
        commands[name].add_argument('--output', required=True, type=Path)
        commands[name].add_argument('--html', type=Path)
    args = p.parse_args()
    try:
        if args.command in ('provision', 'backup'):
            workspace = os.environ.get('WRONGBOOK_WORKSPACE')
            require(bool(workspace), '必须设置WRONGBOOK_WORKSPACE')
            roots = [r for r in os.environ.get('WRONGBOOK_STATIC_ROOTS', '').split(os.pathsep) if r]
            service = Service(workspace, static_roots=roots)
            if args.command == 'provision':
                service.provision(args.user, args.token_file)
                result = {'userId': args.user, 'tokenFile': str(args.token_file)}
            else:
                result = service.backup(args.output)
        else:
            base = '/wrongbook/v1/children/' + identifier(args.child)
            if args.command == 'record':
                result = request('POST', base + '/attempts', read_input(args.file))
            elif args.command == 'import-diagnosis':
                result = request('POST', base + '/diagnoses', dict(sourceSession=args.session, subject=args.subject, diagnosis=read_input(args.file)))
            elif args.command == 'list':
                params = dict(status=args.status, limit=args.limit, offset=args.offset)
                if args.subject:
                    params['subject'] = args.subject
                result = request('GET', base + '/attempts?' + urlencode(params))
            elif args.command == 'master':
                result = request('PATCH', base + '/questions/' + identifier(args.question), {'mastered': not args.undo})
            elif args.command == 'delete':
                result = request('DELETE', base + '/questions/' + identifier(args.question))
            elif args.command == 'paper':
                params = dict(subject=args.subject, count=args.count)
                if args.wrong_count is not None:
                    params['wrongCount'] = args.wrong_count
                result = save_paper(request('POST', base + '/papers', params), args.output, args.html)
            else:
                result = save_paper(request('GET', base + '/papers/' + identifier(args.paper)), args.output, args.html)
        print(json_text(result))
    except WrongbookError as exc:
        print(json_text({'code': exc.code, 'error': str(exc)}), file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, ValueError):
        print(json_text({'code': 'LOCAL_ERROR', 'error': '本地文件、配置或存储操作失败'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
