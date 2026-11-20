import base64
import io
import json
import logging
import os
import subprocess
import warnings
from pathlib import Path

import fitz
from PIL import Image, ImageOps

from .paddle_ocr_worker import DETECTOR, RECOGNIZER, MODELS, MODEL_FILES

MAX_BYTES = 15 * 1024 * 1024
MAX_TEXT = 11000
MAX_PAGES = 3
FILE_TYPES = {
    '.pdf': 'application/pdf',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
}
SUPPORTED_FORMATS = 'Поддерживаются PDF, JPG, JPEG, PNG и WEBP.'
Image.MAX_IMAGE_PIXELS = 24_000_000
TOOLS = Path(__file__).resolve().parents[1] / 'tools'
BEST_TESSDATA = TOOLS / 'tessdata-best'
FAST_TESSDATA = TOOLS / 'tessdata'
PADDLE_ENV = TOOLS / 'paddleocr-env'
PADDLE_PYTHON = PADDLE_ENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
log = logging.getLogger(__name__)


def prepare_image(image):
    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((1600, 1600))
    return image


def image_url(image):
    buf = io.BytesIO()
    image.save(buf, format='JPEG', quality=90)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def ocr_config():
    override = os.environ.get('TESSDATA_PREFIX')
    if override:
        tessdata = Path(override).resolve()
    elif all((BEST_TESSDATA / f'{lang}.traineddata').is_file() for lang in ('rus', 'eng')):
        tessdata = BEST_TESSDATA
    else:
        tessdata = FAST_TESSDATA
    languages = [lang for lang in ('rus', 'eng') if (tessdata / f'{lang}.traineddata').is_file()]
    return tessdata, languages


def ocr_status():
    if paddle_ocr_configured():
        return {'engine': 'PaddleOCR', 'pack': RECOGNIZER, 'detector': DETECTOR, 'device': 'CPU',
                'languages': ['rus', 'eng'], 'configured': True}
    tessdata, languages = ocr_config()
    pack = {BEST_TESSDATA: 'tessdata_best', FAST_TESSDATA: 'tessdata_fast'}.get(tessdata)
    return {'engine': 'Tesseract', 'pack': pack, 'languages': languages, 'configured': 'rus' in languages}


def paddle_ocr_configured():
    packages = 'Lib/site-packages' if os.name == 'nt' else 'lib/python*/site-packages'
    return (
        PADDLE_PYTHON.is_file()
        and any(PADDLE_ENV.glob(f'{packages}/paddleocr/__init__.py'))
        and all((MODELS / model / filename).is_file()
                for model in (DETECTOR, RECOGNIZER) for filename in MODEL_FILES)
    )


def recognize_with_paddle(images):
    result = subprocess.run(
        [str(PADDLE_PYTHON), '-X', 'utf8', '-m', 'backend.paddle_ocr_worker'],
        cwd=TOOLS.parent,
        input=json.dumps([url.partition(',')[2] for url in images]),
        capture_output=True,
        encoding='utf-8',
        errors='replace',
        timeout=90,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    texts = json.loads(result.stdout)
    if not isinstance(texts, list) or len(texts) != len(images) or not all(isinstance(text, str) for text in texts):
        raise ValueError('PaddleOCR returned an invalid page list')
    return texts


def recognize_text(image):
    tessdata, languages = ocr_config()
    if 'rus' not in languages:
        return ''
    language = '+'.join(languages)
    # The Windows Tesseract build can open relative paths inside a Cyrillic directory.
    path = str(tessdata.relative_to(Path.cwd())) if tessdata.is_relative_to(Path.cwd()) else str(tessdata)
    try:
        pixmap = fitz.Pixmap(fitz.csRGB, image.width, image.height, image.tobytes(), False)
        pdf = pixmap.pdfocr_tobytes(language=language, tessdata=path)
        with fitz.open(stream=pdf, filetype='pdf') as document:
            return document[0].get_text().strip()
    except (fitz.mupdf.FzErrorBase, RuntimeError, ValueError) as error:
        log.warning('OCR недоступен для страницы: %s', error)
        return ''


def recognize_pages(pages):
    if not pages:
        return [], []
    texts = [''] * len(pages)
    engines = []
    if paddle_ocr_configured():
        try:
            texts = recognize_with_paddle([url for _, url in pages])
            if any(text.strip() for text in texts):
                engines.append(f'PaddleOCR · {DETECTOR} + {RECOGNIZER} · CPU')
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            log.warning('PaddleOCR недоступен (%s). Используем Tesseract.', type(error).__name__)
    for index, (image, _) in enumerate(pages):
        if not texts[index].strip():
            texts[index] = recognize_text(image)
            if texts[index] and 'Tesseract' not in engines:
                engines.append('Tesseract')
    return texts, engines


def prepare_document(path: Path):
    ext = path.suffix.lower()
    if ext not in FILE_TYPES:
        raise ValueError(SUPPORTED_FORMATS)
    if not path.stat().st_size or path.stat().st_size > MAX_BYTES:
        raise ValueError('Файл должен быть непустым и не больше 15 МБ.')
    images, text, scan_pages, scan_numbers = [], '', [], []
    if ext == '.pdf':
        with fitz.open(path) as pdf:
            if pdf.needs_pass:
                raise ValueError('PDF защищён паролем. Загрузите копию без пароля.')
            if not 1 <= len(pdf) <= MAX_PAGES:
                raise ValueError('Для одной проверки загрузите PDF от 1 до 3 страниц.')
            # Send every page visually, including mixed text/scan PDFs. Never silently omit pages.
            page_texts = [page.get_text() for page in pdf]
            text = '\n\n'.join(page_texts).strip()
            for page in pdf:
                scale = min(2.0, 1600 / max(page.rect.width, page.rect.height))
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                image = prepare_image(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
                url = image_url(image)
                images.append(url)
                if not page_texts[page.number].strip():
                    scan_pages.append((image, url))
                    scan_numbers.append(page.number + 1)
    else:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as img:
                img.load()
                image = prepare_image(img)
                images.append(image_url(image))
                scan_pages.append((image, images[-1]))
    if len(text) > MAX_TEXT:
        raise ValueError('В документе больше 11 000 символов. Разделите его на отдельные заявления.')
    ocr_pages, engines = recognize_pages(scan_pages)
    if scan_numbers:
        ocr_pages = [f'Страница {number}\n{recognized}' for number, recognized in zip(scan_numbers, ocr_pages)
                     if recognized.strip()]
    ocr_text = '\n\n'.join(ocr_pages)[:MAX_TEXT - len(text)].strip()
    return {'text': text, 'images': images, 'mode': 'vision', 'ocr_text': ocr_text,
            'ocr_engine': ' + '.join(engines) if ocr_text else None}
