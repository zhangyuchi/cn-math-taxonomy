"""运维 CLI：只发送 HTTP 请求，禁止本地直接写正式数据。"""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid


class NoRedirect(HTTPRedirectHandler):
    """拒绝重定向，防止 Authorization 随未知地址转发。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """由 urllib 报告重定向响应，不自动请求新 URL。"""
        return None


def multipart(path, parameters):
    """编码一个文件及固定元数据字段；源字节不经过文本归一化。"""
    boundary = 'import-' + uuid.uuid4().hex
    blocks = []
    for key, value in parameters.items():
        if value is None:
            continue
        text = str(value).lower() if isinstance(value, bool) else str(value)
        blocks.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + text + '\r\n').encode())
    filename = path.name.replace('"', '_').replace('\r', '_').replace('\n', '_')
    blocks.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="' + filename
                   + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode())
    blocks.extend([path.read_bytes(), ('\r\n--' + boundary + '--\r\n').encode()])
    return b''.join(blocks), 'multipart/form-data; boundary=' + boundary


def main(argv=None):
    """CLI 成功 0，业务拒绝 2，系统/传输故障 1；token 仅从环境读取。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for kind in ('textbook', 'paper'):
        sub = commands.add_parser(kind)
        sub.add_argument('--prefix', required=True)
        sub.add_argument('--file', type=Path, required=True)
        sub.add_argument('--grade', type=int)
        sub.add_argument('--ocr-pages', help='教材PDF必须显式限定目录页，如1-3,5')
        sub.add_argument('--term', choices=('上', '下', '全'))
        if kind == 'textbook':
            sub.add_argument('--mode', required=True, choices=('expand', 'mappings_only'))
            sub.add_argument('--edition')
            sub.add_argument('--label')
            sub.add_argument('--source')
            sub.add_argument('--domain')
            sub.add_argument('--no-auto-apply-mappings', action='store_true')
    listing = commands.add_parser('list')
    listing.add_argument('--status')
    listing.add_argument('--type', choices=('textbook', 'paper'))
    listing.add_argument('--limit', type=int, default=100)
    for action in ('status', 'diff', 'draft', 'revise', 'approve', 'reject', 'apply', 'requeue'):
        sub = commands.add_parser(action)
        sub.add_argument('jobId')
        if action == 'approve':
            sub.add_argument('--comment', default='')
            sub.add_argument('--expected-digest')
        if action == 'revise':
            sub.add_argument('--file', type=Path, required=True)
            sub.add_argument('--expected-digest', required=True)
            sub.add_argument('--comment', default='')
        if action == 'reject':
            sub.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    token = os.environ.get('IMPORT_TOKEN')
    if not token or not token.strip():
        print(json.dumps({'code': 'TOKEN_REQUIRED', 'error': '必须设置 IMPORT_TOKEN'}, ensure_ascii=False), file=sys.stderr)
        return 1
    try:
        base = os.environ.get('IMPORT_API_URL', 'http://127.0.0.1:8765').rstrip('/')
        parsed = urlsplit(base)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('IMPORT_API_URL 必须为不含凭据/查询参数的 HTTP(S) URL')
        method, body, content_type = 'GET', None, 'application/json'
        endpoint = '/import/v1/jobs'
        if args.command in ('textbook', 'paper'):
            params = {'subjectPrefix': args.prefix, 'grade': args.grade, 'term': args.term, 'ocrPages': args.ocr_pages}
            if args.command == 'textbook':
                params.update(mode=args.mode, edition=args.edition, label=args.label, source=args.source,
                              domain=args.domain, autoApplyMappings=not args.no_auto_apply_mappings)
            body, content_type = multipart(args.file, params)
            endpoint += '/' + args.command
            method = 'POST'
        elif args.command == 'list':
            endpoint += '?' + urlencode({key: value for key, value in vars(args).items()
                                          if key != 'command' and value is not None})
        else:
            endpoint += '/' + quote(args.jobId, safe='')
            if args.command != 'status':
                endpoint += '/' + ('draft' if args.command == 'revise' else args.command)
            if args.command in ('approve', 'revise', 'reject', 'apply', 'requeue'):
                method = 'POST'
                data = {'comment': args.comment} if args.command == 'approve' else {'reason': args.reason} if args.command == 'reject' else {}
                if args.command == 'approve':
                    expected = args.expected_digest
                    if expected is None:
                        # CLI同样以实际读取快照作为所见版本，竞态由服务端409拒绝。
                        snapshot_request = Request(base + '/import/v1/jobs/' + quote(args.jobId, safe=''),
                                                   headers={'Authorization': 'Bearer ' + token})
                        local = parsed.hostname in ('127.0.0.1', 'localhost', '::1')
                        reader = build_opener(ProxyHandler({}) if local else ProxyHandler(), NoRedirect())
                        with reader.open(snapshot_request, timeout=90) as response:
                            expected = json.load(response)['draftSha256']
                    data['expectedDraftSha256'] = expected
                elif args.command == 'revise':
                    draft = json.loads(args.file.read_text(encoding='utf-8'))
                    data = dict(draft=draft.get('draft', draft), expectedDraftSha256=args.expected_digest, comment=args.comment)
                body = json.dumps(data, ensure_ascii=False).encode()
        request = Request(base + endpoint, body, method=method,
                          headers={'Authorization': 'Bearer ' + token, 'Content-Type': content_type})
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=90) as response:
            print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
        return 0
    except HTTPError as exc:
        try:
            try:
                message = json.loads(exc.read())
            except (ValueError, UnicodeError):
                message = {'error': 'HTTP ' + str(exc.code), 'code': 'HTTP_ERROR'}
        finally:
            exc.close()
        print(json.dumps(message, ensure_ascii=False), file=sys.stderr)
        return 2 if 400 <= exc.code < 500 else 1
    except (OSError, URLError, ValueError) as exc:
        print(json.dumps({'code': 'CLI_ERROR', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
