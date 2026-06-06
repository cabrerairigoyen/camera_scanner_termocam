import cv2
import numpy as np

def enhance_for_ocr(image: np.ndarray, config: dict = None) -> np.ndarray:
    """
    Lightweight image enhancement targeting OCR optimization.
    Applies CLAHE contrast enhancement and unsharp masking.
    
    This function acts as a clean hook for future deep learning restoration 
    models (e.g., Restormer, SRN-Deblur, Real-ESRGAN, etc.) without altering
    the core pipeline.
    
    Args:
        image (np.ndarray): Input BGR image.
        config (dict): Optional dictionary for parameters.
        
    Returns:
        np.ndarray: Enhanced BGR image.
    """
    if image is None:
        return None
        
    if config is None:
        config = {}
        
    # Check if gray or color
    is_color = len(image.shape) == 3
    
    # 1. Convert to Grayscale/YUV for contrast adjustment
    if is_color:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    # 2. Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
    clip_limit = config.get("clahe_clip_limit", 2.5)
    tile_grid = config.get("clahe_grid_size", (8, 8))
    
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid)
    enhanced_gray = clahe.apply(gray)
    
    # 3. Apply Lightweight Unsharp Masking (Sharpening)
    # Formula: sharpened = original + (original - blurred) * amount
    sigma = config.get("sharpen_sigma", 1.5)
    amount = config.get("sharpen_amount", 0.5)
    
    blurred = cv2.GaussianBlur(enhanced_gray, (0, 0), sigma)
    sharpened_gray = cv2.addWeighted(enhanced_gray, 1.0 + amount, blurred, -amount, 0)
    
    # 4. Return as BGR if input was color, otherwise return grayscale
    if is_color:
        # We can apply the contrast adjustment to the Y channel of YUV
        # to preserve colors or return grayscale. Since OCR normalized copies
        # are typically grayscale, returning a BGR image with equalized intensity is standard:
        return cv2.cvtColor(sharpened_gray, cv2.COLOR_GRAY2BGR)
    else:
        return sharpened_gray
