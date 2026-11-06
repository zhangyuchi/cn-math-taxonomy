"""Chromium审核台验收；仅测试依赖Playwright，模块运行不依赖浏览器包。"""
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from import_module.api import make_server
from import_module.store import Service

OUT = ROOT / '.codex-run'
OUT.mkdir(exist_ok=True)
from import_module.worker import run_once

with tempfile.TemporaryDirectory(prefix='cn-math-import-browser-') as temporary:
    workspace = Path(temporary)
    data = workspace / 'data'
    data.mkdir()
    for name in ('topics.json', 'dependencies.json', 'questions.json',
                 'cn-curriculum-standards.json', 'id-aliases.json'):
        shutil.copy2(ROOT / 'data' / name, data / name)
    shutil.copytree(ROOT / 'data/mappings', data / 'mappings')
    service = Service(workspace)
    expand = service.create_job('textbook', 'toc.md',
                               (ROOT / 'import-module/examples/toc.md').read_bytes()
                               + b'## <img src=x onerror=alert(1)>\n',
                               dict(mode='expand', subjectPrefix='cnm'))['jobId']
    run_once(service)
    mapping = service.create_job('textbook', 'mappings.csv',
                                (ROOT / 'import-module/examples/mappings.csv').read_bytes(),
                                dict(mode='mappings_only', subjectPrefix='cnm', edition='importdemo_math',
                                     label='浏览器验收示例', source='原创测试目录', autoApplyMappings=False))['jobId']
    run_once(service)
    paper = service.create_job('paper', 'exam.md',
        '## Q1\ndifficulty: standard\ntype: numeric\nprompt: 负数-2的相反数是多少？\nanswer: 2\nexplanation: 相反数之和为零。\nverify: 2\n'.encode(),
        dict(subjectPrefix='cnm'))['jobId']
    run_once(service)
    token = secrets.token_urlsafe(32)
    server = make_server(service, token, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True,
                executable_path=os.environ.get('CHROME') or shutil.which('chromium'),
                args=['--no-proxy-server'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url + '/import-login/')
            page.get_by_label('运维 token').fill(token)
            page.get_by_role('button', name='进入审核台').click()
            page.wait_for_url('**/import-review/')
            page.locator('button[data-id="' + mapping + '"]').click()
            expect(page.locator('#job-state')).to_have_text('待审核')
            page.get_by_label('审核评论 / 驳回原因').fill('已对照示例目录')
            page.get_by_role('button', name='通过审核', exact=True).click()
            expect(page.locator('#job-state')).to_have_text('已批准')
            page.get_by_role('button', name='应用到工作副本', exact=True).click()
            expect(page.locator('#job-state')).to_have_text('已应用')
            page.locator('button[data-id="' + paper + '"]').click()
            page.get_by_text('修订完整草稿', exact=True).click()
            draft = service.draft(paper)
            draft['questions'][0]['topicId'] = 'cnm_001'
            page.get_by_label('五份草稿 JSON').fill(json.dumps(draft, ensure_ascii=False, indent=2))
            page.get_by_role('button', name='保存修订并重新送审', exact=True).click()
            expect(page.locator('#message')).to_contain_text('修订已保存')
            expect(page.locator('#approve')).to_be_enabled()
            displayed_digest = service.draft_digest(paper)
            changed = service.draft(paper)
            changed['questions'][0]['explanation'] += ' 另一个审核者完成了校对。'
            service.revise(paper, changed, displayed_digest)
            page.get_by_role('button', name='通过审核', exact=True).click()
            expect(page.locator('#message')).to_contain_text('DRAFT_CHANGED')
            assert service.get(paper)['status'] == 'needs_review'
            page.get_by_role('button', name='刷新', exact=True).click()
            expect(page.locator('#draft-digest')).to_have_text(service.draft_digest(paper))
            page.get_by_role('button', name='通过审核', exact=True).click()
            expect(page.locator('#job-state')).to_have_text('已批准')
            page.get_by_role('button', name='应用到工作副本', exact=True).click()
            expect(page.locator('#job-state')).to_have_text('已应用')
            page.screenshot(path=str(ROOT / '.codex-run/import-review-desktop.png'), full_page=True)
            page.locator('button[data-id="' + expand + '"]').click()
            expect(page.locator('#source')).to_contain_text('<img src=x onerror=alert(1)>')
            assert page.locator('#source img').count() == 0
            page.get_by_label('审核评论 / 驳回原因').fill('测试驳回：缺少真实课标与题库')
            page.get_by_role('button', name='驳回', exact=True).click()
            expect(page.locator('#job-state')).to_have_text('已驳回')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(ROOT / '.codex-run/import-review-mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert errors == [], errors
            assert service.get(mapping)['status'] == 'applied'
            assert service.get(expand)['status'] == 'rejected'
            assert service.get(paper)['status'] == 'applied'
            print(json.dumps(dict(ok=True, browser=browser.version, login=True, approve=True,
                                  apply=True, paperReviseApply=True, staleApprovalRejected=True, reject=True, sourceDisplayedAsText=True,
                                  mobileNoHorizontalOverflow=True, pageErrors=errors), ensure_ascii=False))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
