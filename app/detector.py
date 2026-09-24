"""
detector.py
-----------
Thin wrapper around ONNX Runtime for YOLO inference.

WHY ONNX RUNTIME INSTEAD OF PYTORCH/ULTRALYTICS:
The original torch-based version repeatedly failed on the target Windows PC
with "WinError 1114: DLL initialization routine failed... c10.dll" -- a
known torch/Windows issue (commonly CPU instruction-set incompatibility,
e.g. missing AVX2, which many industrial/older PCs lack). Reinstalling
torch and the VC++ redistributable did not fix it.

ONNX Runtime is a much lighter, more portable inference engine with far
fewer native-DLL landmines than torch, and is the standard way to deploy
YOLO models to machines like this one (no GPU, older/lower-power CPU).
Testing confirmed it produces the same detections as the torch version
(matching boxes/confidences) while running ~8x faster on CPU.

This is the ONE place model loading + inference happens. Image test mode,
video test mode, and the live camera mode (later stages) all call through
this class, so behavior stays identical across all of them -- which matters
because Requirement #9 explicitly says video test mode must use "the same
detection and tracking logic that will later be used with the live camera".

Runs fully offline: onnxruntime loads the .onnx file from local disk, no
network calls are made during inference.
"""

import os
import time
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np


@dataclass
class Detection:
    class_id: int
    class_name: str
    confidence: float
    xyxy: tuple  # (x1, y1, x2, y2) in pixel coords
    role: str    # "GOOD" | "REJECT" | "UNKNOWN"
    track_id: Optional[int] = None  # populated from Stage 6 onward


@dataclass
class InferenceResult:
    detections: List[Detection]
    inference_ms: float
    image_shape: tuple  # (h, w)


class ModelLoadError(Exception):
    pass


