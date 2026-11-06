"""HTTP/CLI 共用的输入契约与私有文件边界。"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

CODE_ROOT = Path(__file__).resolve().parents[1]
PREFIXES = dict(cnm='math', cne='english', cnc='chinese', cnp='physics', cns='science',
                cnd='morality', cnh='history', cng='geography', cnb='biology', cnk='chemistry')
MAX_BODY = 2 * 1024 * 1024


class WrongbookError(Exception):
    """携带稳定错误码与 HTTP 状态的可恢复业务错误。"""
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def require(condition, message='字段、类型或题目归属不合法'):
    """输入不满足契约时统一返回400，不隐式修正客户端数据。"""
    if not condition:
        raise WrongbookError('INVALID_INPUT', message)


def identifier(value):
    """限定外部标识符，避免路径/命名空间歧义。"""
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is not None)
    return value


def subject_name(value):
    """验证十学科的稳定英文名称。"""
    require(isinstance(value, str) and value in PREFIXES.values(), 'subject 不合法')
    return value


def text(value, maximum=256):
    """验证非空短字符串，不把复杂对象隐式转换为文本。"""
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum)
    return value


def object_fields(value, allowed, required=()):
    """拒绝未知字段及缺失字段，特别是客户端提供的身份字段。"""
    require(isinstance(value, dict) and not (set(value) - set(allowed)) and set(required) <= set(value))


def timestamp(value):
    """仅接受带时区的ISO时间，并以固定宽度UTC保存以支持正确排序。"""
    require(isinstance(value, str) and len(value) <= 40)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(parsed.tzinfo is not None)
        return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except (ValueError, OverflowError) as exc:
        raise WrongbookError('INVALID_INPUT', '时间须为带时区的 ISO 8601 字符串') from exc


def now():
    """返回服务端UTC接收时间。"""
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def json_text(value):
    """生成稳定序列化，用于事件幂等比较和卷面快照。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def private_path(value, static_roots=()):
    """同时检查词法和真实路径，禁止私密状态进入代码或已知静态根。

    任意额外静态根必须由运维显式配置；无法自动发现系统所有HTTP服务。
    """
    path = Path(value)
    forbidden = [CODE_ROOT, Path('/srv/cn-math-taxonomy/current'), Path('/srv/cn-math-taxonomy/releases')]
    forbidden += [Path(p) for p in static_roots]
    if not path.is_absolute() or any(
            path.is_relative_to(root) or path.resolve().is_relative_to(root.resolve()) for root in forbidden):
        raise WrongbookError('INVALID_PATH', '私有文件必须使用仓库及静态根之外的绝对路径')
    return path


def new_private_file(path, static_roots=()):
    """独占创建0600文件，要求私有父目录；已有文件/符号链接均不覆盖。"""
    path = private_path(path, static_roots)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_mode & 0o077:
        raise WrongbookError('INVALID_PATH', '父目录须为私有目录（0700）')
    try:
        return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')
    except FileExistsError as exc:
        raise WrongbookError('INVALID_PATH', '输出文件已存在，不予覆盖') from exc
