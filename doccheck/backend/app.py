import json
import logging
import os
import sqlite3
import threading
import time
import uuid
import base64
import binascii
import hmac
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .documents import EXTENSIONS, MAX_BYTES, ocr_status, prepare_document
from .engine import DOCUMENT_PLANS, MODEL, extract, make_report, model_status

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('DOCCHECK_DATA_DIR', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True)
UPLOADS = DATA / 'uploads'
UPLOADS.mkdir(exist_ok=True)
DB = DATA / 'history.sqlite3'
executor = ThreadPoolExecutor(max_workers=1)
queue_lock = threading.Lock()
log = logging.getLogger('doccheck')


def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, extension TEXT NOT NULL,
            size INTEGER NOT NULL, created TEXT NOT NULL, status TEXT NOT NULL,
            phase TEXT NOT NULL, error TEXT, extracted_text TEXT, mode TEXT,
            result TEXT, seconds REAL, model TEXT NOT NULL)''')
        c.execute("UPDATE documents SET status='error', phase='Прервано', error='Сервер был перезапущен. Повторите проверку.' WHERE status IN ('queued','running')")


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title='Документ — локальная проверка', docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware('http')
async def local_only(request: Request, call_next):
    host = request.headers.get('host', '')
    remote = None
    remote_file = DATA / 'remote.json'
    try:
        remote = json.loads(remote_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    is_remote = bool(remote and re.fullmatch(r'[a-z0-9-]+\.trycloudflare\.com', host) and hmac.compare_digest(host, remote.get('host', '')))
    if host not in {'127.0.0.1:8000', 'localhost:8000', 'testserver'} and not is_remote:
        return JSONResponse({'detail': 'Доступ разрешён только через localhost:8000.'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin != ('https://' if is_remote else 'http://') + host:
        return JSONResponse({'detail': 'Запрос с другого сайта запрещён.'}, status_code=403)
    # With a tunnel active, protect local Host headers as well: reverse proxies
    # can forward a client-controlled Host. Only the local readiness probe is public.
    if remote and remote.get('public') is not True and (is_remote or request.url.path != '/api/health'):
        valid = False
        try:
            kind, encoded = request.headers.get('authorization', '').split(' ', 1)
            credentials = base64.b64decode(encoded, validate=True).decode('utf-8')
            valid = kind.lower() == 'basic' and hmac.compare_digest(credentials, 'doccheck:' + remote['password'])
        except (ValueError, KeyError, UnicodeError, binascii.Error):
            pass
        if not valid:
            return Response('Требуется пароль доступа.', status_code=401, headers={'WWW-Authenticate': 'Basic realm="Doccheck", charset="UTF-8"', 'Cache-Control':'no-store'})
    if request.method in {'POST', 'DELETE', 'PUT', 'PATCH'}:
        if request.headers.get('x-doccheck-client') != '1':
            return JSONResponse({'detail': 'Откройте приложение и повторите действие.'}, status_code=403)
    if request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > MAX_BYTES + 65536:
        return JSONResponse({'detail': 'Максимальный размер файла — 15 МБ.'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; frame-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    return response


def get_record(doc_id):
    with db() as c:
        row = c.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Документ не найден.')
    item = dict(row)
    item['result'] = json.loads(item['result']) if item['result'] else None
    return item


def update(doc_id, **values):
    with db() as c:
        c.execute('UPDATE documents SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', [*values.values(), doc_id])


def worker(doc_id):
    start = time.monotonic()
    try:
        item = get_record(doc_id)
        update(doc_id, status='running', phase='Читаем документ')
        document = prepare_document(UPLOADS / (doc_id + item['extension']))
        update(doc_id, extracted_text=document['text'], mode=document['mode'], phase='Qwen определяет вид и проверяет документ')
        extraction = extract(document)
        update(doc_id, phase='Проверяем поля и даты')
        report = make_report(extraction, document)
        update(doc_id, result=json.dumps(report, ensure_ascii=False), status='done', phase='Готово', seconds=round(time.monotonic() - start, 1), error=None)
    except Exception as ex:
        log.exception('Check failed: %s', doc_id)
        message = str(ex) if isinstance(ex, ValueError) else 'Не удалось прочитать файл или обработать ответ. Проверьте файл и повторите попытку.'
        update(doc_id, status='error', phase='Не удалось проверить', error=message[:1000], seconds=round(time.monotonic() - start, 1))


@app.get('/api/health')
def health():
    return {'ok': True, 'service': 'doccheck', 'version': '1.0', 'public_mode': os.environ.get('DOCCHECK_PUBLIC') == '1'}


@app.get('/api/status')
def status():
    result = model_status()
    result['ocr'] = ocr_status()
    result['document_plans'] = [
        {'label': label, 'checks': list(fields.values())} for label, fields in DOCUMENT_PLANS.values()
    ]
    with db() as c:
        result['pending'] = c.execute("SELECT count(*) FROM documents WHERE status IN ('queued','running')").fetchone()[0]
    return result


@app.get('/api/documents')
def documents():
    with db() as c:
        rows = c.execute('SELECT id,name,extension,size,created,status,phase,error,seconds FROM documents ORDER BY created DESC LIMIT 200').fetchall()
    return [dict(row) for row in rows]


@app.post('/api/documents', status_code=202)
def upload(file: UploadFile = File(...)):
    name = Path((file.filename or 'документ').replace('\\', '/')).name[:180]
    extension = Path(name).suffix.lower()
    if extension not in EXTENSIONS:
        raise HTTPException(415, 'Поддерживаются JPG, PNG, WEBP, PDF и DOCX. Старый формат DOC нужно сохранить как DOCX.')
    content = file.file.read(MAX_BYTES + 1)
    if not content or len(content) > MAX_BYTES:
        raise HTTPException(413, 'Файл должен быть непустым и не больше 15 МБ.')
    with queue_lock:
        with db() as c:
            count = c.execute("SELECT count(*) FROM documents WHERE status IN ('queued','running')").fetchone()[0]
        if count >= 5:
            raise HTTPException(429, 'В очереди уже 5 документов. Дождитесь завершения проверки.')
        doc_id = uuid.uuid4().hex
        path = UPLOADS / (doc_id + extension)
        path.write_bytes(content)
        try:
            with db() as c:
                c.execute('INSERT INTO documents (id,name,extension,size,created,status,phase,model) VALUES (?,?,?,?,?,?,?,?)',
                          (doc_id, name, extension, len(content), datetime.now(timezone.utc).isoformat(), 'queued', 'В очереди', MODEL))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        executor.submit(worker, doc_id)
    return {'id': doc_id}


@app.get('/api/documents/{doc_id}')
def document(doc_id: str):
    return get_record(doc_id)


@app.get('/api/documents/{doc_id}/file')
def original(doc_id: str):
    item = get_record(doc_id)
    # Never serve unvalidated uploads inline.
    if not item['mode']:
        raise HTTPException(409, 'Документ ещё не прочитан или имеет неподдерживаемое содержимое.')
    types = {'.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
    return FileResponse(UPLOADS / (doc_id + item['extension']), media_type=types[item['extension']], filename=item['name'], content_disposition_type='attachment' if item['extension'] == '.docx' else 'inline')


@app.post('/api/documents/{doc_id}/retry', status_code=202)
def retry(doc_id: str):
    with queue_lock:
        item = get_record(doc_id)
        if item['status'] not in {'done', 'error'}:
            raise HTTPException(409, 'Этот документ уже проверяется.')
        with db() as c:
            count = c.execute("SELECT count(*) FROM documents WHERE status IN ('queued','running')").fetchone()[0]
        if count >= 5:
            raise HTTPException(429, 'Очередь заполнена. Дождитесь завершения проверки.')
        update(doc_id, status='queued', phase='В очереди', error=None, result=None, seconds=None)
        executor.submit(worker, doc_id)
    return {'id': doc_id}


@app.delete('/api/documents/{doc_id}')
def delete(doc_id: str):
    with queue_lock:
        item = get_record(doc_id)
        if item['status'] in {'running', 'queued'}:
            raise HTTPException(409, 'Дождитесь завершения проверки перед удалением.')
        (UPLOADS / (doc_id + item['extension'])).unlink(missing_ok=True)
        with db() as c:
            c.execute('DELETE FROM documents WHERE id=?', (doc_id,))
    return {'ok': True}


@app.get('/api/documents/{doc_id}/report')
def download_report(doc_id: str, format: str = 'txt'):
    item = get_record(doc_id)
    report = item['result']
    if not report:
        raise HTTPException(409, 'Отчёт ещё не готов.')
    if format == 'json':
        data = json.dumps(item, ensure_ascii=False, indent=2)
        mime, extension = 'application/json', 'json'
    else:
        kind = report.get('document_type', 'employment')
        heading = 'ПРОВЕРКА ЗАЯВЛЕНИЯ' if kind in {'employment', 'application'} else 'ПРОВЕРКА ДОКУМЕНТА'
        lines = [heading, item['name'], 'Модель: ' + report['model']]
        if report.get('document_type_label'):
            lines.append('План проверки: ' + report['document_type_label'])
        if report.get('ocr'):
            lines.append('OCR: ' + report['ocr'])
        lines.append('')
        for check in report['checks']:
            lines.extend([check['label'] + ': ' + (check['value'] or 'Не найдено'), check['message'], 'Цитата: ' + (check['evidence'] or '—'), ''])
        lines.append('ВОЗМОЖНЫЕ ПРОТИВОРЕЧИЯ')
        lines += [x['description'] + '\nЦитата: ' + x['evidence'] for x in report['inconsistencies']] or ['Не обнаружены.']
        lines += ['', 'ТРЕБУЕТ УТОЧНЕНИЯ', *report['uncertainties'], '', report['notice']]
        data, mime, extension = '\n'.join(lines), 'text/plain; charset=utf-8', 'txt'
    return Response(data.encode('utf-8'), media_type=mime, headers={'Content-Disposition': f'attachment; filename="report-{doc_id[:8]}.{extension}"'})


app.mount('/', StaticFiles(directory=ROOT / 'frontend', html=True), name='frontend')
