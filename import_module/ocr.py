"""运维配置的可选PDF→Markdown插件；默认关闭，教材只允许显式目录页。"""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

from .store import ImportErrorResponse, atomic_bytes, atomic_json, digest_bytes
from .limits import MAX_OCR_BYTES, MIB


def _pages(value, required):
    """验证有界目录页范围，禁止默认整本教材OCR。"""
    if not value:
        if required:
            raise ImportErrorResponse('OCR_PAGES_REQUIRED', '教材PDF必须显式提供目录页 --ocr-pages（最多10页）')
        return ''
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*', value):
        raise ImportErrorResponse('OCR_PAGES_REQUIRED', '页码须如1-3,5，且最多10页')
    pages = set()
    for span in value.split(','):
        ends = [int(x) for x in span.split('-')]
        low, high = ends[0], ends[-1]
        if low < 1 or high > 10000 or high < low or high-low >= 10:
            raise ImportErrorResponse('OCR_PAGES_REQUIRED', '页范围不合法或超过10页')
        pages.update(range(low, high+1))
    if len(pages) > 10:
        raise ImportErrorResponse('OCR_PAGES_REQUIRED', '最多允许10页；教材只提供目录页')
    return ','.join(map(str, sorted(pages)))


def normalize_pdf(service, meta):
    """插件成功且输出经检查后才发布normalized；任何失败清理旧输出。

    命令仅从服务端环境读取，不经shell；保留原PDF与插件标识，不将命令或密钥写报告。
    子进程组超时整体终止，临时输出退出清理，VPS不默认配置插件。
    """
    directory = service.job_dir(meta['jobId'])
    normalized = directory / 'source/normalized.md'
    normalized.unlink(missing_ok=True)
    configured = os.environ.get('IMPORT_OCR_COMMAND')
    if not configured:
        raise ImportErrorResponse('OCR_REQUIRED', '未启用OCR插件；请提供目录/试卷文本或配置IMPORT_OCR_COMMAND')
    plugin_id = digest_bytes(configured.encode())
    report = {'pluginSha256': plugin_id, 'ok': False}
    try:
        try:
            command = json.loads(configured)
            timeout = float(os.environ.get('IMPORT_OCR_TIMEOUT', '120'))
            if (not isinstance(command, list) or not command or
                    any(not isinstance(arg, str) or not arg for arg in command) or not 0 < timeout <= 600):
                raise ValueError('需要非空argv与0–600秒超时')
        except (ValueError, TypeError) as exc:
            raise ImportErrorResponse('OCR_CONFIG_INVALID', 'OCR命令须为JSON argv，超时必须有效') from exc
        pages = _pages(meta['parameters'].get('ocrPages'), meta['type'] == 'textbook')
        report['pages'] = pages
        with tempfile.TemporaryDirectory(prefix='_ocr-', dir=directory) as temporary:
            output = Path(temporary) / 'normalized.md'
            argv = command + ['--input', str(directory / meta['sourceFiles'][0]), '--output', str(output),
                              '--kind', meta['type'], '--pages', pages]
            try:
                process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=True)
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise ImportErrorResponse('OCR_TIMEOUT', 'OCR插件超时，原PDF已保留') from exc
            except OSError as exc:
                raise ImportErrorResponse('OCR_FAILED', 'OCR插件无法启动，请检查用户空间依赖') from exc
            if process.returncode:
                raise ImportErrorResponse('OCR_FAILED', 'OCR插件退出失败，原PDF已保留')
            if output.is_symlink() or not output.is_file() or not 0 < output.stat().st_size <= MAX_OCR_BYTES:
                raise ImportErrorResponse('OCR_OUTPUT_INVALID', f'插件必须输出非空普通UTF8文件（≤{MAX_OCR_BYTES // MIB}MiB）')
            try:
                with output.open('rb') as stream:
                    content = stream.read(MAX_OCR_BYTES + 1)
                if len(content) > MAX_OCR_BYTES:
                    raise ValueError('输出超过上限')
                text = content.decode('utf-8-sig')
                if not text.strip() or '\x00' in text:
                    raise ValueError('空文本或NUL')
            except (UnicodeError, ValueError) as exc:
                raise ImportErrorResponse('OCR_OUTPUT_INVALID', '插件输出不是有效UTF8文本') from exc
            atomic_bytes(normalized, text.encode())
            report.update(ok=True, outputSha256=digest_bytes(text.encode()), confidence='unreviewed')
            return text.encode()
    except ImportErrorResponse as exc:
        report.update(code=exc.code, error=str(exc))
        raise
    finally:
        atomic_json(directory / 'reports/ocr.json', report)
