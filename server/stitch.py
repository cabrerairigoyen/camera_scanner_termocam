import cv2
import numpy as np

def stitch_images(images: list) -> tuple:
    """
    Stitches a list of images together.
    Attempts methods in sequence:
    1. cv2.Stitcher_SCANS
    2. cv2.Stitcher_PANORAMA
    3. Custom feature matching (SIFT/ORB) homography pipeline
    
    Args:
        images (list of np.ndarray): List of input frames (BGR).
        
    Returns:
        (status_string, stitched_image, opencv_status_code, method_used)
    """
    if not images or len(images) == 0:
        return "error", None, -1, "none"
    if len(images) == 1:
        return "success", images[0], 0, "single_frame"
        
    print(f"Stitcher: Attempting to stitch {len(images)} frames...")
    
    # 1. Attempt Stitcher_SCANS
    print("Stitcher: Trying cv2.Stitcher_SCANS...")
    stitcher = cv2.Stitcher_create(cv2.Stitcher_SCANS)
    status, result = stitcher.stitch(images)
    if status == cv2.Stitcher_OK:
        print("Stitcher: Success using SCANS.")
        return "success", result, status, "SCANS"
        
    # 2. Attempt Stitcher_PANORAMA
    print(f"Stitcher: SCANS failed (status code: {status}). Trying cv2.Stitcher_PANORAMA...")
    stitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    status, result = stitcher.stitch(images)
    if status == cv2.Stitcher_OK:
        print("Stitcher: Success using PANORAMA.")
        return "success", result, status, "PANORAMA"
        
    # 3. Custom Feature Matching fallback
    print(f"Stitcher: PANORAMA failed (status code: {status}). Running Custom Feature pipeline...")
    try:
        custom_result = stitch_custom_sequential(images)
        if custom_result is not None:
            print("Stitcher: Success using CUSTOM_FEATURE pipeline.")
            return "success", custom_result, 0, "CUSTOM_FEATURE"
    except Exception as e:
        print(f"Stitcher: Custom feature matching crashed: {e}")
        
    return "failed", None, status, "failed"


def stitch_custom_sequential(images: list) -> np.ndarray:
    """
    Sequentially stitches images using pairwise homography alignment.
    Starts with images[0] and recursively stitches subsequent frames.
    """
    stitched = images[0]
    for i in range(1, len(images)):
        temp = stitch_pair(stitched, images[i])
        if temp is None:
            print(f"Stitcher: Custom sequential stitching failed at frame index {i}.")
            return None
        stitched = temp
    return stitched


def stitch_pair(img1: np.ndarray, img2: np.ndarray) -> np.ndarray:
    """
    Stitches two images (img2 onto img1) using SIFT (preferred) or ORB descriptors.
    """
    # Try SIFT first, fall back to ORB
    detector_name = "SIFT"
    try:
        detector = cv2.SIFT_create()
    except AttributeError:
        detector = cv2.ORB_create(nfeatures=2000)
        detector_name = "ORB"

    # Detect features
    kp1, des1 = detector.detectAndCompute(img1, None)
    kp2, des2 = detector.detectAndCompute(img2, None)
    
    if des1 is None or des2 is None or len(kp1) < 10 or len(kp2) < 10:
        return None
        
    # Match features
    if detector_name == "SIFT":
        matcher = cv2.BFMatcher(cv2.NORM_L2)
    else:
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        
    matches = matcher.knnMatch(des1, des2, k=2)
    
    # Lowe's ratio test
    good_matches = []
    for m, n in matches:
        if m.distance < 0.75 * n.distance:
            good_matches.append(m)
            
    if len(good_matches) < 8:
        return None
        
    # Extract matching coordinates
    src_pts = np.float32([kp1[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    
    # Find Homography (dst -> src, i.e. maps img2 coordinates onto img1 coordinates)
    H, mask = cv2.findHomography(dst_pts, src_pts, cv2.RANSAC, 5.0)
    if H is None:
        return None
        
    # Compute size of warped canvas to contain both images
    h1, w1 = img1.shape[:2]
    h2, w2 = img2.shape[:2]
    
    # Corners of img2
    corners_img2 = np.float32([[0, 0], [0, h2 - 1], [w2 - 1, h2 - 1], [w2 - 1, 0]]).reshape(-1, 1, 2)
    warped_corners = cv2.perspectiveTransform(corners_img2, H)
    
    # All corners (img1 and warped img2)
    corners_img1 = np.float32([[0, 0], [0, h1 - 1], [w1 - 1, h1 - 1], [w1 - 1, 0]]).reshape(-1, 1, 2)
    all_corners = np.concatenate((corners_img1, warped_corners), axis=0)
    
    # Bounding box of all corners
    [x_min, y_min] = np.int32(all_corners.min(axis=0).ravel() - 0.5)
    [x_max, y_max] = np.int32(all_corners.max(axis=0).ravel() + 0.5)
    
    # translation offset to avoid clipping negative coordinates
    translation_dist = [-x_min if x_min < 0 else 0, -y_min if y_min < 0 else 0]
    
    # Create translation matrix
    T = np.array([[1, 0, translation_dist[0]], [0, 1, translation_dist[1]], [0, 0, 1]])
    
    # Transform homography with offset
    H_translated = T.dot(H)
    
    # Warp img2
    canvas_w = max(w1, x_max) + translation_dist[0]
    canvas_h = max(h1, y_max) + translation_dist[1]
    
    warped_img2 = cv2.warpPerspective(img2, H_translated, (canvas_w, canvas_h))
    
    # Overlay img1 on the canvas
    # Since img1 is already in its coordinate space, we just shift it by the translation vector
    dy, dx = translation_dist[1], translation_dist[0]
    
    # Blend img1 and warped_img2 (simple overlay with transition or max-intensity)
    # For simplicity and robust text display, we'll overlay img1 on top of warped_img2 where pixel values are positive
    roi = warped_img2[dy:dy+h1, dx:dx+w1]
    
    # Make a mask of non-black pixels in img1
    gray_img1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    _, mask_img1 = cv2.threshold(gray_img1, 1, 255, cv2.THRESH_BINARY)
    mask_inv = cv2.bitwise_not(mask_img1)
    
    # Combine BGR channels using mask
    img1_bg = cv2.bitwise_and(roi, roi, mask=mask_inv)
    img1_fg = cv2.bitwise_and(img1, img1, mask=mask_img1)
    
    warped_img2[dy:dy+h1, dx:dx+w1] = cv2.add(img1_bg, img1_fg)
    
    # Crop black borders around the final stitched result
    final_result = crop_black_borders(warped_img2)
    return final_result


def crop_black_borders(img: np.ndarray) -> np.ndarray:
    """Crops empty/black borders out of an image canvas."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
    
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img
        
    # Find bounding box of the largest contour representing the content
    max_area = 0
    best_box = None
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area > max_area:
            max_area = area
            best_box = (x, y, w, h)
            
    if best_box:
        x, y, w, h = best_box
        return img[y:y+h, x:x+w]
    return img
