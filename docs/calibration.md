# Calibration & Homography Warp Index

This document provides a highly detailed mathematical and practical index of the geometric calibration and homography perspective transformation pipeline utilized in the TermoCam system.

---

## Table of Contents
1. [Mathematical Foundation of Planar Homography](#1-mathematical-foundation-of-planar-homography)
2. [The 4-Point Coordinate Contract](#2-the-4-point-coordinate-contract)
3. [A4 Target Proportions & DPI Scale Matrix](#3-a4-target-proportions--dpi-scale-matrix)
4. [Calibration Setup Guide (Physical Alignment)](#4-calibration-setup-guide-physical-alignment)
5. [The Calibration CLI Routine (calibrar_simple.py)](#5-the-calibration-cli-routine-calibrar_simplepy)
6. [Applying the Homography Matrix (warpPerspective)](#6-applying-the-homography-matrix-warpperspective)
7. [Coordinate Scaling for Multi-Resolution Targets](#7-coordinate-scaling-for-multi-resolution-targets)
8. [Triggers for System Re-Calibration](#8-triggers-for-system-re-calibration)
9. [Homography Troubleshooting & Mathematical Edge Cases](#9-homography-troubleshooting--mathematical-edge-cases)

---

## 1. Mathematical Foundation of Planar Homography

A physical camera mounted on an articulated arm above a desk will rarely look perfectly straight down (orthogonal) at the paper document. This misalignment introduces geometric perspective distortion (keystone effect), transforming a rectangular A4 sheet of paper into a trapezoid. This distortion makes line tracking and block alignment fail during OCR segmentation.

To rectify this planar surface back to a flat plane, we estimate a **Planar Homography**. A homography is a projective mapping between two planes.

### 1.1 Homography Matrix Structure
A point $(x, y)$ in the source image is mapped to a point $(x', y')$ in the destination (rectified A4) plane using a $3\times3$ homography matrix $H$:

$$H = \begin{bmatrix} h_{11} & h_{12} & h_{13} \\ h_{21} & h_{22} & h_{23} \\ h_{31} & h_{32} & h_{33} \end{bmatrix}$$

Using homogeneous coordinates, the mapping equation is defined as:

$$\begin{bmatrix} x' \\ y' \\ 1 \end{bmatrix} \sim H \begin{bmatrix} x \\ y \\ 1 \end{bmatrix}$$

Expanding the matrix multiplication gives the coordinate mapping equations:

$$x' = \frac{h_{11}x + h_{12}y + h_{13}}{h_{31}x + h_{32}y + h_{33}}$$

$$y' = \frac{h_{21}x + h_{22}y + h_{23}}{h_{31}x + h_{32}y + h_{33}}$$

### 1.2 Estimating the Homography Matrix
The homography matrix has 9 elements, but is defined up to a scale factor (meaning we can divide all elements by $h_{33}$, setting it to 1). This leaves **8 degrees of freedom**. 
Each point match $(x_i, y_i) \to (x'_i, y'_i)$ provides two independent linear equations. Therefore, we require a minimum of **4 non-collinear point correspondences** to solve for the matrix $H$:

$$\begin{bmatrix} 
-x_1 & -y_1 & -1 & 0 & 0 & 0 & x_1 x'_1 & y_1 x'_1 & x'_1 \\
0 & 0 & 0 & -x_1 & -y_1 & -1 & x_1 y'_1 & y_1 y'_1 & y'_1 \\
-x_2 & -y_2 & -1 & 0 & 0 & 0 & x_2 x'_2 & y_2 x'_2 & x'_2 \\
0 & 0 & 0 & -x_2 & -y_2 & -1 & x_2 y'_2 & y_2 y'_2 & y'_2 \\
-x_3 & -y_3 & -1 & 0 & 0 & 0 & x_3 x'_3 & y_3 x'_3 & x'_3 \\
0 & 0 & 0 & -x_3 & -y_3 & -1 & x_3 y'_3 & y_3 y'_3 & y'_3 \\
-x_4 & -y_4 & -1 & 0 & 0 & 0 & x_4 x'_4 & y_4 x'_4 & x'_4 \\
0 & 0 & 0 & -x_4 & -y_4 & -1 & x_4 y'_4 & y_4 y'_4 & y'_4 
\end{bmatrix}
\begin{bmatrix} h_{11} \\ h_{12} \\ h_{13} \\ h_{21} \\ h_{22} \\ h_{23} \\ h_{31} \\ h_{32} \\ h_{33} \end{bmatrix} = \begin{bmatrix} 0 \\ 0 \\ 0 \\ 0 \\ 0 \\ 0 \\ 0 \\ 0 \end{bmatrix}$$

Solving this system using Singular Value Decomposition (SVD) yields the elements of $H$.

---

## 2. The 4-Point Coordinate Contract

The perspective transform requires the source points to be mapped in a strict, cyclic order to prevent diagonal warping or image inversion.

### 2.1 The Standard Sorting Rules
Points must be collected or ordered in a clockwise direction:
1.  **Point 0: Top-Left (TL)** — Coordinate $(x,y)$ representing the upper-left corner of the document.
2.  **Point 1: Top-Right (TR)** — Coordinate $(x,y)$ representing the upper-right corner.
3.  **Point 2: Bottom-Right (BR)** — Coordinate $(x,y)$ representing the lower-right corner.
4.  **Point 3: Bottom-Left (BL)** — Coordinate $(x,y)$ representing the lower-left corner.

```
Point 0 (TL)  o---------------------o  Point 1 (TR)
              |                     |
              |                     |
              |       A4 PAGE       |
              |                     |
              |                     |
Point 3 (BL)  o---------------------o  Point 2 (BR)
```

### 2.2 Schema of `warp_points.json`
The output of the calibration routine is written to the root folder as `warp_points.json`. The schema must be a 2D integer array containing exactly four points:

```json
[
  [105, 230],
  [1800, 245],
  [1850, 1000],
  [90, 1020]
]
```

---

## 3. A4 Target Proportions & DPI Scale Matrix

The aspect ratio of a standard A4 page is defined by the ISO 216 standard as $1 : \sqrt{2} \approx 1 : 1.4142$. Depending on target DPI requirements, we establish destination canvas dimensions.

| Standard Target | Width (Pixels) | Height (Pixels) | Aspect Ratio | Use Case |
| :--- | :--- | :--- | :--- | :--- |
| **Low DPI (72 DPI)** | $595$ | $842$ | $1 : 1.415$ | Fast previews and thumbnails |
| **Medium DPI (150 DPI)**| $1240$ | $1754$ | $1 : 1.414$ | Fast text extraction & alignment checks |
| **Standard OCR (300 DPI)**| $2480$ | $3508$ | $1 : 1.414$ | Primary reconstruction target for OCR |
| **High Resolution (600 DPI)**| $4960$ | $7016$ | $1 : 1.414$ | Fine-print mathematical formulas |

---

## 4. Calibration Setup Guide (Physical Alignment)

To generate valid warp coordinates, follow these positioning steps:
1.  **Placement:** Place a standard white sheet of A4 paper on the desk.
2.  **Contrast:** Ensure the desk surface has a contrasting color (dark grey, brown, or black). If the desk is white, edge detection algorithms will fail.
3.  **Flatness:** Make sure the paper is completely flat. Creases or folds introduce non-planar distortions that standard homography cannot correct.
4.  **Lighting:** Set up uniform overhead lighting. Avoid strong directional spotlights, as they cast harsh shadows from the camera mount across the page.
5.  **Alignment:** Position the desk arm so that the A4 page is centered in the camera's field of view.

---

## 5. The Calibration CLI Routine (calibrar_simple.py)

The manual calibration script `calibrar_simple.py` runs a GUI loop to capture coordinates:

### 5.1 Step-by-Step execution
1.  **Request Image:** The script fetches an unwarped image from the Pi Zero using `GET /calibrate`. This triggers an autofocus sweep on the Pi.
2.  **Store Staged Image:** Saves the frame locally as `autofocus_photo.jpg`.
3.  **Initialize OpenCV Window:** Loads `autofocus_photo.jpg` into memory, downsamples it for display on standard screens (keeping track of the scaling factor), and binds a mouse callback listener.
4.  **Collect Clicks:** The user clicks the corners of the sheet of paper in clockwise order (TL, TR, BR, BL). Red circles and green connecting lines are rendered onto the screen to guide the user.
5.  **Verify & Scale:** Once four points are captured, the script scales the coordinates back to the original resolution of the image (reversing the downsampling scale factor).
6.  **Write Matrix File:** Dumps the points to `warp_points.json`.

---

## 6. Applying the Homography Matrix (warpPerspective)

Once the coordinates are recorded in `warp_points.json`, the image transformation runs on each captured frame:

```python
import cv2
import json
import numpy as np

# Load source points
with open("warp_points.json", "r") as f:
    pts_src = np.array(json.load(f), dtype=np.float32)

# Set target dimension (A4 at 300 DPI)
width, height = 2480, 3508

# Define destination points
pts_dst = np.array([
    [0, 0],
    [width - 1, 0],
    [width - 1, height - 1],
    [0, height - 1]
], dtype=np.float32)

# Calculate Homography Matrix
H = cv2.getPerspectiveTransform(pts_src, pts_dst)

# Load raw captured photo
img_raw = cv2.imread("autofocus_photo.jpg")

# Warp raw image to rectified A4 format
img_rectified = cv2.warpPerspective(img_raw, H, (width, height))
cv2.imwrite("rectified_document.jpg", img_rectified)
```

---

## 7. Coordinate Scaling for Multi-Resolution Targets

Because calibration is performed on high-resolution photos (e.g. $4608 \times 2592$), if we want to apply the same warp parameters to a low-resolution live stream (e.g. $640 \times 480$), the coordinates must be scaled proportionally to prevent out-of-bounds mapping:

$$\text{Scale}_x = \frac{W_{\text{stream}}}{W_{\text{calib}}}$$

$$\text{Scale}_y = \frac{H_{\text{stream}}}{H_{\text{calib}}}$$

$$P_{\text{stream}} = \begin{bmatrix} x_{\text{calib}} \times \text{Scale}_x \\ y_{\text{calib}} \times \text{Scale}_y \end{bmatrix}$$

The edge daemon performs this coordinate translation on-the-fly before applying perspective warps on the alignment stream.

---

## 8. Triggers for System Re-Calibration

The perspective transform remains valid as long as the camera and the target plane remain fixed. You must re-run `calibrar_simple.py` if:
*   **Arm Displacement:** The articulated desk arm is bumped, rotated, or adjusted in height.
*   **Resolution Change:** The default high-resolution image capture settings are changed in `config.yaml`.
*   **Desk Placement Shift:** The primary scanning location on the desk is changed.
*   **Lens Modification:** The physical focus assembly or camera sensor lens is swapped (e.g., swapping a standard lens for a wide-angle lens).

---

## 9. Homography Troubleshooting & Mathematical Edge Cases

If the warped output is black, heavily warped, or fails to render, check these conditions:

*   **Self-Intersecting Polygons (Inverted Points):** If points are clicked out of order (e.g., TL, BR, TR, BL), the homography matrix will attempt to wrap the plane onto itself. This results in a distorted image of crossed triangles. Keep coordinates strictly clockwise.
*   **Collinear Points:** If three of the clicked points fall on a straight line, the perspective transform is degenerate. SVD will fail to solve the homography matrix, resulting in a projection error. Make sure all four clicked points are distinct corners.
*   **Dividing by Zero:** If the divisor term $h_{31}x + h_{32}y + h_{33}$ equals $0$ for a pixel coordinate, projection maps the point to infinity. This typically happens when the camera is angled nearly perpendicular ($90^\circ$) to the surface, causing lines to cross behind the sensor projection. Maintain desk arm angles within $60^\circ$ of vertical.
*   **Excessive Warping Bounds:** Warping an image at extreme angles stretches single pixels across large areas, resulting in blurriness and pixelation. Mount the camera as close to perpendicular (overhead) as possible to maximize text resolution.
