"""导入各入口共用的字节上限；内存缓冲仍须有界。"""
from pathlib import Path

MIB = 1024 * 1024
MAX_TEXT_BYTES = 16 * MIB
MAX_PDF_BYTES = 64 * MIB
MAX_OCR_BYTES = 64 * MIB
MAX_DRAFT_BYTES = 64 * MIB
MAX_REQUEST_BYTES = 65 * MIB


def source_limit(filename):
    """按文件类型限制源文件；PDF 为扫描图像预留更大空间。"""
    return MAX_PDF_BYTES if Path(filename).suffix.lower() == '.pdf' else MAX_TEXT_BYTES


def read_source(path):
    """至多读取类型上限加一字节，调用方据长度拒绝超限内容。"""
    with Path(path).open('rb') as stream:
        return stream.read(source_limit(path) + 1)
