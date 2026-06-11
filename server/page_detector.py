import cv2
import numpy as np
import os
import math

PREVIEW_THRESHOLD = 0.45
AUTO_CAPTURE_THRESHOLD = 0.85
A4_WARP_THRESHOLD = 0.80

try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False

class PageDetector:
    def __init__(self):
        self.yolo_model = None
        self.weights_path = os.path.join(
            os.path.dirname(__file__), 
            "data", "weights", "page_detector", "best.pt"
        )
        self._load_custom_yolo()

    def _load_custom_yolo(self):
        if not ULTRALYTICS_AVAILABLE:
            print("PageDetector: ultralytics package not installed. Using OpenCV fallback.")
            return
            
        if os.path.exists(self.weights_path):
            try:
                self.yolo_model = YOLO(self.weights_path)
                print(f"PageDetector: Successfully loaded custom weights from {self.weights_path}")
            except Exception as e:
                print(f"PageDetector: Failed to load YOLO weights: {e}")
                self.yolo_model = None
        else:
            print("PageDetector: No custom page detector weights found. Using OpenCV fallback only.")

    def detect_page(self, image: np.ndarray, mode: str = "preview") -> dict:
        """
        Detects an A4 page and returns validation metadata.
        mode: "preview" (loose validation) or "strict" (conservative validation).
        """
        if image is None:
            return self._build_empty_response("no_page_detected", "Image is None")

        h, w = image.shape[:2]
        
        # 1. Try Custom YOLO Segmentation
        if self.yolo_model is not None:
            yolo_result = self._yolo_detect(image, w, h, mode)
            if yolo_result["page_detected"]:
                return yolo_result

        # 2. Fallback to OpenCV
        opencv_result = self._opencv_detect(image, w, h, mode)
        if opencv_result["page_detected"] or opencv_result["decision"] == "not_safe_to_warp":
            return opencv_result

        # 3. Explicit Failure
        return self._build_empty_response("no_page_detected", "Neither YOLO nor OpenCV found a valid page.")

    def _build_empty_response(self, decision: str, reason: str) -> dict:
        return {
            "page_detected": False,
            "confidence": 0.0,
            "method": "none",
            "corners": None,
            "mask_available": False,
            "a4_geometry_score": 0.0,
            "area_ratio": 0.0,
            "decision": decision,
            "reason": reason
        }

    def _yolo_detect(self, image: np.ndarray, w: int, h: int, mode: str) -> dict:
        try:
            results = self.yolo_model(image, verbose=False)
            if len(results) == 0 or results[0].masks is None:
                return self._build_empty_response("no_page_detected", "YOLO found no masks.")
                
            masks = results[0].masks.data.cpu().numpy()
            boxes = results[0].boxes.data.cpu().numpy()
            
            if len(masks) == 0:
                return self._build_empty_response("no_page_detected", "YOLO found no masks.")

            # Assume class 0 is document_page, pick highest confidence
            best_idx = np.argmax(boxes[:, 4])
            raw_conf = float(boxes[best_idx, 4])
            
            mask = masks[best_idx]
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
            mask = (mask * 255).astype(np.uint8)
            
            quad = self._mask_to_quad(mask)
            if quad is None:
                return self._build_empty_response("no_page_detected", "Could not extract quadrilateral from YOLO mask.")
                
            return self._validate_and_build_response(quad, w, h, raw_conf, mode, "custom_yolo_seg", mask_available=True)

        except Exception as e:
            print(f"YOLO detection exception: {e}")
            return self._build_empty_response("no_page_detected", f"YOLO exception: {str(e)}")

    def _opencv_detect(self, image: np.ndarray, w: int, h: int, mode: str) -> dict:
        orig_h, orig_w = h, w
        ratio = orig_w / 640.0
        new_h = int(orig_h / ratio)
        resized = cv2.resize(image, (640, new_h))
        
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (7, 7), 0)
        
        median = np.median(blurred)
        lower = int(max(0, 0.7 * median))
        upper = int(min(255, 1.3 * median))
        edged = cv2.Canny(blurred, lower, upper)
        
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        edged = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)
        
        last_fail_res = None
        contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return self._build_empty_response("no_page_detected", "OpenCV found no contours.")
            
        contours = sorted(contours, key=cv2.contourArea, reverse=True)
        for i, c in enumerate(contours[:5]):
            area = cv2.contourArea(c)
            threshold = 640 * new_h * 0.02
            print(f"Contour {i}: Area={area:.1f}, Threshold={threshold:.1f}")
            # Minimum area for OpenCV to even consider it (2% of resized frame)
            if area > threshold:
                hull = cv2.convexHull(c)
                peri = cv2.arcLength(hull, True)
                for eps in np.linspace(0.02, 0.1, 10):
                    approx = cv2.approxPolyDP(hull, eps * peri, True)
                    if len(approx) == 4:
                        pts = approx.reshape(4, 2)
                        rect = self._order_points(pts)
                        rect *= ratio
                        
                        raw_conf = 0.85 # OpenCV gets a flat base confidence if it finds a good shape
                        res = self._validate_and_build_response(rect, w, h, raw_conf, mode, "opencv_fallback", mask_available=False)
                        if res["page_detected"]:
                            return res
                        last_fail_res = res
                            
        if last_fail_res:
            return last_fail_res
        return self._build_empty_response("no_page_detected", "OpenCV failed to find a valid 4-point polygon.")

    def _validate_and_build_response(self, corners: np.ndarray, w: int, h: int, raw_conf: float, mode: str, method: str, mask_available: bool) -> dict:
        """Applies strong geometric validation gates based on mode."""
        pts = np.array(corners, dtype=np.float32)
        
        # 1. Ensure exactly 4 corners
        if len(pts) != 4:
            return self._build_empty_response("no_page_detected", "Not exactly 4 corners.")
            
        # 2. Ensure contour is convex
        if not cv2.isContourConvex(pts.astype(np.int32)):
            return self._build_empty_response("no_page_detected", "Contour is not convex.")
            
        # 3. Calculate Geometry & Ratios
        w_top = np.linalg.norm(pts[0] - pts[1])
        w_bottom = np.linalg.norm(pts[3] - pts[2])
        h_left = np.linalg.norm(pts[0] - pts[3])
        h_right = np.linalg.norm(pts[1] - pts[2])
        
        max_w = max(w_top, w_bottom)
        max_h = max(h_left, h_right)
        
        if max_w == 0 or max_h == 0:
            return self._build_empty_response("no_page_detected", "Degenerate corners (zero width/height).")
            
        area = max_w * max_h
        frame_area = w * h
        area_ratio = float(area / frame_area)
        
        aspect = max_h / max_w
        aspect_diff = abs(aspect - 1.414)
        
        # Check if opposite sides are reasonably parallel
        w_ratio = min(w_top, w_bottom) / max(w_top, w_bottom)
        h_ratio = min(h_left, h_right) / max(h_left, h_right)
        
        # Calculate geometry score (1.0 is perfect)
        geom_score = 1.0 - (aspect_diff * 1.5) - ((1.0 - w_ratio) * 0.5) - ((1.0 - h_ratio) * 0.5)
        geom_score = max(0.0, min(1.0, float(geom_score)))
        
        # Adjust confidence based on geometry
        final_conf = raw_conf * geom_score
        
        # Strict mode validation
        if mode == "strict":
            if area_ratio < 0.15:
                return self._build_empty_response("not_safe_to_warp", f"Area ratio too small ({area_ratio:.2f} < 0.15).")
            if aspect_diff > 0.4:
                return self._build_empty_response("not_safe_to_warp", f"Aspect ratio too far from A4 ({aspect:.2f}).")
            if w_ratio < 0.7 or h_ratio < 0.7:
                return self._build_empty_response("not_safe_to_warp", "Perspective distortion too extreme.")
            if final_conf < A4_WARP_THRESHOLD:
                return self._build_empty_response("not_safe_to_warp", f"Confidence too low for strict warp ({final_conf:.2f} < {A4_WARP_THRESHOLD}).")
                
            decision = "safe_to_warp"
            
        # Preview mode validation
        else:
            if area_ratio < 0.05:
                return self._build_empty_response("no_page_detected", "Area ratio too small for preview.")
            if final_conf < PREVIEW_THRESHOLD:
                return self._build_empty_response("no_page_detected", f"Confidence too low for preview ({final_conf:.2f} < {PREVIEW_THRESHOLD}).")
                
            decision = "preview_overlay"

        return {
            "page_detected": True,
            "confidence": float(final_conf),
            "method": method,
            "corners": pts.tolist(),
            "mask_available": mask_available,
            "a4_geometry_score": float(geom_score),
            "area_ratio": float(area_ratio),
            "decision": decision,
            "reason": "Validation passed."
        }

    def _mask_to_quad(self, mask: np.ndarray):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        c = max(contours, key=cv2.contourArea)
        hull = cv2.convexHull(c)
        peri = cv2.arcLength(hull, True)
        
        for eps in np.linspace(0.01, 0.1, 20):
            approx = cv2.approxPolyDP(hull, eps * peri, True)
            if len(approx) == 4:
                pts = approx.reshape(4, 2)
                return self._order_points(pts)
                
        # Bounding box fallback
        x, y, w, h = cv2.boundingRect(c)
        return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])

    def _order_points(self, pts):
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect

# Global singleton
page_detector = PageDetector()
