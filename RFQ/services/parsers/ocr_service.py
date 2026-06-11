import fitz
import io
from PIL import Image
from paddleocr import PaddleOCR

# 1. Quitamos la variable global de aquí para que Django arranque instantáneamente
# OCR_ENGINE = PaddleOCR(use_angle_cls=True, lang='en')

def extract_text_with_ocr(pdf_path):
    # 2. Inicializamos el motor AQUÍ ADENTRO. 
    # De esta forma, solo el contenedor de Celery sufrirá la espera cuando un usuario suba un archivo.
    ocr_engine = PaddleOCR(use_angle_cls=True, lang='en')
    
    doc = fitz.open(pdf_path)
    full_text = []

    for page in doc:
        pix = page.get_pixmap(dpi=300)
        try:
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='PNG')
            img_bytes = img_byte_arr.getvalue()

            # Usamos el motor local
            result = ocr_engine.ocr(img_bytes)

            page_text = []
            if result:
                for line in result:
                    if line:
                        for item in line:
                            page_text.append(item[1][0])

            full_text.append("\n".join(page_text))
            img_byte_arr.close()
        except Exception as e:
            print(f"OCR Error: {e}")

    return "\n".join(full_text)