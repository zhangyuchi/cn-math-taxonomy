"""真实git archive/HTTP探针与受控systemd替身，验证发布及首次迁移回滚。"""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]


class PublishAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cn-math-publish-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git = self.root / 'repo'
        self.git.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.git)], check=True)
        for name in ('index.html', 'explore/index.html', 'diagnose/index.html', 'children/index.html',
                     'assets/site.css', 'assets/home.js', 'diagnose/engine.js', 'sw.js'):
            path = self.git / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('<!doctype html>原创发布测试' if name.endswith('.html') else '/* test */')
        (self.git / 'data/mappings').mkdir(parents=True)
        for name in ('topics', 'dependencies', 'questions', 'cn-curriculum-standards', 'id-aliases'):
            (self.git / f'data/{name}.json').write_text('[{"id":"test"}]' if name != 'id-aliases' else '{}')
        self.commit('first')
        self.deploy_root = self.root / 'srv'
        self.deploy_root.mkdir()
        self.units = self.root / 'systemd'
        self.units.mkdir()
        self.pidfile = self.root / 'server.pid'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        controller = self.root / 'systemctl'
        controller.write_text('''#!/usr/bin/env python3
import os,signal,subprocess,sys,time
from pathlib import Path
pidfile=Path(os.environ['FAKE_PIDFILE'])
command=sys.argv[1]
if command=='is-active': raise SystemExit(0 if pidfile.exists() else 3)
if command=='daemon-reload': raise SystemExit(0)
if command in ('stop','restart') and pidfile.exists():
    try: os.kill(int(pidfile.read_text()),signal.SIGTERM)
    except ProcessLookupError: pass
    pidfile.unlink(); time.sleep(.08)
if command=='stop': raise SystemExit(0)
if command=='restart':
    current=Path(os.environ['ROOT'])/'current'
    override=Path(os.environ['SYSTEMD_DIR'])/'cn-math.service.d/zz-release.conf'
    root=current.resolve() if override.exists() else Path(os.environ['LEGACY_ROOT'])
    if os.environ.get('FAIL_AFTER_SWITCH')=='1' and root!=Path(os.environ['LEGACY_ROOT']): root=Path(os.environ['LEGACY_ROOT'])
    p=subprocess.Popen([sys.executable,'-m','http.server',os.environ['STATIC_PORT'],'--bind','127.0.0.1','--directory',str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    pidfile.write_text(str(p.pid)); raise SystemExit(0)
raise SystemExit(1)
''')
        controller.chmod(0o755)
        self.env = {**os.environ, 'ROOT': str(self.deploy_root), 'REPO_MIRROR': str(self.git),
                    'PUBLISH_FETCH': '0', 'SYSTEMCTL': str(controller), 'SYSTEMD_DIR': str(self.units),
                    'PUBLISH_PRIVILEGE_PREFIX': '[]', 'STATIC_PORT': str(port), 'LEGACY_ROOT': str(self.git),
                    'FAKE_PIDFILE': str(self.pidfile), 'HEALTH_TIMEOUT': '2'}
        subprocess.run([str(controller), 'restart', 'cn-math.service'], env=self.env, check=True)
        self.addCleanup(self.stop_server)

    def stop_server(self):
        if self.pidfile.exists():
            try:
                os.kill(int(self.pidfile.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass

    def commit(self, message):
        subprocess.run(['git', '-C', str(self.git), 'add', '--all'], check=True)
        subprocess.run(['git', '-C', str(self.git), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', message], check=True)

    def publish(self, **env):
        return subprocess.run(['bash', str(REPO/'deploy/publish-to-qqvps.sh'), 'HEAD'],
                              env={**self.env, **env}, capture_output=True, text=True, timeout=30)

    def test_preflight_failure_preserves_current_and_unit(self):
        old = self.deploy_root / 'current'
        old.symlink_to(self.git)
        (self.git / 'sw.js').unlink()
        self.commit('incomplete')
        result = self.publish()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(old.resolve(), self.git)
        self.assertFalse((self.units/'cn-math.service.d/zz-release.conf').exists())

    def test_first_migration_postcheck_failure_restores_absence_and_service(self):
        # 新release与旧工作树内容不同，模拟重启仍服务旧目录；正文hash必须发现。
        old = self.root / 'legacy'
        import shutil
        shutil.copytree(self.git, old)
        (self.git/'index.html').write_text('<!doctype html>new release')
        self.commit('new page')
        self.env['LEGACY_ROOT'] = str(old)
        subprocess.run([self.env['SYSTEMCTL'], 'restart', 'cn-math.service'], env=self.env, check=True)
        result = self.publish(FAIL_AFTER_SWITCH='1')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.deploy_root/'current').exists())
        self.assertFalse((self.units/'cn-math.service.d/zz-release.conf').exists())
        intent = json.loads((self.deploy_root/'publish-intent.json').read_text())
        self.assertEqual(intent['state'], 'rolled_back')
        self.assertTrue(intent['rollbackHealthy'])

    def test_success_and_subsequent_failure_roll_back_existing_current(self):
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        previous = (self.deploy_root/'current').resolve()
        self.assertNotEqual(previous, self.git)
        self.assertEqual((previous/'index.html').read_bytes(), (self.git/'index.html').read_bytes())
        self.assertFalse((previous/'data/imports').exists())
        old_override = (self.units/'cn-math.service.d/zz-release.conf').read_bytes()
        (self.git/'index.html').write_text('<!doctype html>second release')
        self.commit('second page')
        self.env['LEGACY_ROOT'] = str(previous)
        result = self.publish(FAIL_AFTER_SWITCH='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.deploy_root/'current').resolve(), previous)
        self.assertEqual((self.units/'cn-math.service.d/zz-release.conf').read_bytes(), old_override)


if __name__ == '__main__':
    unittest.main()
