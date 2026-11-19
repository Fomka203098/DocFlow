import io
import time

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import app as server, documents
from backend.documents import prepare_document
from backend.engine import DocumentExtraction, Extraction, date_status, make_report


def example():
    def field(value): return {'value': value, 'evidence': value or ''}
    return Extraction.model_validate(dict(recipient=field('ООО Вектор'), applicant=field('Иванов Иван Иванович'), request=field('Прошу принять'), position=field('Специалист'), date=field('31.02.2026'), signature='visible', signature_evidence='росчерк', inconsistencies=[], uncertainties=[]))


@pytest.mark.parametrize('value,status',[('31.02.2026','issue'),('29.02.2024','ok'),('29 февраля 2025 года','issue'),('«01» ноября 2026 г.','ok'),('2026-11-01','ok'),('ноябрь 2026','review')])
def test_dates(value,status):
    assert date_status(value)[0] == status


def test_text_never_confirms_signature():
    report = make_report(example(), {'mode':'text'})
    assert report['checks'][-1]['status'] == 'review'
    assert 'по извлечённому тексту' in report['checks'][-1]['message']
    assert report['summary']['issue'] == 1


def test_report_uses_detected_plan_and_flags_invalid_payment_date():
    extraction = DocumentExtraction.model_validate({
        'document_type': 'invoice',
        'fields': {
            'number': {'value': '012', 'evidence': 'Счёт № 012'},
            'due_date': {'value': '31.02.2026', 'evidence': 'Оплатить до 31.02.2026'},
            'position': {'value': 'инженер', 'evidence': 'инженер'},
        },
    })
    report = make_report(extraction, {'mode': 'vision', 'ocr_text': 'Счёт № 012'})
    checks = {check['key']: check for check in report['checks']}
    assert report['document_type'] == 'invoice'
    assert report['ocr'] == 'Tesseract'
    assert set(checks) == {'number', 'date', 'seller', 'buyer', 'subject', 'amount', 'due_date'}
    assert checks['due_date']['status'] == 'issue'
    assert checks['buyer']['status'] == 'review'


def test_docx_tables_are_read(tmp_path):
    doc = Document()
    doc.add_paragraph('Прошу принять меня на работу на должность специалиста.')
    doc.add_table(rows=1, cols=1).cell(0,0).text = 'Иванов Иван Иванович'
    path = tmp_path / 'a.docx'
    doc.save(path)
    result = prepare_document(path)
    assert 'Иванов' in result['text']
    assert result['mode'] == 'text'


def test_pdf_limits_and_all_pages(tmp_path):
    import fitz
    with fitz.open() as pdf:
        for _ in range(3): pdf.new_page()
        path = tmp_path / 'pages.pdf'
        pdf.save(path)
    assert len(prepare_document(path)['images']) == 3
    with fitz.open() as pdf:
        for _ in range(4): pdf.new_page()
        path = tmp_path / 'too-many.pdf'
        pdf.save(path)
    with pytest.raises(ValueError, match='3 страниц'):
        prepare_document(path)


def test_empty_paddle_page_uses_tesseract(monkeypatch):
    monkeypatch.setattr(documents, 'paddle_ocr_configured', lambda: True)
    monkeypatch.setattr(documents, 'recognize_with_paddle', lambda pages: ['Первая страница', ''])
    monkeypatch.setattr(documents, 'recognize_text', lambda page: f'Текст страницы {page}')
    texts, engines = documents.recognize_pages([(1, 'first'), (2, 'second')])
    assert texts == ['Первая страница', 'Текст страницы 2']
    assert engines == ['PaddleOCR · PP-OCRv5_mobile_det + eslav_PP-OCRv5_mobile_rec · CPU', 'Tesseract']


def test_upload_report_retry_delete_and_security(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'DB', tmp_path / 'test.sqlite3')
    uploads = tmp_path / 'uploads'; uploads.mkdir()
    monkeypatch.setattr(server, 'UPLOADS', uploads)
    monkeypatch.setattr(server, 'extract', lambda doc: example())
    headers = {'X-Doccheck-Client':'1'}
    doc = Document();doc.add_paragraph('Заявление. Прошу принять меня на работу. 31.02.2026')
    buf = io.BytesIO();doc.save(buf)
    with TestClient(server.app) as client:
        assert client.post('/api/documents').status_code == 403
        assert client.get('/api/documents', headers={'Origin':'https://evil.test'}).status_code == 403
        assert client.get('/api/documents', headers={'Host':'evil.test'}).status_code == 403
        assert client.post('/api/documents', headers=headers, files={'file':('bad.exe', b'x')}).status_code == 415
        response = client.post('/api/documents', headers=headers, files={'file':('../../test.docx', buf.getvalue())})
        assert response.status_code == 202
        id = response.json()['id']
        for _ in range(200):
            item = client.get('/api/documents/'+id).json()
            if item['status'] not in {'queued','running'}:break
            time.sleep(.02)
        assert item['status'] == 'done', item
        assert item['name'] == 'test.docx'
        assert item['result']['summary']['issue'] == 1
        assert 'ПРОВЕРКА ЗАЯВЛЕНИЯ' in client.get(f'/api/documents/{id}/report').text
        assert client.get(f'/api/documents/{id}/report?format=json').json()['result']
        assert client.get(f'/api/documents/{id}/file').status_code == 200
        assert client.post(f'/api/documents/{id}/retry',headers=headers).status_code == 202
        for _ in range(200):
            if client.get(f'/api/documents/{id}').json()['status'] == 'done':break
            time.sleep(.02)
        assert client.delete(f'/api/documents/{id}',headers=headers).status_code == 200
        assert client.get(f'/api/documents/{id}').status_code == 404
        assert list(uploads.iterdir()) == []


def test_remote_requires_password_and_valid_origin(tmp_path, monkeypatch):
    import base64, json
    monkeypatch.setattr(server, 'DATA', tmp_path)
    monkeypatch.setattr(server, 'DB', tmp_path / 'remote-test.sqlite3')
    (tmp_path / 'remote.json').write_text(json.dumps({'host':'example-test.trycloudflare.com','password':'long-test-password'}), encoding='utf-8')
    host = {'Host':'example-test.trycloudflare.com'}
    token = base64.b64encode(b'doccheck:long-test-password').decode()
    with TestClient(server.app) as client:
        assert client.get('/api/documents', headers=host).status_code == 401
        assert client.get('/api/documents').status_code == 401
        assert client.get('/api/documents', headers={**host, 'Authorization':'Basic '+token}).status_code == 200
        assert client.get('/api/documents', headers={**host, 'Authorization':'Basic '+token, 'Origin':'https://evil.test'}).status_code == 403
        assert client.get('/api/documents', headers={'Host':'other.trycloudflare.com', 'Authorization':'Basic '+token}).status_code == 403
