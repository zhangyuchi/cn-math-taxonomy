#!/usr/bin/env python3
"""将 HOME 独立安装的 MinerU 2.5.4 接入 IMPORT_OCR_COMMAND。"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from import_module.limits import MAX_OCR_BYTES, MIB


def page_ranges(value, kind):
    """校验现有协议的最多十页限制，并合并相邻页，避免识别未选中的页。"""
    if not value:
        if kind == 'textbook':
            raise ValueError('教材必须提供目录页 --pages（最多10页）')
        return [None]
    if not re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*', value):
        raise ValueError('页码须如 1-3,5')
    pages = set()
    for span in value.split(','):
        ends = [int(part) for part in span.split('-')]
        low, high = ends[0], ends[-1]
        if low < 1 or high > 10000 or high < low or high - low >= 10:
            raise ValueError('页范围不合法或超过10页')
        pages.update(range(low, high + 1))
        if len(pages) > 10:
            raise ValueError('最多允许10页')
    ranges = []
    for page in sorted(pages):
        if ranges and page == ranges[-1][1] + 1:
            ranges[-1][1] = page
        else:
            ranges.append([page, page])
    return ranges


def convert(source, target, kind, pages):
    """所有页段识别与有界 UTF-8 校验成功后才写入目标 MD。

    MinerU 必须继承 worker 的进程组；整组超时由 worker 统一负责。
    临时图像和中间文件仅存于本次任务目录，退出清理，不进入正式库。
    """
    ranges = page_ranges(pages, kind)
    mineru = Path.home() / 'tools/mineru/venv/bin/mineru'
    if not mineru.is_file() or not os.access(mineru, os.X_OK):
        raise ValueError(f'未安装可执行的 HOME MinerU：{mineru}')
    source = source.resolve(strict=True)
    env = dict(os.environ)
    env.setdefault('MINERU_DEVICE_MODE', 'cpu')
    env.setdefault('MINERU_MODEL_SOURCE', 'modelscope')
    content = bytearray()
    with tempfile.TemporaryDirectory(prefix='mineru-', dir=target.parent) as temporary:
        for index, span in enumerate(ranges):
            output = Path(temporary) / str(index)
            command = [str(mineru), '-p', str(source), '-o', str(output),
                       '-b', 'pipeline', '-m', 'ocr', '-l', 'ch']
            if span is not None:
                command += ['-s', str(span[0] - 1), '-e', str(span[1] - 1)]
            subprocess.run(command, env=env, check=True)
            # 2.5.4 的 CLI 可能捕获解析异常后返回0，必须另行验证预期输出。
            markdown = output / source.stem / 'ocr' / (source.stem + '.md')
            if markdown.is_symlink() or not markdown.is_file():
                raise ValueError('MinerU 未生成普通 Markdown 文件，请检查模型与运行日志')
            with markdown.open('rb') as stream:
                data = stream.read(MAX_OCR_BYTES + 1)
            if len(data) > MAX_OCR_BYTES:
                raise ValueError(f'MinerU Markdown 超过{MAX_OCR_BYTES // MIB}MiB')
            text = data.decode('utf-8-sig').strip()
            if not text or '\x00' in text:
                raise ValueError('MinerU Markdown 为空或含 NUL')
            if content:
                content.extend(b'\n\n')
            content.extend(text.encode('utf-8'))
            if len(content) > MAX_OCR_BYTES:
                raise ValueError(f'合并后的 Markdown 超过{MAX_OCR_BYTES // MIB}MiB')
    target.write_bytes(content)


def main():
    """解析 worker 追加的参数；依赖缺失、引擎失败或输出非法均非零退出。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kind', choices=('textbook', 'paper'), required=True)
    parser.add_argument('--pages', default='')
    args = parser.parse_args()
    try:
        convert(args.input, args.output, args.kind, args.pages)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'MinerU OCR 失败：{exc}\n')


if __name__ == '__main__':
    main()
