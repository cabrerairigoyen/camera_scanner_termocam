import requests
import subprocess
import sys
import os

PI_HOST = os.environ.get('PI_HOST', '192.168.1.153')
HTTP_URL = f"http://{PI_HOST}:5000/latest_photo"
SCP_SOURCE = f"pi@{PI_HOST}:/home/pi/termocam/pi/latest_photo.jpg"
OUTPUT_FILE = "../autofocus_photo.jpg"

def download_http():
    print(f"Attempting HTTP download from {HTTP_URL}...")
    try:
        response = requests.get(HTTP_URL, timeout=10)
        response.raise_for_status()
        with open(OUTPUT_FILE, 'wb') as f:
            f.write(response.content)
        print(f"✅ Downloaded successfully via HTTP to {OUTPUT_FILE}")
        return True
    except requests.RequestException as e:
        print(f"HTTP download failed: {e}")
        return False

def download_scp():
    print(f"Attempting SCP download from {SCP_SOURCE}...")
    try:
        subprocess.run(["scp", "-o", "BatchMode=yes", SCP_SOURCE, OUTPUT_FILE], check=True)
        print(f"✅ Downloaded successfully via SCP to {OUTPUT_FILE}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"SCP download failed: {e}")
        return False

def main():
    print("Starting photo download...")
    
    # Ensure output dir exists
    out_dir = os.path.dirname(OUTPUT_FILE)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)
        
    if not download_http():
        print("Falling back to SCP...")
        if not download_scp():
            print("❌ Failed to download photo using both HTTP and SCP.")
            sys.exit(1)

if __name__ == "__main__":
    main()
