# 📷 TermoCam - Sistema de Captura y Procesamiento de Documentos

## 🎯 Descripción General

TermoCam es un sistema completo de captura y procesamiento de documentos que utiliza una Raspberry Pi con cámara para digitalizar documentos físicos con corrección automática de perspectiva y mejoras de calidad para OCR.

## 🏗️ Arquitectura del Sistema

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Raspberry Pi  │    │    Servidor      │    │   Cliente Web   │
│                 │    │    Local         │    │                 │
│ • Camera Module │◄──►│ • Procesamiento  │◄──►│ • Interfaz HTML │
│ • SPI Display   │    │ • Calibración    │    │ • Control       │
│ • Live Stream   │    │ • Transformación │    │ • Visualización │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## 📁 Estructura del Proyecto

```
termocam/
├── config.py                    # Configuración Google Cloud Document AI
├── pi/                         # Scripts para Raspberry Pi
│   ├── calibrar_simple.py      # Calibración de esquinas
│   └── live_camera_server.py   # Servidor de cámara en vivo
├── server/                     # Scripts de procesamiento
│   ├── descargar_foto.py       # Descarga de fotos desde Pi
│   ├── procesar_a4.py          # Procesamiento A4 para OCR
│   └── rotar_documento.py      # Corrección de orientación
├── templates/                  # Interfaces web
│   ├── camera_control.html     # Control básico de cámara
│   └── document_scanner.html   # Escáner avanzado de documentos
├── numworks_display.html       # Simulador de display NumWorks
└── TERMOCAM_DOCUMENTATION.md   # Esta documentación
```

## 🔧 Componentes Técnicos

### 1. Sistema de Cámara (Raspberry Pi)

**Archivo:** `pi/live_camera_server.py`

**Funcionalidades:**
- **Live Streaming:** Video en tiempo real a 640x480 píxeles
- **Captura de Alta Resolución:** Fotos a 4608x2592 píxeles (máx IMX708)
- **Transformaciones en Tiempo Real:**
  - Rotación ajustable
  - Corrección de perspectiva mediante 4 puntos de esquina
  - Aplicación de transformaciones por OpenCV

**Características Técnicas:**
```python
# Configuración de stream en vivo
Video: 640x480, ~10 FPS, JPEG compresión 85%
Foto: 4608x2592, autofoco activado, sin previsualización

# Transformaciones soportadas
- Rotación: ±360°
- Perspectiva: 4 puntos de esquina independientes
- Matrices de transformación en tiempo real
```

**API Endpoints:**
- `GET /video_feed` - Stream MJPEG en vivo
- `POST /capture_photo` - Captura foto alta resolución
- `GET /latest_photo` - Descarga última foto
- `POST /set_transform` - Configura transformaciones

### 2. Sistema de Calibración

**Archivo:** `pi/calibrar_simple.py`

**Proceso:**
1. Carga imagen de referencia (`autofocus_photo.jpg`)
2. Interfaz gráfica para selección de 4 esquinas
3. Almacena coordenadas en `warp_points.json`
4. Orden requerido: Arriba-izq → Arriba-der → Abajo-der → Abajo-izq

**Salida:**
```json
[
  [x1, y1],  // Esquina superior izquierda
  [x2, y2],  // Esquina superior derecha  
  [x3, y3],  // Esquina inferior derecha
  [x4, y4]   // Esquina inferior izquierda
]
```

### 3. Procesamiento de Documentos

**Archivo:** `server/procesar_a4.py`

**Pipeline de Procesamiento:**
1. **Carga de Datos:**
   - Imagen original desde Pi
   - Puntos de calibración desde JSON

2. **Transformación Geométrica:**
   - Cálculo matriz de perspectiva
   - Warp a formato A4 estándar (210×297mm a 300 DPI)
   - Dimensiones resultantes: 2480×3508 píxeles

3. **Mejora de Calidad:**
   - Conversión a escala de grises
   - CLAHE (Contrast Limited Adaptive Histogram Equalization)
   - Optimización para OCR

**Parámetros de Salida:**
```python
# Formato A4 a 300 DPI
width_px = 210 * 300 / 25.4 = 2480 píxeles
height_px = 297 * 300 / 25.4 = 3508 píxeles

# Mejora de contraste
CLAHE: clipLimit=3.0, tileGridSize=(8,8)
```

### 4. Corrección de Orientación

**Archivo:** `server/rotar_documento.py`

