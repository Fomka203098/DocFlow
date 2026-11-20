# Технический стек

| Уровень | Технологии |
|---|---|
| Интерфейс | HTML, CSS, JavaScript |
| Сервер | Python 3.12, FastAPI, Uvicorn |
| Хранение | SQLite и локальная файловая система |
| Подготовка документов | PyMuPDF и Pillow |
| OCR | PaddleOCR PP-OCRv5 на CPU, Tesseract как резерв |
| Извлечение полей | Локальная Qwen 3.5 4B через LM Studio, HTTPX и схема Pydantic |
| Тесты | pytest, FastAPI TestClient и синтетический датасет |

Описание компонентов и потоков данных есть в [архитектуре DocFlow](docs/architecture.md).
