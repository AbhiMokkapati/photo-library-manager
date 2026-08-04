"""
object_engine.py — general-purpose object detection via a YOLOv8n ONNX model,
run through onnxruntime (the same runtime already used for face embeddings in
face_engine.py, so no extra heavyweight dependency like ultralytics/torch is
needed at runtime).

Model setup (one-time, not needed at runtime after this):
    pip install ultralytics
    yolo export model=yolov8n.pt format=onnx imgsz=640
Then move the resulting yolov8n.onnx to data/models/yolov8n.onnx (relative to
the photo_manager package root). After that this runs fully offline, same as
FaceEngine's InsightFace models.
"""

import json
import cv2
import numpy as np
import onnxruntime as ort
from pathlib import Path

from .paths import APP_DIR
from .exif_utils import load_image_bgr

DEFAULT_MODEL_PATH = APP_DIR / "data" / "models" / "yolov8n.onnx"
INPUT_SIZE = 640

# Standard 80 COCO class names, in the order YOLOv8 was trained on.
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
    "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]

_MIN_CONFIDENCE = 0.35
_NMS_IOU_THRESHOLD = 0.45


class ObjectEngine:
    def __init__(self, model_path: Path = DEFAULT_MODEL_PATH):
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Object detection model not found at {self.model_path}.\n"
                "One-time setup: `pip install ultralytics` then "
                "`yolo export model=yolov8n.pt format=onnx imgsz=640`, and move "
                f"the resulting yolov8n.onnx to {self.model_path}."
            )
        self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def _letterbox(self, img: np.ndarray):
        h, w = img.shape[:2]
        scale = min(INPUT_SIZE / h, INPUT_SIZE / w)
        new_h, new_w = int(round(h * scale)), int(round(w * scale))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        pad_h, pad_w = INPUT_SIZE - new_h, INPUT_SIZE - new_w
        top, bottom = pad_h // 2, pad_h - pad_h // 2
        left, right = pad_w // 2, pad_w - pad_w // 2
        padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
        return padded, scale, left, top

    def detect(self, image_path: Path, conf_threshold: float = _MIN_CONFIDENCE,
               iou_threshold: float = _NMS_IOU_THRESHOLD):
        """
        Returns a list of dicts: {label: str, confidence: float, bbox: [x1,y1,x2,y2]}
        in original-image pixel coordinates. Returns [] if the file can't be read
        or nothing is detected above threshold.
        """
        img = load_image_bgr(image_path)
        if img is None:
            return []

        padded, scale, pad_left, pad_top = self._letterbox(img)
        blob = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[None, ...]  # NCHW

        output = self.session.run(None, {self.input_name: blob})[0]  # (1, 84, 8400)
        predictions = np.squeeze(output).T  # (8400, 84)

        boxes_xywh = predictions[:, :4]
        class_scores = predictions[:, 4:]
        class_ids = np.argmax(class_scores, axis=1)
        confidences = class_scores[np.arange(len(class_scores)), class_ids]

        keep = confidences >= conf_threshold
        if not np.any(keep):
            return []
        boxes_xywh = boxes_xywh[keep]
        class_ids = class_ids[keep]
        confidences = confidences[keep]

        # xywh (center-based, in padded-image space) -> xyxy in original-image space
        cx, cy, w, h = boxes_xywh[:, 0], boxes_xywh[:, 1], boxes_xywh[:, 2], boxes_xywh[:, 3]
        x1 = (cx - w / 2 - pad_left) / scale
        y1 = (cy - h / 2 - pad_top) / scale
        x2 = (cx + w / 2 - pad_left) / scale
        y2 = (cy + h / 2 - pad_top) / scale
        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

        nms_boxes = [[float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1])] for b in boxes_xyxy]
        indices = cv2.dnn.NMSBoxes(nms_boxes, confidences.tolist(), conf_threshold, iou_threshold)
        if len(indices) == 0:
            return []
        indices = np.array(indices).flatten()

        results = []
        for i in indices:
            label = COCO_CLASSES[class_ids[i]] if class_ids[i] < len(COCO_CLASSES) else str(class_ids[i])
            results.append({
                "label": label,
                "confidence": float(confidences[i]),
                "bbox": [float(v) for v in boxes_xyxy[i]],
            })
        return results
