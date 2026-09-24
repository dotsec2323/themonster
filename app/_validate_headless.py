"""
Headless validation of the actual Stage 1 GUI (not a reimplementation).
Drives MainWindow exactly as a user would: load model -> load image -> run.
Saves a screenshot so the rendering can be visually confirmed.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from main import MainWindow

app = QApplication(sys.argv)
win = MainWindow()
win.resize(1200, 760)
win.show()

# Load model
model_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "best.pt")
win._load_model(model_path)

# Load image 1
img_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_images", "1.jpg")
import cv2
img = cv2.imread(img_path)
win.image_tab.current_image_path = img_path
win.image_tab.current_image_bgr = img
win.image_tab._show_frame(img)
win.image_tab.btn_run.setEnabled(True)

# Run detection
win.image_tab.on_run_detection()

app.processEvents()
pix = win.grab()
out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "stage1_validation_screenshot.png")
pix.save(out_path)
print("Saved screenshot:", out_path)
print("Detail text:")
print(win.image_tab.detail_text.toPlainText())
print(win.image_tab.lbl_count.text(), "|", win.image_tab.lbl_good.text(), "|", win.image_tab.lbl_reject.text())
print(win.image_tab.lbl_proc_time.text())
