#!/usr/bin/env python3
"""部署独立loopback导入服务；不切静态current，不输出token。"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from publish import switch_link

ROOT = Path('/srv/cn-math-taxonomy')
ENV_FILE = Path('/etc/cn-math-import.env')
UNITS = ('import-api', 'import-worker')


def privileged(*argv, **kwargs):
    """只对安装配置与本模块units使用sudo，敏感文件内容始终留在进程内。"""
    return subprocess.run(['sudo', '-n', *argv], check=True, capture_output=True, **kwargs)


def install(content, target, mode='644'):
    """以受控权限安装单个配置文件。"""
    with tempfile.NamedTemporaryFile(dir=ROOT, delete=False) as stream:
        temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        stream.write(content)
    try:
        privileged('install', '-m', mode, str(temporary), str(target))
    finally:
        temporary.unlink(missing_ok=True)


def health(token):
    """验证独立origin的认证边界与业务API，token不写日志。"""
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic()+20
    while True:
        try:
            for path in ('/import/v1/jobs', '/import-review/'):
                try:
                    with opener.open('http://127.0.0.1:8765'+path, timeout=2):
                        raise RuntimeError('未鉴权访问意外成功')
                except HTTPError as exc:
                    try:
                        if exc.code != 401:
                            raise RuntimeError('未鉴权请求不是401')
                    finally:
                        exc.close()
            request = Request('http://127.0.0.1:8765/import/v1/jobs', headers={'Authorization': 'Bearer '+token})
            with opener.open(request, timeout=2) as response:
                if response.status != 200 or not isinstance(json.load(response), list):
                    raise RuntimeError('认证API健康失败')
            return
        except (OSError, URLError) as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError('导入API启动健康超时') from exc
            time.sleep(.2)


def main():
    """按SHA安装独立代码指针与持久workspace；失败恢复旧units/指针。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sha')
    args = parser.parse_args()
    mirror = ROOT/'repository.git'
    sha = subprocess.check_output(['git', '-C', str(mirror), 'rev-parse', '--verify', args.sha+'^{commit}'], text=True).strip()
    release = ROOT/'import-releases'/sha
    release.parent.mkdir(exist_ok=True)
    if not release.exists():
        staging = release.with_name('.staging-'+sha)
        if staging.exists(): shutil.rmtree(staging)
        staging.mkdir(mode=0o700)
        with tempfile.TemporaryFile(dir=ROOT) as stream:
            subprocess.run(['git', '-C', str(mirror), 'archive', sha], stdout=stream, check=True)
            stream.seek(0)
            with tarfile.open(fileobj=stream) as source:
                source.extractall(staging, filter='data')
        staging.rename(release)
    workspace = ROOT/'import-workspace'
    workspace.mkdir(mode=0o700, exist_ok=True)
    data = workspace/'data'
    if not data.exists():
        candidate = workspace/'data.preparing'
        if candidate.exists(): shutil.rmtree(candidate)
        candidate.mkdir(mode=0o700)
        for name in ('topics', 'dependencies', 'questions', 'cn-curriculum-standards', 'id-aliases'):
            shutil.copy2(release/'data'/(name+'.json'), candidate/(name+'.json'))
        shutil.copytree(release/'data/mappings', candidate/'mappings')
        candidate.rename(data)
    if ENV_FILE.exists():
        environment = privileged('cat', str(ENV_FILE)).stdout.decode()
        values = dict(line.split('=', 1) for line in environment.splitlines() if '=' in line and not line.startswith('#'))
        token = values.get('IMPORT_TOKEN', '')
        if not token or values.get('IMPORT_WORKSPACE') != str(workspace):
            raise RuntimeError('现有私密环境配置不符合workspace约定，停止覆盖')
    else:
        token = secrets.token_urlsafe(48)
        install(f'IMPORT_WORKSPACE={workspace}\nIMPORT_TOKEN={token}\nIMPORT_OCR_COMMAND=\n'.encode(), ENV_FILE, '600')
    pointer = ROOT/'import-current'
    previous = os.readlink(pointer) if pointer.is_symlink() else None
    saved, active = {}, {}
    for unit in UNITS:
        target = Path('/etc/systemd/system')/(unit+'.service')
        saved[unit] = target.read_bytes() if target.exists() else None
        active[unit] = subprocess.run(['systemctl', 'is-active', '--quiet', unit+'.service']).returncode == 0
    try:
        for unit in UNITS:
            privileged('systemctl', 'stop', unit+'.service') if active[unit] else None
        switch_link(pointer, release)
        for unit in UNITS:
            install((release/'deploy/systemd'/(unit+'.service.example')).read_bytes(), Path('/etc/systemd/system')/(unit+'.service'))
        privileged('systemctl', 'daemon-reload')
        for unit in UNITS:
            privileged('systemctl', 'restart', unit+'.service')
        health(token)
        for unit in UNITS:
            privileged('systemctl', 'is-active', '--quiet', unit+'.service')
            privileged('systemctl', 'enable', unit+'.service')
    except BaseException:
        for unit in UNITS:
            privileged('systemctl', 'stop', unit+'.service')
        if previous:
            switch_link(pointer, previous)
        else:
            pointer.unlink(missing_ok=True)
        for unit in UNITS:
            target = Path('/etc/systemd/system')/(unit+'.service')
            if saved[unit] is None:
                privileged('rm', '-f', str(target))
            else:
                install(saved[unit], target)
        privileged('systemctl', 'daemon-reload')
        for unit in UNITS:
            if active[unit]: privileged('systemctl', 'restart', unit+'.service')
        raise
    print(json.dumps(dict(ok=True, sha=sha, importCurrent=str(release), workspace=str(workspace),
                          origin='http://127.0.0.1:8765', authentication='401 without token / 200 with token',
                          ocr='disabled'), ensure_ascii=False))


if __name__ == '__main__':
    main()