**Correcciones Aplicadas:**
1. **Rotación:** 180° para invertir documento
2. **Reflexión Horizontal:** Corrección del efecto espejo de la cámara
3. **Preservación de Calidad:** Sin pérdida durante transformaciones

### 5. Sistema de Descarga

**Archivo:** `server/descargar_foto.py`

**Métodos de Transferencia:**
1. **HTTP (Prioritario):** Descarga directa desde `http://raspberrypi.local:5000/latest_photo`
2. **SCP (Respaldo):** Transferencia segura vía SSH

**Ventajas del método HTTP:**
- Más rápido que SCP
- No requiere autenticación SSH
- Integración directa con el servidor de cámara

## 🖥️ Interfaces de Usuario

### 1. Control Básico de Cámara

**Archivo:** `templates/camera_control.html`

**Características:**
- Stream en vivo embebido
- Botón de captura con autofoco
- Vista previa de última foto capturada
- Diseño responsivo y moderno

### 2. Escáner Avanzado de Documentos

**Archivo:** `templates/document_scanner.html`

**Workflow Completo:**
1. **Captura:** Múltiples segmentos de documento
2. **Procesamiento:** Corrección automática de perspectiva
3. **Stitching:** Combinación de segmentos en documento completo
4. **Descarga:** Resultado final optimizado

**Características Avanzadas:**
- Progreso visual en tiempo real
- Instrucciones paso a paso
- Gestión de estado de sesión
- Alertas y notificaciones

## 🎮 Simulador NumWorks Display

**Archivo:** `numworks_display.html`

**Especificaciones Técnicas:**
- **Resolución:** 320×240 píxeles
- **Profundidad de Color:** 16 bits por píxel (RGB565)
- **Framerate:** ~50 FPS (teórico)
- **Tamaño de Frame:** 1,228,800 bits por frame
- **SPI Máximo:** 62.5 MHz (Raspberry Pi), 50 MHz (STM32F412)

**Modos de Simulación:**
1. **Calculadora:** Interfaz estándar con teclado virtual
2. **Gráficos:** Visualización de funciones matemáticas
3. **Menú:** Sistema de navegación
4. **Test Pattern:** Patrón de colores RGB565

**Funcionalidades:**
- Conversión RGB ↔ RGB565 en tiempo real
- Contador de FPS en vivo
- Información de píxel bajo cursor
- Simulación de framebuffer SPI

## ⚙️ Configuración y Setup

### 1. Requisitos del Sistema

**Raspberry Pi:**
- Raspberry Pi 4B (recomendado)
- Módulo de cámara IMX708 o compatible
- Raspbian OS actualizado
- Python 3.9+

**Dependencias Python:**
```bash
pip install flask opencv-python numpy requests subprocess
```

**Librerías del Sistema:**
```bash
sudo apt update
sudo apt install libcamera-tools
```

### 2. Configuración de Red

**mDNS:**
- Hostname: `raspberrypi.local`
- IP actual: 192.168.1.153 (puede cambiar)
- Puerto HTTP: 5000

### 3. Inicialización

**En Raspberry Pi:**
```bash
cd /home/pi
python3 live_camera_server.py
```

**En Cliente Local:**
```bash
cd termocam/server
python3 descargar_foto.py
python3 calibrar_simple.py  # Solo primera vez
python3 procesar_a4.py
```

## 🔍 Flujo de Trabajo Típico

### Procesamiento de Documento Simple

1. **Preparación:**
   - Asegurar que el Pi está encendido y conectado
   - Iniciar servidor de cámara en Pi
   - Posicionar documento bajo cámara

2. **Captura:**
   - Abrir `camera_control.html` en navegador
   - Ajustar posición del documento
   - Tomar foto con autofoco

3. **Descarga y Calibración:**
   ```bash
   python3 descargar_foto.py
   python3 calibrar_simple.py  # Solo si no hay warp_points.json
   ```

4. **Procesamiento:**
   ```bash
   python3 procesar_a4.py
   python3 rotar_documento.py  # Si es necesario
   ```

5. **Resultado:**
   - `documento_a4.jpg` - Procesado para OCR
   - `documento_a4_corregido.jpg` - Con corrección de orientación

### Escaneo Multi-Segmento

1. **Apertura de Interfaz:**
   - Acceder a `document_scanner.html`
   - Seguir instrucciones de posicionamiento

2. **Captura Secuencial:**
   - Capturar segmentos con 20-30% de solapamiento
   - Mover documento horizontal y verticalmente
   - Monitorear progreso en interfaz