def _letterbox(img, new_shape=640, color=(114, 114, 114)):
    h, w = img.shape[:2]
    r = min(new_shape / h, new_shape / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top = (new_shape - nh) // 2
    bottom = new_shape - nh - top
    left = (new_shape - nw) // 2
    right = new_shape - nw - left
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return padded, r, left, top


def _nms(boxes, scores, iou_thresh):
    """Simple per-class NMS. boxes: (N,4) xyxy, scores: (N,)."""
    idxs = scores.argsort()[::-1]
    keep = []
    while len(idxs) > 0:
        i = idxs[0]
        keep.append(i)
        if len(idxs) == 1:
            break
        rest = idxs[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        w = np.maximum(0, xx2 - xx1)
        h = np.maximum(0, yy2 - yy1)
        inter = w * h
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        iou = inter / (area_i + area_r - inter + 1e-9)
        idxs = rest[iou <= iou_thresh]
    return keep


class CanDetector:
    def __init__(self, config):
        self.cfg = config
        self.session = None
        self.model_path = None
        self.class_names = {}
        self.input_name = None
        self.input_size = 640
        self._active_provider = None

    def load(self, model_path: Optional[str] = None):
        """Load a YOLO .onnx model from local disk. Raises ModelLoadError on failure."""
        import onnxruntime as ort  # local import: keeps startup fast if unused

        path = model_path or self.cfg.model_path
        if not os.path.exists(path):
            raise ModelLoadError(f"Model file not found: '{path}'")

        # Try GPU acceleration first, then fall back to CPU cleanly if no GPU
        # execution provider is available or usable on this machine. Which
        # provider(s) even show up in get_available_providers() depends
        # entirely on which onnxruntime package is installed -- no code path
        # here needs to change per platform, only the requirements file used
        # to set the machine up:
        #   - Windows:            onnxruntime-directml -> DmlExecutionProvider
        #     (any DirectX 12 GPU -- NVIDIA/AMD/Intel -- no separate CUDA
        #     install needed)
        #   - Jetson Orin Nano:   a JetPack-version-matched onnxruntime-gpu
        #     wheel (see requirements-jetson.txt / JETSON.md -- NOT the
        #     generic PyPI onnxruntime-gpu, which has no Jetson build) ->
        #     TensorrtExecutionProvider / CUDAExecutionProvider
        #   - Raspberry Pi 5:     plain onnxruntime (see requirements-rpi.txt)
        #     -> no GPU provider at all, CPU only
        #
        # NOTE on Jetson specifically: get_available_providers() is a
        # compile-time list -- a provider can appear in it even if the
        # installed wheel wasn't built for this exact GPU architecture, so
        # its presence alone doesn't prove it works. What actually proves it
        # is this try/except below: if TensorrtExecutionProvider can't
        # really run, session creation raises and we move to the next
        # candidate, or (if creation succeeds but silently falls back
        # internally) self.session.get_providers()[0] reports the provider
        # ACTUALLY in use, not just the one requested -- which is what
        # active_device_label() below reports, so the UI never shows a GPU
        # label unless a GPU is genuinely running inference.
        self._active_provider = None
        available = ort.get_available_providers()
        provider_attempts = []
        if "TensorrtExecutionProvider" in available:
            provider_attempts.append(["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"])
        if "CUDAExecutionProvider" in available:
            provider_attempts.append(["CUDAExecutionProvider", "CPUExecutionProvider"])
        if "DmlExecutionProvider" in available:
            provider_attempts.append(["DmlExecutionProvider", "CPUExecutionProvider"])
        provider_attempts.append(["CPUExecutionProvider"])

        last_error = None
        for providers in provider_attempts:
            try:
                self.session = ort.InferenceSession(path, providers=providers)
                self._active_provider = self.session.get_providers()[0]
                break
            except Exception as e:
                last_error = e
                self.session = None
        if self.session is None:
            raise ModelLoadError(f"Failed to load model at '{path}': {last_error}")

        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.input_size = inp.shape[2] if isinstance(inp.shape[2], int) else 640

        self.class_names = self._load_class_names(path)
        self.model_path = path
        return self.class_names

    def _load_class_names(self, model_path: str) -> dict:
        """
        ONNX files don't carry Ultralytics' class-name metadata the way .pt
        files do. We read it from a sidecar file if present (classes.txt,
        one name per line, matching export order), otherwise fall back to
        the names this project's model is known to use.
        """
        sidecar = os.path.splitext(model_path)[0] + ".classes.txt"
        if os.path.exists(sidecar):
            with open(sidecar, "r", encoding="utf-8") as f:
                names = [line.strip() for line in f if line.strip()]
            if names:
                return {i: n for i, n in enumerate(names)}
        return {0: "fall can", 1: "Good can"}

    def is_loaded(self) -> bool:
        return self.session is not None

    def _role_for(self, class_name: str) -> str:
        if class_name in self.cfg.good_class_names:
            return "GOOD"
        if class_name in self.cfg.reject_class_names:
            return "REJECT"
        return "UNKNOWN"

    def predict(self, image: np.ndarray, conf: Optional[float] = None,
                iou: Optional[float] = None, imgsz: Optional[int] = None) -> InferenceResult:
        """
        Run detection-only inference (no tracking) on a single image/frame.
        Used by Stage 2/3 (image test) and as the building block for
        Stage 4+ (video/camera), before tracking is layered on top.
        """
        if self.session is None:
            raise ModelLoadError("Model not loaded. Call load() first.")

        conf = self.cfg.confidence if conf is None else conf
        iou = self.cfg.iou if iou is None else iou
        size = imgsz if imgsz is not None else self.input_size

        padded, r, dx, dy = _letterbox(image, size)
        blob = padded[:, :, ::-1].astype(np.float32) / 255.0
        blob = np.ascontiguousarray(blob.transpose(2, 0, 1)[None])

        t0 = time.perf_counter()
        outputs = self.session.run(None, {self.input_name: blob})
        inference_ms = (time.perf_counter() - t0) * 1000.0

        detections = self._postprocess(outputs[0], r, dx, dy, image.shape, conf, iou)
        h, w = image.shape[:2]
        return InferenceResult(detections=detections, inference_ms=inference_ms, image_shape=(h, w))

    def _postprocess(self, output, r, dx, dy, orig_shape, conf_thresh, iou_thresh) -> List[Detection]:
        pred = output[0].T  # (num_anchors, 4+num_classes)
        boxes_cxcywh = pred[:, :4]
        class_scores = pred[:, 4:]
        class_ids = class_scores.argmax(axis=1)
        confs = class_scores.max(axis=1)

        mask = confs >= conf_thresh
        boxes_cxcywh = boxes_cxcywh[mask]
        class_ids = class_ids[mask]
        confs = confs[mask]

        if len(confs) == 0:
            return []

        cx, cy, w, h = boxes_cxcywh[:, 0], boxes_cxcywh[:, 1], boxes_cxcywh[:, 2], boxes_cxcywh[:, 3]
        x1 = cx - w / 2
        y1 = cy - h / 2
        x2 = cx + w / 2
        y2 = cy + h / 2
        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

        boxes_xyxy[:, [0, 2]] -= dx
        boxes_xyxy[:, [1, 3]] -= dy
        boxes_xyxy /= r
        oh, ow = orig_shape[:2]
        boxes_xyxy[:, [0, 2]] = boxes_xyxy[:, [0, 2]].clip(0, ow)
        boxes_xyxy[:, [1, 3]] = boxes_xyxy[:, [1, 3]].clip(0, oh)

        detections = []
        for c in np.unique(class_ids):
            m = class_ids == c
            keep = _nms(boxes_xyxy[m], confs[m], iou_thresh)
            b = boxes_xyxy[m][keep]
            s = confs[m][keep]
            cls_name = self.class_names.get(int(c), str(int(c)))
            for bb, ss in zip(b, s):
                detections.append(Detection(
                    class_id=int(c),
                    class_name=cls_name,
                    confidence=float(ss),
                    xyxy=tuple(float(v) for v in bb.tolist()),
                    role=self._role_for(cls_name),
                ))
        return detections

    def gpu_available(self) -> bool:
        return self._active_provider in (
            "DmlExecutionProvider", "TensorrtExecutionProvider", "CUDAExecutionProvider",
        )

    def active_device_label(self) -> str:
        return {
            "DmlExecutionProvider": "GPU (DirectML)",
            "TensorrtExecutionProvider": "GPU (TensorRT)",
            "CUDAExecutionProvider": "GPU (CUDA)",
            "CPUExecutionProvider": "CPU",
        }.get(self._active_provider, "unknown")
