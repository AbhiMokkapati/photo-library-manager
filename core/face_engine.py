"""
face_engine.py — wraps InsightFace for face detection + embedding extraction.

InsightFace's buffalo_l bundle gives us, in one pass over an image:
  - face bounding boxes
  - detection confidence
  - a 512-dim embedding vector per face, suitable for cosine-similarity
    comparison / clustering (this is what "recognition" is built on)

Model weights download once (~300MB) on first run and are cached locally,
so after that this runs fully offline.
"""

import logging
import numpy as np
import onnxruntime as ort
from pathlib import Path
from insightface.app import FaceAnalysis

from .exif_utils import load_image_bgr

logger = logging.getLogger(__name__)

_MIN_FACE_SIZE_PX = 40          # skip faces smaller than this (usually noise/false positives)
_MIN_DET_SCORE = 0.55           # skip low-confidence detections


def _gpu_available() -> bool:
    """True if this onnxruntime install actually has CUDA support (i.e. the
    user installed onnxruntime-gpu per the requirements.txt note) — checking
    get_available_providers() rather than just trying CUDA and hoping, since
    requesting CUDAExecutionProvider when it isn't installed either raises or
    silently falls back to CPU depending on onnxruntime version."""
    return "CUDAExecutionProvider" in ort.get_available_providers()


class FaceEngine:
    def __init__(self, use_gpu: bool = None, det_size=(640, 640)):
        if use_gpu is None:
            use_gpu = _gpu_available()
        elif use_gpu and not _gpu_available():
            logger.warning("GPU requested but CUDAExecutionProvider isn't available; falling back to CPU")
            use_gpu = False
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if use_gpu else ["CPUExecutionProvider"]
        self.app = FaceAnalysis(name="buffalo_l", providers=providers)
        # ctx_id=0 selects GPU 0 when a CUDA provider is available; ignored on CPU
        self.app.prepare(ctx_id=0 if use_gpu else -1, det_size=det_size)

    def detect_and_embed(self, image_path: Path, orig_size: tuple = None):
        """
        Returns a list of dicts: {bbox: [x1,y1,x2,y2], embedding: np.ndarray(512,), det_score: float}
        Returns [] if the file can't be read or no faces are found.

        `orig_size`, if given, is the (width, height) of the photo as reported by its
        metadata (what every other bbox consumer — thumbnail cropping, overlays — assumes
        bbox coordinates are relative to). RAW files are decoded at half resolution here
        (see exif_utils._load_raw_bgr) for detection speed, so without rescaling, bboxes
        would come out in that smaller decoded image's coordinate space instead — off by
        ~2x from where callers expect them.
        """
        img = load_image_bgr(image_path)
        if img is None:
            return []

        decoded_h, decoded_w = img.shape[:2]
        if orig_size and orig_size[0] and orig_size[1]:
            scale_x, scale_y = orig_size[0] / decoded_w, orig_size[1] / decoded_h
        else:
            scale_x = scale_y = 1.0

        faces = self.app.get(img)
        results = []
        for f in faces:
            x1, y1, x2, y2 = f.bbox
            x1, y1, x2, y2 = x1 * scale_x, y1 * scale_y, x2 * scale_x, y2 * scale_y
            w, h = x2 - x1, y2 - y1
            # sized against orig_size (true image pixels) so the threshold means the
            # same thing regardless of what resolution detection actually ran at
            if w < _MIN_FACE_SIZE_PX or h < _MIN_FACE_SIZE_PX:
                continue
            if f.det_score < _MIN_DET_SCORE:
                continue
            results.append({
                "bbox": [x1, y1, x2, y2],
                "embedding": f.normed_embedding.astype(np.float32),  # unit-norm, ready for cosine sim
                "det_score": float(f.det_score),
            })
        return results


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Both embeddings from FaceEngine are already unit-normalized, so this is just a dot product."""
    return float(np.dot(a, b))
