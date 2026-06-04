import fitz
import io
from PIL import Image

def extract_text_with_ocr(pdf_path):
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(
        use_angle_cls=True,
        lang='en'
    )
    
    doc = fitz.open(pdf_path)
    full_text = []

    for page in doc:
        pix = page.get_pixmap(dpi=300)
        
        try:
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            img_byte_arr = io.BytesIO()
            
            img.save(img_byte_arr, format='PNG')
            
            img_bytes = img_byte_arr.getvalue()

            result = ocr.ocr(img_bytes)
            page_text = []

            if result:  
                for line in result:
                    if line:
                        for item in line:
                            detected_text = item[1][0]
                            page_text.append(detected_text)

            full_text.append("\n".join(page_text))
            
            img_byte_arr.close()
            
        except Exception as e:
            print(f"Error controlado en procesamiento de OCR: {str(e)}")
            pass

    return "\n".join(full_text)