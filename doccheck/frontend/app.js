'use strict';

const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[character]);

const stateLabels = { done: 'Проверено', error: 'Ошибка проверки', queued: 'В очереди', running: 'Проверяется' };
const stateClasses = { done: 'ok', error: 'error', queued: 'pending', running: 'pending' };
const navPages = ['new', 'history', 'rules'];
const supportedExtensions = new Set($('file-input').accept.split(','));
let files = [];
let listSignature = '';
let selected = null;
let activeId = null;
let page = 'new';
let uploading = false;
let connection;
let statusRefreshing = false;
let detailRequest = 0;
let detailSignature = '';
let mutation = false;
let toastTimer;
let dragDepth = 0;

function formatSize(bytes) {
  return bytes < 1048576 ? Math.ceil(bytes / 1024) + ' КБ' : (bytes / 1048576).toFixed(1) + ' МБ';
}

function formatDate(value) {
  return new Date(value).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function toast(message) {
  $('toast').textContent = message;
  $('toast').classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.add('hidden'), 7000);
}

async function api(path, options = {}) {
  const response = await fetch('/api' + path, {
    ...options,
    headers: { 'X-Doccheck-Client': '1', ...options.headers }
  });
  if (response.ok) return response.json();

  const error = await response.json().catch(() => ({}));
  throw new Error(typeof error.detail === 'string' ? error.detail : 'Не удалось выполнить запрос. Попробуйте снова.');
}

function showPage(name, focus) {
  page = name;
  document.querySelectorAll('.page').forEach(element => {
    element.classList.toggle('hidden', element.id !== 'page-' + name);
  });
  document.querySelectorAll('.nav-item').forEach(link => {
    const current = link.dataset.page === name || (name === 'detail' && link.dataset.page === 'history');
    link.classList.toggle('active', current);
    if (current) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  if (focus) {
    window.scrollTo(0, 0);
    $('page-' + name).querySelector('h1').focus({ preventScroll: true });
  }
}

function route(focus = false) {
  const hash = location.hash.slice(1);
  if (hash.startsWith('document/')) {
    openDocument(hash.slice(9), focus);
    return;
  }
  activeId = null;
  showPage(navPages.includes(hash) ? hash : 'new', focus);
}

document.querySelectorAll('button[data-page]').forEach(button => {
  button.addEventListener('click', () => { location.hash = button.dataset.page; });
});
window.addEventListener('hashchange', () => route(true));

function setFileError(message = '') {
  $('file-error').textContent = message;
  $('file-error').classList.toggle('hidden', !message);
}

function choose(file) {
  if (!file || uploading) return;
  const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0];
  if (!supportedExtensions.has(extension)) {
    setFileError('Этот формат не поддерживается. Выберите PDF, JPG, JPEG, PNG или WEBP.');
    return;
  }
  if (!file.size || file.size > 15 * 1024 * 1024) {
    setFileError('Выберите непустой файл размером до 15 МБ.');
    return;
  }
  setFileError();
  selected = file;
  $('selected-name').textContent = file.name;
  $('selected-size').textContent = formatSize(file.size);
  $('selected-file').querySelector('.selected-icon').textContent = file.name.split('.').pop().toUpperCase();
  $('selected-file').classList.remove('hidden');
  updateUploadControls();
  ($('check-button').disabled ? $('remove-file') : $('check-button')).focus();
}

function resetFile() {
  selected = null;
  $('file-input').value = '';
  $('selected-file').classList.add('hidden');
  setFileError();
  updateUploadControls();
}

function updateUploadControls() {
  $('check-button').disabled = !selected || uploading || !connection;
  $('check-label').textContent = uploading ? 'Загружаем…' : 'Проверить документ';
  $('remove-file').disabled = uploading;
  document.querySelectorAll('.choose-file').forEach(button => { button.disabled = uploading; });
  let message = selected ? 'Файл готов к проверке' : 'Сначала выберите файл';
  if (uploading) message = 'Отправляем документ на проверку';
  else if (connection === false) message = 'Нет подключения к модели';
  else if (connection === undefined) message = 'Проверяем подключение';
  if ($('action-status').textContent !== message) $('action-status').textContent = message;
}

document.querySelectorAll('.choose-file').forEach(button => {
  button.addEventListener('click', () => $('file-input').click());
});
$('file-input').addEventListener('change', event => {
  const file = event.target.files[0];
  event.target.value = '';
  choose(file);
});
$('remove-file').addEventListener('click', () => {
  resetFile();
  $('dropzone').querySelector('button').focus();
});

$('dropzone').addEventListener('dragenter', event => {
  event.preventDefault();
  dragDepth++;
  $('dropzone').classList.add('dragover');
});
$('dropzone').addEventListener('dragover', event => event.preventDefault());
$('dropzone').addEventListener('dragleave', () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $('dropzone').classList.remove('dragover');
});
$('dropzone').addEventListener('drop', event => {
  event.preventDefault();
  dragDepth = 0;
  $('dropzone').classList.remove('dragover');
  if (event.dataTransfer.files.length > 1) return setFileError('Добавляйте по одному документу.');
  choose(event.dataTransfer.files[0]);
});

