#!/bin/bash
# Comprehensive Focus Test for IMX708AF
echo "🎯 Starting comprehensive focus test for IMX708AF..."

# Test 1: Different autofocus ranges
echo "📷 Testing autofocus ranges..."
libcamera-still -o focus_auto_normal.jpg --width 2304 --height 1296 --autofocus-mode auto --autofocus-range normal --timeout 3000 --nopreview
libcamera-still -o focus_auto_macro.jpg --width 2304 --height 1296 --autofocus-mode auto --autofocus-range macro --timeout 3000 --nopreview
libcamera-still -o focus_auto_full.jpg --width 2304 --height 1296 --autofocus-mode auto --autofocus-range full --timeout 3000 --nopreview

# Test 2: Continuous autofocus with different speeds
echo "🔄 Testing continuous autofocus..."
libcamera-still -o focus_continuous_fast.jpg --width 2304 --height 1296 --autofocus-mode continuous --autofocus-speed fast --timeout 3000 --nopreview
libcamera-still -o focus_continuous_normal.jpg --width 2304 --height 1296 --autofocus-mode continuous --autofocus-speed normal --timeout 3000 --nopreview

# Test 3: Manual focus positions for document scanning (close range)
echo "📏 Testing manual focus positions..."
for pos in 0.5 1.0 2.0 3.0 5.0 8.0; do
    echo "Testing lens position: $pos"
    libcamera-still -o "focus_manual_$pos.jpg" --width 2304 --height 1296 --lens-position $pos --timeout 2000 --nopreview
done

# Test 4: Autofocus with window (center focus)
echo "🎯 Testing autofocus with center window..."
libcamera-still -o focus_window_center.jpg --width 2304 --height 1296 --autofocus-mode auto --autofocus-range macro --autofocus-window 0.25,0.25,0.5,0.5 --timeout 3000 --nopreview

echo "✅ Focus tests complete! Check the following images:"
echo "📁 Auto focus: focus_auto_*.jpg"
echo "📁 Continuous: focus_continuous_*.jpg" 
echo "📁 Manual: focus_manual_*.jpg"
echo "📁 Window: focus_window_*.jpg"
echo ""
echo "🔍 Download and compare to find the sharpest image!"
