import cv2
import numpy as np


def sharpness_laplacian(image) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def frame_difference(current, previous) -> float:
    if previous is None:
        return float("inf")
    current_small = cv2.resize(current, (320, 180))
    previous_small = cv2.resize(previous, (320, 180))
    return float(np.mean(cv2.absdiff(current_small, previous_small)))


def should_accept_frame(image, previous, config: dict) -> dict:
    sharpness = sharpness_laplacian(image)
    if sharpness < float(config.get("sharpness_threshold", 120.0)):
        return {"accepted": False, "reason": "blur", "sharpness": sharpness, "difference": None}
    difference = frame_difference(image, previous)
    if previous is not None and difference < float(config.get("min_frame_difference", 8.0)):
        return {"accepted": False, "reason": "duplicate", "sharpness": sharpness, "difference": difference}
    return {"accepted": True, "reason": "accepted", "sharpness": sharpness, "difference": difference}
