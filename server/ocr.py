import os
import cv2
import numpy as np

# Safety imports for OCR engines
try:
    from paddleocr import PaddleOCR
    PADDLE_AVAILABLE = True
except ImportError:
    PADDLE_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False


def run_ocr(image_path: str) -> dict:
    """
    Runs OCR on the given image.
    Prefers PaddleOCR, falls back to Tesseract.
    If neither is available, returns a descriptive error structure.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"OCR Error: Image not found at {image_path}")

    # Load image for dimension checking
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"OCR Error: Failed to decode image at {image_path}")
    h, w = img.shape[:2]

    if PADDLE_AVAILABLE:
        try:
            print("OCR: Running PaddleOCR...")
            return run_paddle_ocr(image_path)
        except Exception as e:
            print(f"OCR: PaddleOCR failed with error: {e}. Falling back to Tesseract...")

    if TESSERACT_AVAILABLE:
        try:
            print("OCR: Running Tesseract OCR...")
            return run_tesseract_ocr(img)
        except Exception as e:
            print(f"OCR: Tesseract failed with error: {e}.")

    # Fallback when no OCR tools are installed on dev system
    print("OCR: No OCR engines (PaddleOCR or Tesseract) are available. Returning mock results.")
    return {
        "text": "WARNING: No OCR engines (PaddleOCR/Tesseract) were detected on the reconstruction server. Showing mock text output.",
        "lines": [
            {
                "text": "WARNING: OCR engines not detected.",
                "confidence": 0.0,
                "bbox": [[10, 10], [w - 10, 10], [w - 10, 50], [10, 50]]
            }
        ],
        "fields": {},
        "engine": "None (Mock)"
    }


def run_paddle_ocr(image_path: str) -> dict:
    """Executes PaddleOCR and converts output to the JSON contract."""
    # Initialize PaddleOCR (downloads models if not cached)
    ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
    
    # Run OCR
    results = ocr.ocr(image_path, cls=True)
    
    text_blocks = []
    lines = []
    
    if results and len(results) > 0 and results[0] is not None:
        for block in results[0]:
            bbox = block[0] # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            text, conf = block[1]
            
            lines.append({
                "text": text,
                "confidence": float(conf),
                "bbox": [[int(pt[0]), int(pt[1])] for pt in bbox]
            })
            text_blocks.append(text)
            
    return {
        "text": "\n".join(text_blocks),
        "lines": lines,
        "fields": {},
        "engine": "PaddleOCR"
    }


def run_tesseract_ocr(img: np.ndarray) -> dict:
    """Executes Tesseract image_to_data and aggregates word coordinates to line contract."""
    # Query details as dictionary
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    
    # Tesseract yields results word-by-word. We group words into lines using 
    # (block_num, paragraph_num, line_num) groupings
    grouped_lines = {}
    
    n_boxes = len(data['text'])
    for i in range(n_boxes):
        # Filter empty words and low confidence boxes
        conf = float(data['conf'][i])
        text = data['text'][i].strip()
        
        if conf < 0 or not text:
            continue
            
        block_id = data['block_num'][i]
        par_id = data['par_num'][i]
        line_id = data['line_num'][i]
        
        key = (block_id, par_id, line_id)
        if key not in grouped_lines:
            grouped_lines[key] = []
            
        grouped_lines[key].append({
            "text": text,
            "conf": conf,
            "x": data['left'][i],
            "y": data['top'][i],
            "w": data['width'][i],
            "h": data['height'][i]
        })
        
    lines = []
    full_text_list = []
    
    for key, words in sorted(grouped_lines.items()):
        # Sort words in line by X coordinate
        words.sort(key=lambda w: w['x'])
        
        # Combine texts and average confidence
        line_text = " ".join([w['text'] for w in words])
        avg_conf = sum([w['conf'] for w in words]) / len(words) / 100.0
        
        # Bounding box of the entire line (encompassing all words)
        min_x = min([w['x'] for w in words])
        min_y = min([w['y'] for w in words])
        max_x = max([w['x'] + w['w'] for w in words])
        max_y = max([w['y'] + w['h'] for w in words])
        
        bbox = [
            [min_x, min_y],
            [max_x, min_y],
            [max_x, max_y],
            [min_x, max_y]
        ]
        
        lines.append({
            "text": line_text,
            "confidence": round(avg_conf, 3),
            "bbox": bbox
        })
        full_text_list.append(line_text)
        
    return {
        "text": "\n".join(full_text_list),
        "lines": lines,
        "fields": {},
        "engine": "Tesseract"
    }