$('check-button').addEventListener('click', async () => {
  if (!selected || uploading || !connection) return;
  setFileError();
  uploading = true;
  updateUploadControls();
  try {
    const form = new FormData();
    form.append('file', selected);
    const result = await api('/documents', { method: 'POST', body: form });
    resetFile();
    location.hash = 'document/' + encodeURIComponent(result.id);
    refreshList().catch(() => {});
  } catch (error) {
    setFileError(error.message);
  } finally {
    uploading = false;
    updateUploadControls();
  }
});

async function refreshStatus() {
  if (statusRefreshing) return;
  statusRefreshing = true;
  $('refresh-status').disabled = true;
  $('reconnect').disabled = true;
  let label;
  try {
    const status = await api('/status');
    connection = status.online;
    label = connection ? 'Qwen подключена' : status.message;
    const ocr = status.ocr;
    let ocrLabel = 'OCR не настроен';
    if (ocr?.configured) {
      const languages = ocr.languages.map(lang => ({ rus: 'русский', eng: 'английский' })[lang] || lang);
      ocrLabel = [ocr.engine, ocr.detector ? ocr.device : ocr.pack, languages.join(', ')].filter(Boolean).join(' · ');
    }
    if ($('ocr-label').textContent !== ocrLabel) $('ocr-label').textContent = ocrLabel;
    $('ocr-models').classList.toggle('hidden', !ocr?.configured || !ocr.detector);
    if (ocr?.configured && ocr.detector) {
      if ($('ocr-detector').textContent !== ocr.detector) $('ocr-detector').textContent = ocr.detector;
      if ($('ocr-recognizer').textContent !== ocr.pack) $('ocr-recognizer').textContent = ocr.pack;
    }
    const plans = (status.document_plans || []).map(plan =>
      '<div><dt>' + escapeHtml(plan.label) + '</dt><dd>' + escapeHtml(plan.checks.join(', ')) + '</dd></div>'
    ).join('');
    if ($('document-plans').dataset.plans !== plans) {
      $('document-plans').dataset.plans = plans;
      $('document-plans').innerHTML = plans;
    }
  } catch {
    connection = false;
    label = 'Сайт недоступен';
    $('ocr-label').textContent = 'Статус OCR недоступен';
    $('ocr-models').classList.add('hidden');
  } finally {
    statusRefreshing = false;
    $('refresh-status').disabled = false;
    $('reconnect').disabled = false;
  }
  if ($('connection-label').textContent !== label) $('connection-label').textContent = label;
  $('connection-dot').className = 'dot ' + (connection ? 'online' : 'offline');
  $('offline-banner').classList.toggle('hidden', connection);
  updateUploadControls();
}

$('refresh-status').addEventListener('click', refreshStatus);
$('reconnect').addEventListener('click', refreshStatus);

function listHTML(items, hasQuery) {
  if (!items.length) {
    if (hasQuery) return '<div class="empty-state"><strong>Ничего не найдено</strong><p>Попробуйте другое название файла.</p><button class="text-button clear-search">Очистить поиск</button></div>';
    return '<div class="empty-state"><strong>Пока нет проверок</strong><p>Загрузите документ, чтобы его результат появился здесь.</p></div>';
  }

  return '<div class="doc-list">' + items.map(item => `
    <a class="doc-row" href="#document/${encodeURIComponent(item.id)}">
      <span class="file-icon" aria-hidden="true">${escapeHtml(item.extension.slice(1).toUpperCase())}</span>
      <span class="doc-row-text"><strong>${escapeHtml(item.name)}</strong>
      <small>${formatDate(item.created)} · ${formatSize(item.size)}${item.seconds ? ' · ' + item.seconds + ' сек.' : ''}</small></span>
      <span class="badge ${stateClasses[item.status]}">${stateLabels[item.status]}</span><span class="row-arrow" aria-hidden="true">↗</span>
    </a>
  `).join('') + '</div>';
}

