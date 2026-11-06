'use strict';

const labels = {
  queued: '排队中', running: '处理中', needs_review: '待审核', approved: '已批准',
  applied: '已应用', failed: '失败', rejected: '已驳回'
};
let selected = null;
let displayed = null;
const node = (id) => document.getElementById(id);

// 使用原文节点展示两段解析；不执行草稿 HTML，也不把完整解析放进短答案。
function renderAnswers(meta) {
  const preview = node('answer-preview');
  preview.replaceChildren();
  const risks = new Map((meta.reports.parse.questionRisks || []).map(risk => [risk.id, risk]));
  for (const [index, question] of (meta.draft.questions || []).entries()) {
    const article = document.createElement('article');
    article.className = 'answer-card';
    const title = document.createElement('h3');
    title.textContent = '第 ' + (index + 1) + ' 题 · ' + question.id;
    const answer = document.createElement('p');
    answer.textContent = '最终答案：' + (question.answer || '未填写，禁止入库');
    const risk = document.createElement('p');
    risk.className = 'answer-risk';
    const codes = risks.get(question.id);
    risk.textContent = codes ? '待核对：' + codes.riskCodes.join(' / ') : '';
    article.append(title, answer, risk);
    const explanation = question.explanation || '';
    const parts = /^### 思路提示\s*\n([\s\S]*?)\n+### 详细解答\s*\n([\s\S]*)$/.exec(explanation);
    for (const [label, text] of [['思路提示', parts ? parts[1] : '旧草稿未分段，请修订。'],
                                 ['详细解答', parts ? parts[2] : explanation]]) {
      const heading = document.createElement('h4');
      const content = document.createElement('pre');
      heading.textContent = label;
      content.textContent = text;
      article.append(heading, content);
    }
    preview.append(article);
  }
}

// 所有 job 内容只经 textContent 展示，不将上传文本解释成 HTML。
async function request(path, body) {
  const options = { credentials: 'same-origin', cache: 'no-store' };
  if (body !== undefined) {
    options.method = 'POST';
    options.headers = { 'Content-Type': 'application/json', 'X-Import-Review': '1' };
    options.body = JSON.stringify(body);
  }
  const response = await fetch('/import/v1/' + path, options);
  if (response.status === 401) {
    window.location.replace('/import-login/');
    throw new Error('会话已失效，请重新登录');
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.code + '：' + result.error);
  return result;
}

function setActions(meta) {
  node('approve').disabled = !meta || meta.status !== 'needs_review';
  node('reject').disabled = !meta || meta.status !== 'needs_review';
  node('apply').disabled = !meta || meta.status !== 'approved';
  node('save-draft').disabled = !meta || !['needs_review', 'approved'].includes(meta.status);
  node('draft-editor').disabled = !meta || !['needs_review', 'approved'].includes(meta.status);
}

// 选中任务变化时清除旧操作目标，迟到响应不能覆盖新任务详情。
async function showJob(id) {
  selected = id;
  displayed = null;
  setActions(null);
  node('comment').value = '';
  const meta = await request('jobs/' + id);
  const diff = meta.diff;
  if (selected !== id) return;
  displayed = meta;
  node('empty').hidden = true;
  node('detail').hidden = false;
  node('job-id').textContent = meta.jobId;
  node('detail-title').textContent = (meta.type === 'textbook' ? '教材目录' : '文本卷') + ' / ' + meta.subjectPrefix;
  node('job-state').textContent = labels[meta.status] || meta.status;
  node('job-state').dataset.state = meta.status;
  node('metadata').textContent = meta.mode + ' · ' + meta.createdBy + ' · ' + meta.createdAt + '（Asia/Shanghai）';
  node('job-error').textContent = meta.error ? meta.error.code + '：' + meta.error.error : '';
  node('source').textContent = meta.sourcePreview;
  node('diff').textContent = JSON.stringify(diff, null, 2);
  node('draft-editor').value = JSON.stringify(meta.draft, null, 2);
  node('draft-digest').textContent = meta.draftSha256;
  node('reports').textContent = JSON.stringify(meta.reports, null, 2);
  renderAnswers(meta);
  setActions(meta);
  for (const button of node('jobs').querySelectorAll('button')) {
    button.setAttribute('aria-current', String(button.dataset.id === id));
  }
}

async function loadJobs() {
  const status = node('status-filter').value;
  const jobs = await request('jobs?limit=100' + (status ? '&status=' + encodeURIComponent(status) : ''));
  node('jobs').replaceChildren();
  node('job-count').textContent = jobs.length ? '最近 ' + jobs.length + ' 条' : '暂无任务记录';
  for (const job of jobs) {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.id = job.jobId;
    button.setAttribute('aria-current', String(selected === job.jobId));
    const state = document.createElement('span');
    state.className = 'job-state';
    state.textContent = (labels[job.status] || job.status) + ' · ' + job.subjectPrefix;
    const title = document.createElement('span');
    title.className = 'job-key';
    title.textContent = job.jobId;
    button.append(state, title);
    button.addEventListener('click', () => showJob(job.jobId).catch(showError));
    item.append(button);
    node('jobs').append(item);
  }
}

function showError(error) {
  node('message').textContent = error.message;
  node('message').classList.add('error');
}

// 操作绑定已显示且经认证读取的 jobId，失败时保留可核查的服务器错误。
async function decide(action) {
  if (!displayed) return;
  const job = displayed;
  const comment = node('comment').value.trim();
  if (action === 'reject' && !comment) {
    node('comment').focus();
    throw new Error('请填写驳回原因');
  }
  setActions(null);
  const body = action === 'approve' ? { comment, expectedDraftSha256: job.draftSha256 } : action === 'reject' ? { reason: comment } : {};
  try {
    await request('jobs/' + job.jobId + '/' + action, body);
    node('message').classList.remove('error');
    node('message').textContent = '操作已记录：' + job.jobId;
    await loadJobs();
    if (selected === job.jobId) await showJob(job.jobId);
  } catch (error) {
    if (displayed && displayed.jobId === job.jobId) setActions(displayed);
    throw error;
  }
}

// 修订绑定当前显示版本；先保存修订，再重新读取并批准，避免旧页面授权新草稿。
async function saveDraft() {
  if (!displayed) return;
  const job = displayed;
  const draft = JSON.parse(node('draft-editor').value);
  setActions(null);
  try {
    await request('jobs/' + job.jobId + '/draft', {
      draft, expectedDraftSha256: job.draftSha256, comment: node('comment').value.trim()
    });
    node('message').classList.remove('error');
    node('message').textContent = '修订已保存，须重新核对并批准：' + job.jobId;
    await loadJobs();
    if (selected === job.jobId) await showJob(job.jobId);
  } catch (error) {
    if (selected === job.jobId) setActions(displayed);
    throw error;
  }
}
node('save-draft').addEventListener('click', () => saveDraft().catch(showError));

for (const action of ['approve', 'reject', 'apply']) {
  node(action).addEventListener('click', () => decide(action).catch(showError));
}
node('refresh').addEventListener('click', () => loadJobs().then(() => selected && showJob(selected)).catch(showError));
node('status-filter').addEventListener('change', () => loadJobs().catch(showError));
loadJobs().catch(showError);
