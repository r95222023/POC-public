// SPDX-License-Identifier: GPL-3.0-only
const form = document.getElementById('scene-form');
const input = document.getElementById('files');
const dropzone = document.getElementById('dropzone');
const formStatus = document.getElementById('form-status');
const submit = document.getElementById('submit');
const panel = document.getElementById('job-panel');
const historyList = document.getElementById('history');
const viewer = document.getElementById('viewer');
const VIDEO = /\.(mp4|mov|m4v|webm|mkv|avi|mts|m2ts)$/i;
const ACTIVE = new Set(['queued', 'running']);
const STATUS = {queued: '排隊中', running: '處理中', completed: '完成', failed: '失敗', cancelled: '已取消', interrupted: '已中斷'};
let selected = null;
let pollTimer = null;
let busy = false;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function bytes(value) {
  if (value > 1e9) return (value / 1e9).toFixed(2) + ' GB';
  if (value > 1e6) return (value / 1e6).toFixed(1) + ' MB';
  return Math.max(1, Math.round(value / 1e3)) + ' KB';
}
function duration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return m ? `${m} 分 ${String(s).padStart(2, '0')} 秒` : `${s} 秒`;
}
function localTime(iso) {
  return iso ? new Date(iso).toLocaleString('zh-TW', {hour12: false}) : '';
}
function setFormStatus(text, error) {
  formStatus.textContent = text || '';
  formStatus.classList.toggle('error', Boolean(error));
}

// ---- choosing files -------------------------------------------------------
function describeSelection() {
  const files = Array.from(input.files);
  const isVideo = files.length === 1 && VIDEO.test(files[0].name);
  document.querySelectorAll('.video-only').forEach(node => { node.hidden = files.length > 0 && !isVideo; });
  const title = document.getElementById('drop-title');
  const detail = document.getElementById('drop-detail');
  if (!files.length) {
    title.textContent = '選擇影片或照片，或拖放至此';
    detail.textContent = 'MP4／MOV／WebM 影片，或 JPG／PNG 照片；上限 4 GiB';
    return;
  }
  const total = files.reduce((sum, file) => sum + file.size, 0);
  title.textContent = isVideo ? `影片：${files[0].name}` : `${files.length} 個檔案`;
  detail.textContent = isVideo ? `${bytes(total)} · 將自動挑選清晰且分布平均的影格`
    : `${bytes(total)} · ${files.slice(0, 3).map(f => f.name).join('、')}${files.length > 3 ? ' …' : ''}`;
}
input.addEventListener('change', () => { describeSelection(); setFormStatus(''); });
['dragenter', 'dragover'].forEach(type => dropzone.addEventListener(type, () => dropzone.classList.add('dragging')));
['dragleave', 'drop'].forEach(type => dropzone.addEventListener(type, () => dropzone.classList.remove('dragging')));

// ---- creating jobs --------------------------------------------------------
function send(data, uploading) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', '/api/scenes');
    request.responseType = 'json';
    if (uploading) {
      request.upload.addEventListener('progress', event => {
        if (event.lengthComputable) setFormStatus(`上傳中… ${Math.round(event.loaded / event.total * 100)}%`);
      });
    }
    request.addEventListener('load', () => {
      const body = request.response || {};
      if (request.status >= 200 && request.status < 300) resolve(body.job);
      else reject(new Error(body.error || `伺服器回應 ${request.status}`));
    });
    request.addEventListener('error', () => reject(new Error('無法連線到本機伺服器，請確認 run.cmd 仍在執行。')));
    request.send(data);
  });
}
async function start(data, uploading) {
  if (busy) return;
  busy = true;
  submit.disabled = true;
  document.querySelectorAll('[data-sample]').forEach(button => { button.disabled = true; });
  try {
    const job = await send(data, uploading);
    setFormStatus('已加入佇列，處理進度顯示在下方。');
    form.reset();
    describeSelection();
    select(job.id, true);
  } catch (error) {
    setFormStatus(error.message, true);
  } finally {
    busy = false;
    submit.disabled = false;
    document.querySelectorAll('[data-sample]').forEach(button => { button.disabled = false; });
  }
}
form.addEventListener('submit', event => {
  event.preventDefault();
  if (!input.files.length) { setFormStatus('請先選擇影片或照片。', true); return; }
  start(new FormData(form), true);
});
document.querySelectorAll('[data-sample]').forEach(button => button.addEventListener('click', () => {
  const data = new FormData();
  data.append('sample', button.dataset.sample);
  data.append('preset', document.getElementById('preset').value);
  setFormStatus('正在建立工作…');
  start(data, false);
}));