function renderHistory() {
  const query = $('search').value.trim().toLowerCase();
  const matches = files.filter(file => file.name.toLowerCase().includes(query));
  $('history-list').innerHTML = listHTML(matches, Boolean(query));
}

$('search').addEventListener('input', renderHistory);
$('history-list').addEventListener('click', event => {
  if (!event.target.closest('.clear-search')) return;
  $('search').value = '';
  renderHistory();
  $('search').focus();
});

async function refreshList() {
  const items = await api('/documents');
  const signature = JSON.stringify(items);
  if (signature === listSignature) return;
  listSignature = signature;
  files = items;
  $('recent-count').textContent = files.length;
  $('history-count').textContent = files.length;
  $('recent-list').innerHTML = listHTML(files.slice(0, 4), false);
  renderHistory();
}

function reportHTML(item) {
  const report = item.result;
  if (!report) {
    const message = item.error ? 'Повторите проверку, чтобы получить отчёт.' : 'Отчёт появится после проверки.';
    return `<div class="card empty-state"><strong>Отчёт пока недоступен</strong><p>${message}</p></div>`;
  }

  const labels = { ok: 'Найдено', review: 'Проверить', issue: 'Ошибка даты' };
  const endpoint = '/api/documents/' + encodeURIComponent(item.id) + '/report';
  const checks = report.checks.map(check => `
    <div class="check">
      <div class="check-heading"><h3>${escapeHtml(check.label)}</h3><span class="badge ${escapeHtml(check.status)}">${labels[check.status]}</span></div>
      <p class="check-value">${escapeHtml(check.value || 'Не найдено')}</p>
      <p class="check-message">${escapeHtml(check.message)}</p>
      ${check.evidence ? '<blockquote>«' + escapeHtml(check.evidence) + '»</blockquote>' : ''}
    </div>`).join('');
  const contradictions = report.inconsistencies.map(item => `<p>${escapeHtml(item.description)}</p><blockquote>«${escapeHtml(item.evidence)}»</blockquote>`).join('');
  const uncertainties = report.uncertainties.map(value => '<p>' + escapeHtml(value) + '</p>').join('');
  return `
    <div class="report-ready">
      <section class="card report-card">
        <div class="report-heading"><h2>Результат проверки</h2><span class="badge review">Сверьте с оригиналом</span></div>
        <p class="report-subtitle">${escapeHtml(report.model)} · ${escapeHtml(item.seconds)} сек.</p>
        ${report.document_type_label ? '<p class="report-subtitle"><strong>Вид документа</strong> · ' + escapeHtml(report.document_type === 'other' ? 'Прочее или не определён' : report.document_type_label) + '</p>' : ''}
        ${report.ocr ? '<p class="report-subtitle ocr-config"><strong>OCR этого отчёта</strong><br>' + escapeHtml(report.ocr) + '</p>' : ''}
        <div class="download-row"><a class="text-button" href="${endpoint}" download>Скачать отчёт · TXT</a><a class="text-button" href="${endpoint}?format=json" download>Скачать JSON</a></div>
        <div class="score-row"><div><strong>${escapeHtml(report.summary.ok)}</strong>полей найдено</div><div><strong class="amber">${escapeHtml(report.summary.review)}</strong>проверить вручную</div><div><strong class="red">${escapeHtml(report.summary.issue)}</strong>ошибок даты</div></div>
        ${report.document_type_label ? '<h2 class="report-plan">План проверки</h2>' : ''}
        ${checks}
        <p class="report-bottom">${escapeHtml(report.notice)}</p>
      </section>
      <section class="card extra-card"><h2>Возможные противоречия</h2>${contradictions || '<p>Модель не обнаружила явных противоречий.</p>'}${uncertainties ? '<h2>Что не удалось прочитать уверенно</h2>' + uncertainties : ''}</section>
    </div>`;
}

