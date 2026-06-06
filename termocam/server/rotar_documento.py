import cv2
import argparse
import os

INPUT_PATH = '../documento_a4.jpg'
OUTPUT_PATH = '../documento_a4_corregido.jpg'

def main():
    parser = argparse.ArgumentParser(description="Rotate and/or flip a processed document.")
    parser.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=180,
                        help="Rotation angle in degrees clockwise.")
    parser.add_argument("--flip", action="store_true",
                        help="Apply horizontal flip (reflection) to fix mirror effects.")
    
    args = parser.parse_args()
    
    if not os.path.exists(INPUT_PATH):
        print(f"Error: {INPUT_PATH} not found. Run procesar_a4.py first.")
        return
        
    print(f"Loading {INPUT_PATH}...")
    img = cv2.imread(INPUT_PATH)
    
    if args.rotate == 90:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        print("Rotated 90 degrees.")
    elif args.rotate == 180:
        img = cv2.rotate(img, cv2.ROTATE_180)
        print("Rotated 180 degrees.")
    elif args.rotate == 270:
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        print("Rotated 270 degrees.")
        
    if args.flip:
        img = cv2.flip(img, 1) # 1 = horizontal flip
        print("Flipped horizontally.")
        
    cv2.imwrite(OUTPUT_PATH, img)
    print(f"✅ Correction complete! Saved to {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
