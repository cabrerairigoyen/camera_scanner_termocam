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

    # 2. Apply Unsharp Masking to sharpen text characters
    blurred_for_mask = cv2.GaussianBlur(gray, (5, 5), 0)
    sharpened = cv2.addWeighted(gray, 1.6, blurred_for_mask, -0.6, 0)
    
    # 3. Apply CLAHE (Contrast Limited Adaptive Histogram Equalization) to optimize text contrast
    clip_limit = config.get("clahe_clip_limit", 2.5)
    tile_grid = config.get("clahe_grid_size", (8, 8))
    
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid)
    enhanced_gray = clahe.apply(sharpened)
    
    # 4. Mathpix performs best on high-fidelity grayscale or color images.
    # Aggressive binarization (Adaptive Thresholding) destroys anti-aliasing and faint text.
    # We will just return the CLAHE enhanced image, converting back to BGR for standard inputs.
    
    if is_color:
        return cv2.cvtColor(enhanced_gray, cv2.COLOR_GRAY2BGR)
    else:
        return enhanced_gray