async function openDocument(id, focus) {
  activeId = id;
  detailSignature = '';
  $('document-name').textContent = 'Загружаем документ…';
  $('document-meta').textContent = '';
  $('preview').innerHTML = '';
  $('preview').dataset.key = '';
  $('report').innerHTML = '';
  $('processing').classList.add('hidden');
  $('detail-error').classList.add('hidden');
  $('original-link').classList.add('hidden');
  $('retry-button').disabled = true;
  $('delete-button').disabled = true;
  showPage('detail', focus);
  await refreshDetail();
}

async function refreshDetail() {
  const id = activeId;
  if (!id || page !== 'detail') return;
  const request = ++detailRequest;
  try {
    const item = await api('/documents/' + encodeURIComponent(id));
    if (activeId !== id || request !== detailRequest) return;
    const signature = JSON.stringify(item);
    if (signature === detailSignature) return;
    detailSignature = signature;
    $('document-name').textContent = item.name;
    const mode = { vision: 'Анализ изображения', text: 'Анализ текста' }[item.mode] || stateLabels[item.status];
    $('document-meta').textContent = [formatDate(item.created), formatSize(item.size), mode].join(' · ');
    const pending = item.status === 'queued' || item.status === 'running';
    const supported = supportedExtensions.has(item.extension);
    $('processing').classList.toggle('hidden', !pending);
    $('processing-phase').textContent = item.phase;
    $('retry-button').disabled = !supported || pending || mutation;
    $('delete-button').disabled = pending || mutation;
    $('detail-error').classList.toggle('hidden', !item.error);
    $('detail-error').textContent = item.error || '';

    const url = '/api/documents/' + encodeURIComponent(item.id) + '/file';
    $('original-link').href = url;
    $('original-link').classList.toggle('hidden', !supported || !item.mode);
    const previewKey = item.id + '|' + item.mode + '|' + item.extension;
    if ($('preview').dataset.key !== previewKey) {
      $('preview').dataset.key = previewKey;
      if (!supported) {
        $('preview').innerHTML = '<div class="empty-state"><p>Этот формат больше не поддерживается. Готовый отчёт доступен в истории.</p></div>';
      } else if (!item.mode) {
        $('preview').innerHTML = '<div class="empty-state"><p>Предпросмотр появится после чтения файла.</p></div>';
      } else if (item.extension === '.pdf') {
        $('preview').innerHTML = '<iframe title="Оригинал документа в PDF" src="' + url + '#toolbar=0"></iframe>';
      } else {
        $('preview').innerHTML = '<img alt="Загруженный документ" src="' + url + '">';
      }
    }
    $('report').innerHTML = reportHTML(item);
  } catch (error) {
    if (activeId === id && request === detailRequest) {
      detailSignature = '';
      $('detail-error').textContent = error.message;
      $('detail-error').classList.remove('hidden');
    }
  }
}

$('back-button').addEventListener('click', () => { location.hash = 'history'; });
$('retry-button').addEventListener('click', async () => {
  const id = activeId;
  if (!id || mutation) return;
  mutation = true;
  $('retry-button').disabled = true;
  $('delete-button').disabled = true;
  try {
    await api('/documents/' + encodeURIComponent(id) + '/retry', { method: 'POST' });
    refreshList().catch(() => {});
  } catch (error) {
    toast(error.message);
  } finally {
    mutation = false;
    if (activeId === id) detailSignature = '';
    await refreshDetail();
  }
});

$('delete-button').addEventListener('click', () => $('delete-dialog').showModal());
$('cancel-delete').addEventListener('click', () => $('delete-dialog').close());
$('confirm-delete').addEventListener('click', async () => {
  const id = activeId;
  if (!id || mutation) return;
  mutation = true;
  $('confirm-delete').disabled = true;
  try {
    await api('/documents/' + encodeURIComponent(id), { method: 'DELETE' });
    $('delete-dialog').close();
    if (activeId === id) location.hash = 'history';
    toast('Документ и отчёт удалены.');
    refreshList().catch(() => {});
  } catch (error) {
    toast(error.message);
  } finally {
    mutation = false;
    $('confirm-delete').disabled = false;
  }
});

let ticks = 0;
async function poll() {
  await Promise.allSettled([refreshList(), refreshDetail()]);
  if (++ticks % 8 === 0) await refreshStatus();
  setTimeout(poll, 2500);
}
route();
refreshStatus();
poll();
