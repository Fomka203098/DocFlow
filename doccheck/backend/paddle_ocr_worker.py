"""OCR subprocess entry point. Its Python environment lives in tools/paddleocr-env."""

import base64
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path


DETECTOR = 'PP-OCRv5_mobile_det'
RECOGNIZER = 'eslav_PP-OCRv5_mobile_rec'
MODELS = Path(__file__).resolve().parents[1] / 'tools' / 'paddleocr-models'
MODEL_FILES = ('inference.json', 'inference.pdiparams', 'inference.yml')


def main():
    images = json.load(sys.stdin)
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    os.environ['PADDLE_PDX_CACHE_HOME'] = str(MODELS / '.cache')
    os.environ.setdefault('OMP_NUM_THREADS', '1')
    # The OCR libraries print startup messages. Keep stdout for the JSON reply.
    with redirect_stdout(sys.stderr):
        import cv2
        import numpy as np
        from paddleocr import PaddleOCR

        model_paths = MODELS.relative_to(Path.cwd())
        model = PaddleOCR(
            text_detection_model_name=DETECTOR,
            text_detection_model_dir=str(model_paths / DETECTOR),
            text_recognition_model_name=RECOGNIZER,
            text_recognition_model_dir=str(model_paths / RECOGNIZER),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device='cpu',
            enable_mkldnn=False,
            cpu_threads=4,
            text_det_limit_side_len=1152,
            text_det_limit_type='max',
        )
        pages = [cv2.imdecode(np.frombuffer(base64.b64decode(image), dtype=np.uint8), cv2.IMREAD_COLOR)
                 for image in images]
        texts = ['\n'.join(result['rec_texts']) for result in model.predict_iter(pages)]
    json.dump(texts, sys.stdout, ensure_ascii=True)


if __name__ == '__main__':
    main()
