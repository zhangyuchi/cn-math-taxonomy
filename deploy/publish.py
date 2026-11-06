#!/usr/bin/env python3
"""qqvps release发布：预检、持久切流意图、正文健康检查与失败自动回滚。"""
import argparse
import base64
from datetime import datetime, timezone
import fcntl
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import functools
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import uuid

from healthcheck import expected_files, health_check


def persist(path, value):
    """同目录fsync并替换发布元数据；与导入运行状态分离。"""
    content = (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.publish-')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_dir(path):
    """持久化目录项。"""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def switch_link(link, target):
    """同目录原子切换current；不在release中写运行状态。"""
    temporary = link.with_name('.current-'+uuid.uuid4().hex)
    temporary.symlink_to(target)
    os.replace(temporary, link)
    sync_dir(link.parent)


class QuietHandler(SimpleHTTPRequestHandler):
    """预检不打印资源路径。"""
    def log_message(self, *args):
        pass


class Publisher:
    """独占发布锁，保存首次迁移配置，并在健康失败时恢复原运行状态。"""
    def __init__(self):
        self.root = Path(os.environ.get('ROOT', '/srv/cn-math-taxonomy')).resolve()
        self.mirror = Path(os.environ.get('REPO_MIRROR', str(self.root/'repository.git')))
        self.current = self.root/'current'
        self.unit_dir = Path(os.environ.get('SYSTEMD_DIR', '/etc/systemd/system'))
        self.dropin = self.unit_dir/'cn-math.service.d/zz-release.conf'
        self.control_command = os.environ.get('SYSTEMCTL', 'systemctl')
        self.privilege = json.loads(os.environ.get('PUBLISH_PRIVILEGE_PREFIX', '["sudo","-n"]'))
        self.port = int(os.environ.get('STATIC_PORT', '8000'))
        self.timeout = float(os.environ.get('HEALTH_TIMEOUT', '20'))
        if not 0 < self.timeout <= 60:
            raise ValueError('HEALTH_TIMEOUT必须在0–60秒')
        self.url = f'http://127.0.0.1:{self.port}'
        self.intent_path = self.root/'publish-intent.json'

    def control(self, *args, check=True):
        """仅控制本次cn-math服务，不重启共享nginx/gate。"""
        return subprocess.run(self.privilege+[self.control_command, *args], check=check,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)

    def install_dropin(self, content):
        """只变更本脚本拥有的drop-in，原unit和其他drop-in保持原样。"""
        if content is None:
            subprocess.run(self.privilege+['rm', '-f', str(self.dropin)], check=True, timeout=10)
            return
        with tempfile.NamedTemporaryFile(dir=self.root, prefix='.dropin-', delete=False) as stream:
            path = Path(stream.name)
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        try:
            subprocess.run(self.privilege+['mkdir', '-p', str(self.dropin.parent)], check=True, timeout=10)
            subprocess.run(self.privilege+['install', '-m', '644', str(path), str(self.dropin)], check=True, timeout=10)
        finally:
            path.unlink(missing_ok=True)

    def rollback(self, intent):
        """恢复current存在性、原drop-in和服务运行状态，并检查旧站正文。"""
        if intent['previousCurrent'] is None:
            self.current.unlink(missing_ok=True)
            sync_dir(self.root)
        else:
            switch_link(self.current, intent['previousCurrent'])
        old = intent['previousDropin']
        self.install_dropin(base64.b64decode(old) if old is not None else None)
        self.control('daemon-reload')
        self.control('restart' if intent['wasActive'] else 'stop', 'cn-math.service')
        if intent['wasActive']:
            health_check(intent['rollbackRoot'], self.url, self.timeout)
        intent['state'], intent['rollbackHealthy'] = 'rolled_back', True
        persist(self.intent_path, intent)
        print('ROLLBACK: 已恢复切流前current、服务配置及旧站健康', flush=True)

    def publish(self, ref):
        """创建干净archive并先验证；切流后任意失败返回非零且自动回滚。"""
        # 恢复只依赖本机已持久状态，不能被新ref或网络故障阻断。
        if self.intent_path.exists():
            pending = json.loads(self.intent_path.read_text())
            if pending.get('state') == 'switching':
                self.rollback(pending)
        if os.environ.get('PUBLISH_FETCH', '1') != '0':
            subprocess.run(['git', '-C', str(self.mirror), 'fetch', '--all', '--tags', '--prune'], check=True, timeout=120)
        sha = subprocess.check_output(['git', '-C', str(self.mirror), 'rev-parse', '--verify', ref+'^{commit}'], text=True).strip()
        if self.current.exists() and not self.current.is_symlink():
            raise RuntimeError('current必须为symlink，拒绝覆盖目录')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        release = self.root/'releases'/(stamp+'-'+sha[:12]+'-'+uuid.uuid4().hex[:6])
        release.mkdir(parents=True)
        with tempfile.TemporaryFile(dir=self.root) as archive:
            subprocess.run(['git', '-C', str(self.mirror), 'archive', '--format=tar', sha], stdout=archive, check=True, timeout=60)
            archive.seek(0)
            with tarfile.open(fileobj=archive) as source:
                source.extractall(release, filter='data')
        for relative in ('data/imports', '.codex-run', '.github', 'review-ui', 'import_module', 'import-module',
                         'import-api', 'import-worker', 'import-cli'):
            path = release/relative
            if path.is_dir(): shutil.rmtree(path)
            elif path.exists() or path.is_symlink(): path.unlink()
        expected_files(release)
        handler = functools.partial(QuietHandler, directory=str(release))
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            health_check(release, 'http://127.0.0.1:'+str(server.server_port), self.timeout)
        finally:
            server.shutdown(); server.server_close(); thread.join()
        previous = os.readlink(self.current) if self.current.is_symlink() else None
        rollback_root = self.current.resolve() if previous is not None else Path(os.environ.get('LEGACY_ROOT', '/home/ubuntu/Projects/cn-math-taxonomy'))
        active = self.control('is-active', '--quiet', 'cn-math.service', check=False).returncode == 0
        if active:
            # 首次迁移前也要求旧站健康，确保回滚目标实际存在。
            health_check(rollback_root, self.url, self.timeout)
        old_dropin = self.dropin.read_bytes() if self.dropin.exists() else None
        intent = dict(state='switching', sha=sha, release=str(release), previousCurrent=previous,
                      previousDropin=base64.b64encode(old_dropin).decode() if old_dropin is not None else None,
                      wasActive=active, rollbackRoot=str(rollback_root))
        # 只新增本脚本专用drop-in；旧base unit/其他drop-ins不变，记录供审计。
        unit = self.unit_dir/'cn-math.service'
        intent['baseUnitSha256'] = __import__('hashlib').sha256(unit.read_bytes()).hexdigest() if unit.exists() else None
        persist(self.intent_path, intent)
        try:
            content = (f'[Service]\nWorkingDirectory={self.current}\nExecStart=\n'
                       f'ExecStart=/usr/bin/python3 -m http.server {self.port} --bind 127.0.0.1 --directory {self.current}\n').encode()
            self.install_dropin(content)
            switch_link(self.current, release)
            self.control('daemon-reload')
            self.control('restart', 'cn-math.service')
            result = health_check(release, self.url, self.timeout)
            gate_check = json.loads(os.environ.get('PUBLISH_GATE_CHECK', '[]'))
            if gate_check:
                subprocess.run(gate_check+[str(release)], check=True, timeout=60)
            intent['state'], intent['health'] = 'committed', result
            persist(self.intent_path, intent)
        except BaseException:
            self.rollback(intent)
            raise
        print(json.dumps(dict(ok=True, sha=sha, current=str(release), health=result), ensure_ascii=False), flush=True)
        return release


def main():
    """CLI发布入口，独占锁防止两次切流交错；SIGTERM也进入补偿回滚。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ref')
    args = parser.parse_args()
    publisher = Publisher()
    publisher.root.mkdir(parents=True, exist_ok=True)
    (publisher.root/'releases').mkdir(exist_ok=True)
    def terminate(signum, frame):
        raise KeyboardInterrupt('发布收到终止信号')
    signal.signal(signal.SIGTERM, terminate)
    try:
        with open(publisher.root/'.publish.lock', 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            publisher.publish(args.ref)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print('PUBLISH_FAILED: '+str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