3. **Stitching Automático:**
   - Presionar "Stitch Documents"
   - Esperar procesamiento automático
   - Descargar resultado final

## 🎯 Aplicaciones y Casos de Uso

### 1. Digitalización de Documentos Académicos
- Apuntes de clase manuscritos
- Exámenes y tareas
- Libros de texto y referencias

### 2. Procesamiento para OCR
- Preparación óptima para Mathpix
- Extracción de texto con Google Document AI
- Conversión a formatos editables

### 3. Prototipado de Hardware
- Simulación de displays embebidos
- Testing de interfaces SPI
- Desarrollo de sistemas de visualización

### 4. Investigación y Desarrollo
- Análisis de calidad de imagen
- Benchmarking de algoritmos de CV
- Calibración de sistemas ópticos

## 🚀 Características Destacadas

### ✅ Fortalezas del Sistema

1. **Tiempo Real:** Stream en vivo con transformaciones aplicadas
2. **Alta Resolución:** Captura a máxima resolución del sensor
3. **Automático:** Calibración una vez, uso repetido
4. **Multiplataforma:** Funciona en cualquier navegador moderno
5. **Extensible:** Arquitectura modular para nuevas funcionalidades

### 🔧 Optimizaciones Implementadas

1. **Gestión de Recursos:**
   - Pausa de stream durante captura de alta resolución
   - Liberación automática de cámara
   - Control de memoria en transformaciones

2. **Calidad de Imagen:**
   - CLAHE para mejor contraste
   - Preservación de información en transformaciones
   - Formato RGB565 para compatibilidad con displays

3. **Red y Transferencia:**
   - Múltiples métodos de descarga
   - Compresión JPEG ajustable
   - Headers CORS para desarrollo web

## 🛠️ Troubleshooting

### Problemas Comunes

**"No se pudo abrir la imagen"**
- Verificar que existe `autofocus_photo.jpg`
- Ejecutar `descargar_foto.py` primero

**"Permission denied" en SSH**
- Verificar credenciales del usuario `pi`
- Comprobar conectividad de red

**"Capture timed out"**
- Reiniciar servidor de cámara en Pi
- Verificar que la cámara no esté en uso por otro proceso

**Calidad de imagen deficiente**
- Recalibrar puntos de esquina
- Mejorar iluminación del documento
- Verificar que el documento esté completamente visible

### Logs y Debugging

**Ver estado del servidor:**
```bash
systemctl status camera-server  # Si está configurado como servicio
```

**Logs de aplicación:**
```bash
tail -f /var/log/camera-server.log
```

**Test de conectividad:**
```bash
ping raspberrypi.local
curl http://raspberrypi.local:5000/
```

## 🔮 Roadmap y Mejoras Futuras

### Funcionalidades Planificadas

1. **OCR Integrado:**
   - Procesamiento directo con Tesseract
   - Integración con Google Document AI
   - Extracción automática de texto

2. **Machine Learning:**
   - Detección automática de esquinas
   - Clasificación de tipos de documento
   - Mejora de calidad basada en IA

3. **Almacenamiento en la Nube:**
   - Sincronización automática con Drive
   - Backup incremental
   - Gestión de versiones

4. **Aplicación Móvil:**
   - Control remoto desde smartphone
   - Preview en tiempo real
   - Configuración inalámbrica

### Optimizaciones Técnicas

1. **Rendimiento:**
   - Caching de transformaciones
   - Procesamiento paralelo
   - Optimización de memoria

2. **Calidad:**
   - Algoritmos de stitching mejorados
   - Corrección automática de iluminación
   - Detección de documentos múltiples

3. **Usabilidad:**
   - Calibración automática
   - Interfaz táctil para Pi con pantalla
   - Modo batch para múltiples documentos

---

## 📄 Resumen Técnico

TermoCam representa una solución completa y modular para la digitalización de documentos, combinando hardware de bajo costo (Raspberry Pi) con software avanzado de visión por computador. El sistema destaca por su capacidad de procesamiento en tiempo real, alta calidad de imagen resultante, y facilidad de uso mediante interfaces web modernas.

La arquitectura modular permite tanto uso básico (captura simple) como workflows avanzados (stitching multi-segmento), mientras que el simulador NumWorks proporciona una herramienta adicional para el desarrollo de interfaces embebidas.

**Tecnologías Clave:** Python, OpenCV, Flask, libcamera, HTML5 Canvas, RGB565, SPI, HTTP streaming
