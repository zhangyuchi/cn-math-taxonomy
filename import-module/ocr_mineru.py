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
from import_module.paper_assets import MAX_ASSETS_BYTES, copy_assets


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
    仅明确引用的安全图片复制到目标旁路资产，供 worker 持久化；中间文件退出清理。
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
    asset_bytes = 0
    segments, original_bytes = [], 0
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
            original_bytes += len(text.encode('utf-8')) + (2 if segments else 0)
            if original_bytes > MAX_OCR_BYTES:
                raise ValueError(f'合并后的 Markdown 超过{MAX_OCR_BYTES // MIB}MiB')
            segments.append((index, markdown.parent, text))
        # 先验证完整原文，再分配全局路径增长预算；缺图不能使合规的多页 OCR 失败。
        growth_budget = MAX_OCR_BYTES - original_bytes
        for index, source_root, text in segments:
            original_size = len(text.encode('utf-8'))
            # 页段使用独立命名空间，避免各页同名题图相互覆盖。
            prefix = target.stem + '-assets/' + str(index)
            text, _, used = copy_assets(text, source_root, target.parent / prefix, prefix,
                                       max_bytes=MAX_ASSETS_BYTES - asset_bytes,
                                       max_text_bytes=original_size + growth_budget)
            asset_bytes += used
            growth_budget -= len(text.encode('utf-8')) - original_size
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
