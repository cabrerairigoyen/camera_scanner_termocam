# Hardware Layer & Integration Index

This document details the hardware specifications, assembly instructions, cabling interfaces, system services, and diagnostic commands for the TermoCam Sweep Scanner edge system.

---

## Table of Contents
1. [Physical Assembly & Mounts](#1-physical-assembly--mounts)
2. [Bill of Materials (BOM)](#2-bill-of-materials-bom)
3. [Raspberry Pi Zero 2W Sensor Pinouts & Cabling](#3-raspberry-pi-zero-2w-sensor-pinouts--cabling)
4. [SoC Memory Constraints & Swap Space Configuration](#4-soc-memory-constraints--swap-space-configuration)
5. [Firmware & Boot Configurations (/boot/firmware/config.txt)](#5-firmware--boot-configurations-bootfirmwareconfigtxt)
6. [The libcamera Subsystem Architecture](#6-the-libcamera-subsystem-architecture)
7. [Thermal Characteristics & Heat Dissipation Boundaries](#7-thermal-characteristics--heat-dissipation-boundaries)
8. [Automatic Startup Daemon (Systemd Configuration)](#8-automatic-startup-daemon-systemd-configuration)
9. [Hardware Diagnostic Commands & Checklists](#9-hardware-diagnostic-commands--checklists)

---

## 1. Physical Assembly & Mounts

To operate as a document scanner, the camera module must be mounted in a rigid, top-down configuration. Hand-holding the camera module leads to erratic tilts, severe optical distortions, and inconsistent focal lengths, making stitching impossible.

### 1.1 Spatial Position and Angles
*   **Mount Style:** Downward-facing desk arm or rigid vertical copy stand.
*   **Mount Height:** $15\text{--}25\text{ cm}$ above the desk surface, targeting a field of view that encompasses a standard A4 page ($210\text{ mm} \times 297\text{ mm}$) at standard sweep heights.
*   **Angle of Capture:** The physical lens axis must be parallel to the normal vector of the desk surface. If a skew angle exists, the homography matrix will scale features unevenly.
*   **Mounting Adaptor:** A 3D-printed bracket joining a standard 1/4" tripod screw stand with the Raspberry Pi Zero 2W official camera case.

---

## 2. Bill of Materials (BOM)

| Item Quantity | Component Name | Exact Part Number / Description | Function |
| :--- | :--- | :--- | :--- |
| 1 | Raspberry Pi Zero 2W | Broadcom BCM2710A1 SoC, 512MB LPDDR2 RAM | Core edge processing platform |
| 1 | Camera Sensor Module 3 | Sony IMX708 (11.9 Megapixel PDAF) | Ingestion of image frames |
| 1 | High-Speed microSD Card | SanDisk Extreme 32GB MicroSDXC UHS-I | OS system partition & session database |
| 1 | CSI Ribbon Cable | FPC 15-Pin to 22-Pin Ribbon Cable ($15\text{ cm}$) | High-speed sensor transmission bridge |
| 1 | Rigid Tripod Stand | Desk mount scissor arm or boom stand | Rigid optical alignment |
| 1 | Pi Power Adapter | 5.1V 2.5A Micro-USB adapter (Official Pi Supply) | Solid power delivery to prevent sensor drops |
| 1 | Aluminium Passive Heatsink | 14mm x 14mm self-adhesive copper/aluminium sink | Prevent CPU thermal throttling |

---

## 3. Raspberry Pi Zero 2W Sensor Pinouts & Cabling

The Raspberry Pi Zero 2W uses a smaller Camera Serial Interface (CSI-2) connector than the Raspberry Pi 4 or 5. It uses a 22-pin pitch connector, whereas the IMX708 sensor module uses a 15-pin connector.

### 3.1 Cabling Steps
1.  **Cable Orientation:** The $15\text{ cm}$ ribbon cable has metallic contacts on both ends. At the Raspberry Pi Zero 2W connector, the contacts must face **downward** (toward the PCB).
2.  **Latch Locking:** Gently pull the dark lock bar of the Pi CSI connector, slide the cable in securely, and push the bar down. Ensure the cable is straight.
3.  **Sensor Side Connection:** At the camera board connector, the contacts must face **inward** (toward the camera sensor circuitry). Press the locking clip down until it snaps.

---

## 4. SoC Memory Constraints & Swap Space Configuration

The Pi Zero 2W has only **512 MB LPDDR2 RAM**. This RAM is shared with the VideoCore IV GPU. 
Running a Python Flask server, Picamera2 daemon, and performing downsampled frame calculations leaves very little margin. If RAM is exhausted, the Linux Out-Of-Memory (OOM) killer will immediately kill the python process.

### 4.1 Configuring the GPU Memory Split
Since we do not run graphical desktops or HDMI displays on the edge Pi Zero (it runs headless), we must minimize GPU memory allocation:
1.  Open `/boot/firmware/config.txt` via root permissions:
    ```bash
    sudo nano /boot/firmware/config.txt
    ```
2.  Add or modify the memory split parameter:
    ```ini
    gpu_mem=16
    ```
    This allocates only $16\text{MB}$ to the GPU, leaving $496\text{MB}$ for system processes.

### 4.2 Configuring virtual memory swap space
To prevent process crashes during high memory allocations (such as zipping large frames directories), you must increase the virtual swap memory partition:
1.  Disable the active swap daemon:
    ```bash
    sudo dphys-swapfile swapoff
    ```
2.  Modify the configuration file:
    ```bash
    sudo nano /etc/dphys-swapfile
    ```
3.  Locate `CONF_SWAPSIZE` and increase its value to `1024` (1 GB of virtual memory):
    ```ini
    CONF_SWAPSIZE=1024
    ```
4.  Reinitialize and start the swap daemon:
    ```bash
    sudo dphys-swapfile setup
    sudo dphys-swapfile swapon
    ```
5.  Verify the swap memory mapping using:
    ```bash
    free -m
    ```

---

## 5. Firmware & Boot Configurations (/boot/firmware/config.txt)

The legacy camera driver subsystem (MMAL and `raspistill`) has been deprecated. Raspberry Pi OS Bookworm utilizes the modern `libcamera` backend, which interfaces through kernel overlays.

### 5.1 Kernel Configuration Setup
To map the Sony IMX708 sensor to the Raspberry Pi Zero 2W, configure the kernel interfaces:
1.  Open the firmware configuration file:
    ```bash
    sudo nano /boot/firmware/config.txt
    ```
2.  Disable automatic camera detection to ensure manual overlays are parsed:
    ```ini
    camera_auto_detect=0
    ```
3.  Add the specific device tree overlay configuration for the Sony IMX708 sensor:
    ```ini
    dtoverlay=imx708
    ```
4.  If using the wide-angle camera module version, apply the wide overlay:
    ```ini
    dtoverlay=imx708,wide=1
    ```
5.  Save the changes and reboot:
    ```bash
    sudo reboot
    ```

---

## 6. The libcamera Subsystem Architecture

Modern Raspberry Pi OS layers use `libcamera` as an open-source C++ library that bypasses legacy closed GPU drivers.

```
+------------------------------------------+
|          Python Application Code         |
|      (live_camera_server.py / Picamera2) |
+--------------------+---------------------+
                     |
                     v
+--------------------+---------------------+
|           libcamera C++ Core             |
|       (ISP Control & Focus Loops)        |
+--------------------+---------------------+
                     |
                     v
+--------------------+---------------------+
|      Linux V4L2 Subdev Kernel Drivers     |
|         (imx708.ko / bcm2835-unicam)     |
+--------------------+---------------------+
                     |
                     v
+--------------------+---------------------+
|         Physical Sony IMX708 CSI         |
+------------------------------------------+
```

### 6.1 The libcamerify Wrapper
Standard OpenCV `cv2.VideoCapture` uses legacy V4L2 IOCTL commands. Because libcamera doesn't expose standard V4L2 buffer formats directly without translation, calling `cv2.VideoCapture(0)` on Pi OS will fail.
The `libcamerify` utility intercepts V4L2 calls using `LD_PRELOAD` and translates them into corresponding libcamera buffer streams, allowing standard Python CV2 libraries to receive frames.

---

## 7. Thermal Characteristics & Heat Dissipation Boundaries

The Broadcom BCM2710A1 quad-core SoC operates inside a narrow thermal envelope when housed in closed plastic cases.

### 7.1 Heat Progression Rates
*   **Idle Daemon:** CPU at $1\text{--}3\%$ load. Temperature ranges between $45^\circ\text{C}$ and $50^\circ\text{C}$.
*   **MJPEG Streaming:** Continuous compression and encoding on a single core. CPU load at $25\%$. Temperature increases by $1.5^\circ\text{C}$ per minute, stabilizing at $70^\circ\text{C}$ in open air, or exceeding $80^\circ\text{C}$ in standard cases.
*   **Sweep Capturing Loop:** Fast array captures, resizing, and Laplacian calculations. Temperature peaks quickly.
*   **Thermal Guardrail:** The edge capture daemon monitors the CPU core temperature via `/sys/class/thermal/thermal_zone0/temp`. If the reading exceeds $80.0^\circ\text{C}$, the daemon rejects new sweep start operations to prevent hardware throttling and damage.

### 7.2 Mitigation Steps
*   **Passive Cooling:** Apply a $14\text{mm} \times 14\text{mm}$ copper heatsink directly onto the Broadcom chip.
*   **Case Ventilation:** Drill ventilation holes in the plastic case lid or run the board open-air.
*   **Software Limits:** The Flask server enforces a 5-minute (300-second) inactivity timeout for streaming. If the client leaves the alignment stream open, it auto-closes.

---

## 8. Automatic Startup Daemon (Systemd Configuration)

To ensure the edge capture appliance operates autonomously when powered on, configure it as a systemd background service.

### 8.1 Creating the Service File
1.  Create the service file on the Pi:
    ```bash
    sudo nano /etc/systemd/system/termocam.service
    ```
2.  Write the following configuration:
    ```ini
    [Unit]
    Description=TermoCam Edge Capture Flask Server
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    User=pi
    WorkingDirectory=/home/pi/camera_scanner_termocam/pi
    ExecStart=/usr/bin/libcamerify /usr/bin/python3 live_camera_server.py
    Restart=always
    RestartSec=5
    Environment=PYTHONUNBUFFERED=1
    StandardOutput=append:/var/log/termocam.log
    StandardError=append:/var/log/termocam.err

    [Install]
    WantedBy=multi-user.target
    ```
3.  Reload the systemd manager configs:
    ```bash
    sudo systemctl daemon-reload
    ```
4.  Enable the service to auto-start on boot:
    ```bash
    sudo systemctl enable termocam.service
    ```
5.  Start the service immediately:
    ```bash
    sudo systemctl start termocam.service
    ```
6.  Inspect the running status:
    ```bash
    sudo systemctl status termocam.service
    ```

---

## 9. Hardware Diagnostic Commands & Checklists

If the camera sensor fails to capture frames or returns errors, run the following diagnostic commands via SSH.

### 9.1 Hardware Verification Checklist

#### 1. Verify Device Tree Overlays
Ensure the IMX708 kernel module is loaded:
```bash
lsmod | grep imx708
```
*Expected Output:*
`imx708                 28672  1` (or similar size). If blank, verify `/boot/firmware/config.txt`.

#### 2. Query Camera Detection
Run the libcamera utility to see if camera devices are visible:
```bash
rpicam-still --list-cameras
```
*Expected Output:*
```
Available cameras
-----------------
0 : imx708 [4608x2592] (/base/soc/i2c0mux/i2c@1/imx708@1a)
    Modes: 'SRGGB10_CSI2P' : 1536x864 [120.00 fps]
                             2304x1296 [56.00 fps]
                             4608x2592 [14.00 fps]
```
If "No cameras available" is returned:
*   Inspect CSI ribbon locking pins on both ends.
*   Ensure the ribbon is not damaged or bent.
*   Verify GPU memory allocation split (`gpu_mem=16`).

#### 3. Test Still Capture Directly
Test capture functionality using libcamera-still bypass:
```bash
rpicam-still -o /tmp/test.jpg --nopreview --timeout 1000
```
This isolates hardware faults from Python or server library configurations.

#### 4. Monitor CPU Core Temperature
Check the current temperature of the SoC chip:
```bash
vcgencmd measure_temp
```
*Expected Output:* `temp=48.2'C`

#### 5. Verify log files
If the systemd service fails, inspect the diagnostic files:
```bash
tail -n 50 /var/log/termocam.err
```
This output contains Python crash logs and camera binding errors.
