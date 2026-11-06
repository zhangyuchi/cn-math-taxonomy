"""独立loopback Bearer API；没有静态文件服务、cookie或CORS授权。"""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit

from .common import MAX_BODY, WrongbookError, json_text, require
from .store import Service


def make_server(service, port=8766):
    """固定绑定127.0.0.1，逐请求验证token并将身份限定到user/child。

    单请求服务配10秒socket超时及2MiB体积边界；任何错误都不回显请求或内部路径。
    """
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, fmt, *args):
            # 路径中含孩子或卷面ID，不进入日志。
            logging.getLogger('wrongbook').info('request %s', self.command)

        def send_json(self, status, value):
            content = json_text(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(content)

        def body(self):
            """按明确长度读取JSON对象，拒绝chunked、重复长度和大请求。"""
            require(not self.headers.get('Transfer-Encoding'), '不支持Transfer-Encoding')
            lengths = self.headers.get_all('Content-Length', [])
            require(len(lengths) == 1 and len(lengths[0]) <= 10 and lengths[0].isascii() and lengths[0].isdigit(), '需要合法Content-Length')
            size = int(lengths[0])
            if size > MAX_BODY:
                raise WrongbookError('REQUEST_TOO_LARGE', '请求不得超过2MiB', 413)
            require(self.headers.get_content_type() == 'application/json', '需要application/json')
            raw = self.rfile.read(size)
            require(len(raw) == size, '请求体不完整')
            try:
                value = json.loads(raw)
            except (ValueError, RecursionError) as exc:
                raise WrongbookError('INVALID_INPUT', 'JSON无效') from exc
            require(isinstance(value, dict), 'JSON必须为对象')
            return value

        def route(self, method):
            auth = self.headers.get('Authorization', '')
            user = service.authenticate(auth[7:] if auth.startswith('Bearer ') else '')
            parsed = urlsplit(self.path)
            if method in ('POST', 'PATCH', 'DELETE'):
                origin = self.headers.get('Origin')
                if origin is not None and origin != 'http://' + self.headers.get('Host', ''):
                    raise WrongbookError('ORIGIN_FORBIDDEN', '不允许跨源写操作', 403)
            if parsed.path == '/wrongbook/v1/health' and method == 'GET':
                self.send_json(200, {'ok': True}); return
            parts = parsed.path.split('/')
            if len(parts) < 6 or parts[:4] != ['', 'wrongbook', 'v1', 'children']:
                raise WrongbookError('NOT_FOUND', '接口不存在', 404)
            child, resource = parts[4:6]
            query = parse_qs(parsed.query, keep_blank_values=True)
            if method == 'GET' and resource == 'attempts' and len(parts) == 6:
                require(not (set(query) - {'status', 'subject', 'limit', 'offset'}) and all(len(v) == 1 for v in query.values()))
                params = {k: v[0] for k, v in query.items()}
                try:
                    for key in ('limit', 'offset'):
                        if key in params:
                            params[key] = int(params[key])
                except ValueError as exc:
                    raise WrongbookError('INVALID_INPUT', '分页参数必须为整数') from exc
                result = service.list_attempts(user, child, **params)
            elif resource == 'attempts' and method == 'POST' and len(parts) == 6:
                result = service.record(user, child, self.body())
            elif resource == 'diagnoses' and method == 'POST' and len(parts) == 6:
                result = service.import_diagnosis(user, child, self.body())
            elif resource == 'questions' and len(parts) == 7 and method == 'PATCH':
                result = service.mark(user, child, parts[6], self.body())
            elif resource == 'questions' and len(parts) == 7 and method == 'DELETE':
                result = service.delete(user, child, parts[6])
            elif resource == 'papers' and len(parts) == 6 and method == 'POST':
                self.send_json(201, service.create_paper(user, child, self.body())); return
            elif resource == 'papers' and len(parts) == 7 and method == 'GET':
                result = service.get_paper(user, child, parts[6])
            else:
                raise WrongbookError('NOT_FOUND', '接口不存在', 404)
            self.send_json(200, result)

        def dispatch(self, method):
            """业务错误保留稳定码，数据库争用503，其余内部失败不泄露敏感上下文。"""
            try:
                self.route(method)
            except WrongbookError as exc:
                self.send_json(exc.status, {'code': exc.code, 'error': str(exc)})
            except sqlite3.OperationalError:
                self.send_json(503, {'code': 'STORAGE_UNAVAILABLE', 'error': '存储暂不可用，请保留eventId稍后重试'})
            except (TimeoutError, ConnectionError):
                self.close_connection = True
            except Exception:
                logging.getLogger('wrongbook').error('internal failure')
                self.send_json(500, {'code': 'INTERNAL_ERROR', 'error': '服务内部错误'})
        def do_GET(self):
            self.dispatch('GET')
        def do_POST(self):
            self.dispatch('POST')
        def do_PATCH(self):
            self.dispatch('PATCH')
        def do_DELETE(self):
            self.dispatch('DELETE')
    return HTTPServer(('127.0.0.1', port), Handler)


def main():
    """从显式私有工作区启动服务；配置缺失时拒绝推导到静态根。"""
    parser = argparse.ArgumentParser(description='服务端错题本loopback API')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--catalog', type=Path)
    args = parser.parse_args()
    workspace = os.environ.get('WRONGBOOK_WORKSPACE')
    if not workspace:
        parser.error('必须设置WRONGBOOK_WORKSPACE为私有绝对目录')
    roots = os.environ.get('WRONGBOOK_STATIC_ROOTS', '').split(os.pathsep)
    try:
        service = Service(workspace, args.catalog, [p for p in roots if p])
        with make_server(service, args.port) as server:
            print('wrongbook API ready on loopback', flush=True)
            server.serve_forever()
    except (WrongbookError, OSError) as exc:
        parser.exit(1, f'错题服务启动失败：{type(exc).__name__}\n')


if __name__ == '__main__':
    main()
