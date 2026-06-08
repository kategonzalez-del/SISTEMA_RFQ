import fitz
import io
from PIL import Image
import pytesseract

def extract_text_with_ocr(pdf_path):
    doc = fitz.open(pdf_path)
    full_text = []

    for page in doc:
        pix = page.get_pixmap(dpi=300)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        try:
            text = pytesseract.image_to_string(img, lang='eng')
            full_text.append(text)
        except Exception as e:
            print(f"Error OCR página: {str(e)}")

    return "\n".join(full_text)
