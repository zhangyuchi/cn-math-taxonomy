"""OCR 题图的有界搬运与当前草稿风险；资产只属于私有 job。"""
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit

from .limits import MAX_OCR_BYTES, MIB
from .store import atomic_bytes, digest_bytes
from .answers import GENERATED, UNCERTAIN, ORIGINAL

MAX_ASSET_BYTES = 16 * MIB
MAX_ASSETS_BYTES = 64 * MIB
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
ASSET = re.compile(r'!\[[^\]]*\]|<\s*(?:img|svg)\b', re.I)
LINK = re.compile(r'!\[[^\]]*\]\s*\(\s*(?:<([^>]+)>|([^\s)]+))[^)]*\)|'
                  r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\'][^>]*>', re.I)


def asset_references(text):
    """读取 Markdown/HTML 图片地址；不支持的内嵌资源保留为不可解析风险。"""
    refs, count = {}, 0
    for match in LINK.finditer(text):
        refs[next(g for g in match.groups() if g is not None)] = None
        count += 1
    if sum(1 for _ in ASSET.finditer(text)) > count:
        refs['[无法解析的图片引用]'] = None
    return list(refs)


def local_image(root, reference):
    """只接受根内相对图片普通文件；任何路径分量均不得为符号链接。"""
    url = urlsplit(reference)
    path = Path(unquote(url.path))
    if (url.scheme or url.netloc or url.query or url.fragment or path.is_absolute() or
            '..' in path.parts or path.suffix.lower() not in IMAGE_SUFFIXES):
        raise ValueError('图片地址不是受支持的本地相对路径')
    root = Path(root).resolve()
    target = root
    for part in path.parts:
        target /= part
        if target.is_symlink():
            raise ValueError('图片路径含符号链接')
    if not target.resolve().is_relative_to(root) or not stat.S_ISREG(target.stat().st_mode):
        raise ValueError('图片不是根内普通文件')
    if not 0 < target.stat().st_size <= MAX_ASSET_BYTES:
        raise ValueError('图片为空或超过16MiB')
    return target


def copy_assets(text, source_root, target_root, prefix, *, max_bytes=MAX_ASSETS_BYTES, max_text_bytes=None):
    """临时目录清理前复制明确引用的图片；缺失/非法/超限引用原样留给人审。

    两层 OCR 生命周期共用此边界；仅写可信目标目录，不访问网络。
    返回改写文本、成功路径与累计字节，调用方负责跨页累计限额。
    """
    replacements, copied, used = {}, [], 0
    max_text_bytes = MAX_OCR_BYTES if max_text_bytes is None else min(max_text_bytes, MAX_OCR_BYTES)
    occurrences = {}
    for match in LINK.finditer(text):
        ref = next(g for g in match.groups() if g is not None)
        occurrences[ref] = occurrences.get(ref, 0) + 1
    text_bytes = len(text.encode('utf-8'))
    for ref in asset_references(text):
        try:
            source = local_image(source_root, ref)
            with source.open('rb') as stream:
                content = stream.read(MAX_ASSET_BYTES + 1)
            if not content or len(content) > MAX_ASSET_BYTES or used + len(content) > max_bytes:
                continue
            name = digest_bytes(content) + source.suffix.lower()
            replacement = prefix + '/' + name
            growth = (len(replacement.encode('utf-8')) - len(ref.encode('utf-8'))) * occurrences.get(ref, 0)
            # 在构造替换文本之前限制增长；超限资产保留原引用供审，不使 OCR 归零。
            if text_bytes + growth > max_text_bytes:
                continue
            target = Path(target_root) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_bytes(target, content)
            replacements[ref] = replacement
            copied.append(replacements[ref])
            used += len(content)
            text_bytes += growth
        except (OSError, ValueError):
            continue

    def replace(match):
        for group in range(1, 4):
            ref = match[group]
            if ref in replacements:
                start, end = match.span(group)
                return match[0][:start-match.start()] + replacements[ref] + match[0][end-match.start():]
        return match[0]

    return LINK.sub(replace, text), copied, used


def question_risks(service, job_id, questions):
    """从当前草稿重算缺答案/资源风险；不信任初始报告，也不改变正式题字段。"""
    risks, codes = [], set()
    for q in questions:
        texts = [q.get(key, '') for key in ('prompt', 'answer', 'explanation')]
        choices = q.get('choices', [])
        if isinstance(choices, list):
            texts += [c.get('text', '') for c in choices if isinstance(c, dict)]
        refs = list(dict.fromkeys(ref for text in texts if isinstance(text, str)
                                  for ref in asset_references(text)))
        missing, assets = [], []
        for ref in refs:
            try:
                if not ref.startswith('source/assets/'):
                    raise ValueError('图片不是本任务已持久化资产')
                local_image(service.job_dir(job_id), ref)
                assets.append(ref)
            except (OSError, ValueError):
                missing.append(ref)
        answer_missing = not isinstance(q.get('answer'), str) or not q['answer'].strip()
        current = []
        explanation = q.get('explanation', '')
        explanation = explanation if isinstance(explanation, str) else ''
        generated, uncertain, original = (marker in explanation for marker in (GENERATED, UNCERTAIN, ORIGINAL))
        if generated:
            current.append('PAPER_ANSWER_GENERATED')
        if uncertain:
            current.append('PAPER_ANSWER_UNCERTAIN')
        if original:
            current.append('PAPER_ORIGINAL_REVIEW_REQUIRED')
        if answer_missing:
            current.append('PAPER_ANSWER_MISSING')
        if missing:
            current.append('PAPER_ASSET_MISSING')
        if assets:
            current.append('PAPER_ASSET_REVIEW_REQUIRED')
        if current:
            risks.append(dict(id=q.get('id'), answerMissing=answer_missing, missingAssets=missing,
                              assets=assets, riskCodes=current, requiresHumanReview=True,
                              generated=generated, answerUncertain=uncertain, originalRequired=original))
            codes.update(current)
    return dict(questionRisks=risks, riskCodes=sorted(codes), requiresHumanReview=True)
