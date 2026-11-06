"""仅绑定 loopback 的运维 HTTP API；静态原文目录从不直接服务。"""
import argparse
from email.parser import BytesParser
from email.policy import default as email_policy
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, unquote, urlsplit

from .apply import apply_job
from .store import CODE_ROOT, ImportErrorResponse, Service, read_json
from .limits import MAX_REQUEST_BYTES, MIB

UI_ROOT = CODE_ROOT / 'review-ui'


def _multipart(content_type, body):
    """使用标准库 MIME 解析 multipart，拒绝重复字段与多个上传文件。"""
    message = BytesParser(policy=email_policy).parsebytes(
        ('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
    if not message.is_multipart() or message.defects:
        raise ImportErrorResponse('INVALID_MULTIPART', '需要合法 multipart/form-data', 400)
    params, filename, content = {}, None, None
    allowed = {'mode', 'subjectPrefix', 'edition', 'grade', 'term', 'domain', 'label', 'source', 'autoApplyMappings', 'ocrPages'}
    for part in message.iter_parts():
        name = part.get_param('name', header='content-disposition')
        if name == 'file':
            if content is not None or not part.get_filename():
                raise ImportErrorResponse('INVALID_MULTIPART', '仅允许一个命名源文件', 400)
            filename, content = part.get_filename(), part.get_payload(decode=True)
        elif name in allowed and name not in params:
            params[name] = part.get_payload(decode=True).decode('utf-8')
        else:
            raise ImportErrorResponse('INVALID_FIELD', '未知或重复的导入字段', 400)
    if content is None:
        raise ImportErrorResponse('SOURCE_REQUIRED', '缺少 file 字段', 400)
    if 'autoApplyMappings' in params:
        if params['autoApplyMappings'] not in ('true', 'false'):
            raise ImportErrorResponse('INVALID_FLAG', 'autoApplyMappings 必须为 true/false', 400)
        params['autoApplyMappings'] = params['autoApplyMappings'] == 'true'
    return filename, content, params


def make_server(service, token, port=8765, secure_cookie=False):
    """创建单请求 HTTP 服务；所有业务路径必须通过 token 或短期会话认证。

    登录页没有 job 数据；会话 cookie 不含运维 token。cookie 写请求要求同源及
    自定义头，Bearer CLI 不依赖浏览器 cookie。token 不进入 URL、磁盘或访问日志。
    """
    if not isinstance(token, str) or not token.strip():
        raise ImportErrorResponse('TOKEN_REQUIRED', '必须设置非空 IMPORT_TOKEN')
    sessions = {}

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            """限制本地异常客户端等待时间，避免顺序服务永久阻塞。"""
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format_string, *args):
            """不记录 URL 或请求体，避免误把敏感参数写入日志。"""
            logging.getLogger('import-api').info('request %s', self.command)

        def send_content(self, status, content, content_type='application/json; charset=utf-8', cookie=None):
            """所有响应禁止缓存并设置基本内容隔离；SW 隔离仍依赖独立 origin。"""
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(content)

        def send_json(self, status, value, cookie=None):
            """输出稳定 JSON，不把服务端异常堆栈返回客户端。"""
            self.send_content(status, json.dumps(value, ensure_ascii=False).encode(), cookie=cookie)

        def authenticate(self):
            """认证方式在服务器判断；客户端不能选择系统审批身份。"""
            authorization = self.headers.get('Authorization', '')
            if authorization.startswith('Bearer ') and hmac.compare_digest(authorization[7:].encode(), token.encode()):
                return 'bearer'
            cookies = SimpleCookie()
            cookies.load(self.headers.get('Cookie', ''))
            session = cookies.get('import_session')
            if session and sessions.get(session.value, 0) > time.monotonic():
                return 'cookie'
            raise ImportErrorResponse('UNAUTHORIZED', '需要有效 IMPORT_TOKEN 或审核会话', 401)

        def body(self):
            """请求体按显式长度读取；不接受 chunked 或超过本地导入上限的数据。"""
            if self.headers.get('Transfer-Encoding'):
                raise ImportErrorResponse('INVALID_LENGTH', '不支持 Transfer-Encoding', 400)
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError as exc:
                raise ImportErrorResponse('INVALID_LENGTH', 'Content-Length 不合法', 400) from exc
            if size < 0 or size > MAX_REQUEST_BYTES:
                raise ImportErrorResponse('REQUEST_TOO_LARGE', f'请求体不得超过 {MAX_REQUEST_BYTES // MIB} MiB', 413)
            content = self.rfile.read(size)
            if len(content) != size:
                raise ImportErrorResponse('INCOMPLETE_BODY', '请求体未完整接收', 400)
            return content

        def route(self, method):
            """仅路由明确资源；写操作统一经过认证、来源检查和状态机。"""
            parsed = urlsplit(self.path)
            path = unquote(parsed.path, errors='strict')
            public_assets = {'/import-login/': ('login.html', 'text/html; charset=utf-8'),
                             '/import-login/login.js': ('login.js', 'text/javascript; charset=utf-8'),
                             '/import-login/style.css': ('style.css', 'text/css; charset=utf-8')}
            if method == 'GET' and path in public_assets:
                filename, mime = public_assets[path]
                self.send_content(200, (UI_ROOT / filename).read_bytes(), mime)
                return
            authentication = self.authenticate()
            if method == 'POST':
                expected_origin = ('https' if secure_cookie else 'http') + '://' + self.headers.get('Host', '')
                origin = self.headers.get('Origin')
                if origin and origin != expected_origin:
                    raise ImportErrorResponse('ORIGIN_FORBIDDEN', '不允许跨源写操作', 403)
                if authentication == 'cookie' and (origin != expected_origin or self.headers.get('X-Import-Review') != '1'):
                    raise ImportErrorResponse('CSRF_FORBIDDEN', '审核写操作需要同源和审核请求头', 403)
            assets = {'/import-review/': ('index.html', 'text/html; charset=utf-8'),
                      '/import-review/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/import-review/style.css': ('style.css', 'text/css; charset=utf-8')}
            if method == 'GET' and path in assets:
                filename, mime = assets[path]
                self.send_content(200, (UI_ROOT / filename).read_bytes(), mime)
                return
            if method == 'POST' and path == '/import/v1/session':
                if authentication != 'bearer':
                    raise ImportErrorResponse('UNAUTHORIZED', '建立会话需要运维 token', 401)
                self.body()
                for key, expiry in list(sessions.items()):
                    if expiry <= time.monotonic():
                        del sessions[key]
                session = secrets.token_urlsafe(32)
                sessions[session] = time.monotonic() + 28800
                cookie = 'import_session=' + session + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800'
                if secure_cookie:
                    cookie += '; Secure'
                self.send_json(200, {'ok': True}, cookie=cookie)
                return
            if path == '/import/v1/jobs' and method == 'GET':
                query = parse_qs(parsed.query)
                limit = int(query.get('limit', ['100'])[0])
                if not 1 <= limit <= 500:
                    raise ImportErrorResponse('INVALID_LIMIT', 'limit 必须在 1–500', 400)
                self.send_json(200, service.list_jobs(query.get('status', [None])[0], query.get('type', [None])[0], limit))
                return
            prefix = '/import/v1/jobs/'
            if not path.startswith(prefix):
                raise ImportErrorResponse('NOT_FOUND', '接口不存在', 404)
            remaining = path[len(prefix):]
            if method == 'POST' and remaining in ('textbook', 'paper'):
                filename, content, params = _multipart(self.headers.get('Content-Type', ''), self.body())
                self.send_json(201, service.create_job(remaining, filename, content, params))
                return
            job_id, _, action = remaining.partition('/')
            if method == 'GET':
                if not action:
                    self.send_json(200, service.snapshot(job_id))
                    return
                if action == 'draft':
                    self.send_json(200, service.snapshot(job_id))
                    return
                if action == 'diff':
                    self.send_json(200, service.diff(job_id))
                    return
                if action.startswith('files/'):
                    self.send_content(200, service.read_file(job_id, action[6:]), 'text/plain; charset=utf-8')
                    return
            if method == 'POST':
                if self.headers.get_content_type() != 'application/json':
                    raise ImportErrorResponse('JSON_REQUIRED', '操作请求须使用 application/json', 400)
                data = json.loads(self.body() or b'{}')
                if not isinstance(data, dict):
                    raise ImportErrorResponse('INVALID_BODY', '请求体必须为对象', 400)
                allowed = {'approve': {'comment', 'expectedDraftSha256'}, 'draft': {'draft', 'expectedDraftSha256', 'comment'},
                           'reject': {'reason'}, 'apply': set(), 'requeue': set()}
                if action not in allowed:
                    raise ImportErrorResponse('NOT_FOUND', '操作不存在', 404)
                if set(data) - allowed[action]:
                    raise ImportErrorResponse('INVALID_FIELD', '不支持的审核字段（覆盖率不能绕过正式校验）', 400)
                if any(not isinstance(value, str) for key, value in data.items() if key != 'draft'):
                    raise ImportErrorResponse('INVALID_FIELD', '评论、原因与摘要必须为字符串', 400)
                if action in ('approve', 'draft') and not data.get('expectedDraftSha256'):
                    raise ImportErrorResponse('DRAFT_DIGEST_REQUIRED', '必须携带审核所见expectedDraftSha256', 400)
                if action == 'approve':
                    result = service.approve(job_id, data.get('comment', ''), expected_digest=data['expectedDraftSha256'])
                elif action == 'draft':
                    result = service.revise(job_id, data.get('draft'), data['expectedDraftSha256'], data.get('comment', ''))
                elif action == 'reject':
                    result = service.reject(job_id, data.get('reason', ''))
                elif action == 'requeue':
                    result = service.requeue(job_id)
                else:
                    result = apply_job(service, job_id)
                self.send_json(200, result)
                return
            raise ImportErrorResponse('NOT_FOUND', '接口不存在', 404)

        def dispatch(self, method):
            """将业务错误与系统错误区分；记录异常但不泄露 token 或原文。"""
            try:
                self.route(method)
            except ImportErrorResponse as exc:
                body = {'error': str(exc), 'code': exc.code}
                if exc.code == 'VALIDATION_FAILED':
                    body['reports'] = ['reports/validate.json']
                self.send_json(exc.status, body)
            except (ValueError, UnicodeError) as exc:
                self.send_json(400, {'error': '请求格式不合法：' + str(exc), 'code': 'INVALID_REQUEST'})
            except (OSError, KeyError, TypeError):
                logging.getLogger('import-api').exception('导入服务内部错误')
                self.send_json(500, {'error': '服务内部错误，请检查运维日志', 'code': 'INTERNAL_ERROR'})

        def do_GET(self):
            """处理只读或登录资源请求。"""
            self.dispatch('GET')

        def do_POST(self):
            """处理经统一鉴权的运维写操作。"""
            self.dispatch('POST')

    return HTTPServer(('127.0.0.1', port), Handler)


def main():
    """启动本地 API；缺少 workspace/token 时失败关闭。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=os.environ.get('IMPORT_WORKSPACE'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--secure-cookie', action='store_true', help='仅在独立 HTTPS 反代后使用')
    args = parser.parse_args()
    if not args.workspace:
        parser.error('必须设置 IMPORT_WORKSPACE 或 --workspace')
    logging.basicConfig(level=logging.INFO)
    try:
        service = Service(args.workspace)
        from .apply import recover_pending
        recover_pending(service)
        server = make_server(service, os.environ.get('IMPORT_TOKEN'), args.port, args.secure_cookie)
        logging.info('import-api 仅监听 127.0.0.1:%s', server.server_port)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    except (OSError, ImportErrorResponse) as exc:
        logging.error('%s: %s', getattr(exc, 'code', 'API_ERROR'), exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
