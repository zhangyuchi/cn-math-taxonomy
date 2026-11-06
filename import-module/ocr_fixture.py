#!/usr/bin/env python3
"""仅用于插件协议验收：不识别PDF，不代表真实OCR质量。"""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--kind', choices=('textbook', 'paper'), required=True)
parser.add_argument('--pages', default='')
args = parser.parse_args()
if not args.input.read_bytes().startswith(b'%PDF'):
    parser.error('fixture要求PDF标记')
if args.kind == 'textbook':
    if not args.pages:
        parser.error('目录fixture要求明确页码')
    text = 'grade: 7\nterm: 上\n# 原创接口测试目录\n## 正负数概念测试\n## 相反数测试\n'
else:
    text = ('## Q1\ntopicId: cnm_001\ndifficulty: standard\ntype: numeric\n'
            'prompt: 负数-2的相反数是多少？\nanswer: 2\nexplanation: 相反数之和为零。\nverify: 2\n')
args.output.write_text(text, encoding='utf-8')