// ---- showing a job --------------------------------------------------------
function renderStages(job) {
  const list = document.getElementById('stages');
  list.replaceChildren();
  const stages = job.stage_list;
  const currentIndex = stages.findIndex(stage => stage.key === job.stage);
  stages.forEach((stage, index) => {
    const item = el('li', stage.label);
    const finished = job.status === 'completed' || (currentIndex >= 0 && index < currentIndex);
    const current = index === currentIndex && job.status === 'running';
    if (finished) item.classList.add('done');
    if (current) item.classList.add('current');
    if (index === currentIndex && ['failed', 'cancelled', 'interrupted'].includes(job.status)) item.classList.add('stopped');
    if (stage.started_utc) {
      const next = stages.slice(index + 1).find(s => s.started_utc);
      const end = next ? Date.parse(next.started_utc) : job.finished_utc ? Date.parse(job.finished_utc) : Date.now();
      if (finished || current) item.append(el('small', duration((end - Date.parse(stage.started_utc)) / 1000)));
    }
    list.append(item);
  });
}
function renderJob(job, log) {
  panel.hidden = false;
  document.getElementById('job-kind').textContent = job.kind === 'video' ? 'VIDEO → 3D' : 'PHOTOS → 3D';
  document.getElementById('job-title').textContent = job.label;
  const meta = [`建立於 ${localTime(job.created_utc)}`, job.preset_label];
  if (job.frames) meta.push(job.kind === 'video' ? `自動切出 ${job.frames} 張影格` : `${job.frames} 張照片`);
  if (job.registered) meta.push(`${job.registered[0]}/${job.registered[1]} 張定位成功`);
  if (job.mesh) meta.push(`${job.mesh.faces.toLocaleString()} 個三角面`);
  if (job.elapsed_seconds) meta.push(`共 ${duration(job.elapsed_seconds)}`);
  else if (job.started_utc && job.status === 'running') meta.push(`已執行 ${duration((Date.now() - Date.parse(job.started_utc)) / 1000)}`);
  if (job.status === 'queued' && job.queue_position > 1) meta.push(`前面還有 ${job.queue_position - 1} 個工作`);
  document.getElementById('job-meta').textContent = meta.join(' · ');
  const badge = document.getElementById('job-badge');
  badge.textContent = job.status === 'running' ? `處理中：${job.stage_label}` : STATUS[job.status] || job.status;
  badge.className = 'badge ' + job.status;
  document.getElementById('cancel').hidden = !ACTIVE.has(job.status);
  renderStages(job);
  const error = document.getElementById('job-error');
  error.hidden = !job.error;
  error.textContent = job.error || '';
  const warnings = document.getElementById('job-warnings');
  warnings.replaceChildren(...(job.warnings || []).map(text => el('li', text)));
  warnings.hidden = !(job.warnings || []).length;
  const wrap = document.getElementById('viewer-wrap');
  const done = job.status === 'completed' && job.result;
  wrap.hidden = !done;
  if (done) {
    const base = `/scenes/${job.id}/view/`;
    const target = base + job.result.viewer + '?embed=1';
    if (viewer.dataset.src !== target) { viewer.src = target; viewer.dataset.src = target; }
    document.getElementById('open-full').href = base + job.result.viewer;
    const glb = document.getElementById('download-glb');
    glb.href = base + job.result.model;
    glb.textContent = `下載貼圖模型 GLB（${bytes(job.result.model_bytes)}）`;
    document.getElementById('download-ply').href = base + job.result.points;
    document.getElementById('open-sparse').href = base + job.result.sparse_preview;
  } else if (viewer.dataset.src) {
    viewer.removeAttribute('src');
    delete viewer.dataset.src;
  }
  document.getElementById('log').textContent = (log || []).join('\n') || '尚無輸出。';
}
async function refresh() {
  clearTimeout(pollTimer);
  if (!selected) return;
  try {
    const response = await fetch(`/api/scenes/${selected}`, {cache: 'no-store'});
    if (response.status === 404) {
      selected = null;
      panel.hidden = true;
      window.history.replaceState(null, '', location.pathname);
      return;
    }
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '無法讀取工作');
    renderJob(body.job, body.log);
    loadHistory();
    if (ACTIVE.has(body.job.status)) pollTimer = setTimeout(refresh, 2000);
  } catch (error) {
    panel.hidden = false;
    document.getElementById('job-error').hidden = false;
    document.getElementById('job-error').textContent = error.message;
    pollTimer = setTimeout(refresh, 5000);
  }
}
function select(id, scroll) {
  selected = id;
  historyList.querySelectorAll('li[data-id]').forEach(item => item.classList.toggle('selected', item.dataset.id === id));
  if (location.hash !== '#job=' + id) window.history.replaceState(null, '', '#job=' + id);
  refresh().then(() => { loadHistory(); if (scroll) panel.scrollIntoView({behavior: 'smooth', block: 'start'}); });
}
document.getElementById('cancel').addEventListener('click', async () => {
  if (!selected || !confirm('確定要取消這個重建工作？')) return;
  await fetch(`/api/scenes/${selected}/cancel`, {method: 'POST'});
  refresh();
});

// ---- history --------------------------------------------------------------
async function loadHistory() {
  let jobs = [];
  try {
    jobs = (await (await fetch('/api/scenes', {cache: 'no-store'})).json()).jobs || [];
  } catch (error) {
    return [];
  }
  historyList.replaceChildren();
  if (!jobs.length) historyList.append(el('li', '尚無紀錄。', 'muted'));
  for (const job of jobs) {
    const item = el('li');
    item.dataset.id = job.id;
    if (job.id === selected) item.classList.add('selected');
    const open = el('button', job.label, 'open');
    open.type = 'button';
    open.addEventListener('click', () => select(job.id, true));
    const badge = el('span', job.status === 'running' ? job.stage_label : STATUS[job.status] || job.status, 'badge ' + job.status);
    item.append(open, badge, el('small', localTime(job.created_utc)));
    if (!ACTIVE.has(job.status)) {
      const remove = el('button', '刪除', 'link');
      remove.type = 'button';
      remove.addEventListener('click', async () => {
        if (!confirm(`刪除「${job.label}」的輸入與重建結果？此操作無法復原。`)) return;
        const response = await fetch(`/api/scenes/${job.id}`, {method: 'DELETE'});
        if (response.ok && selected === job.id) { selected = null; panel.hidden = true; window.history.replaceState(null, '', location.pathname); }
        loadHistory();
      });
      item.append(remove);
    }
    historyList.append(item);
  }
  return jobs;
}

loadHistory().then(jobs => {
  const fromHash = (location.hash.match(/^#job=([\w-]+)$/) || [])[1];
  const running = jobs.find(job => ACTIVE.has(job.status));
  if (fromHash) select(fromHash, false);
  else if (running) select(running.id, false);
});
