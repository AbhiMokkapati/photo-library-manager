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

import numpy as np
from pathlib import Path
from insightface.app import FaceAnalysis

from .exif_utils import load_image_bgr

_MIN_FACE_SIZE_PX = 40          # skip faces smaller than this (usually noise/false positives)
_MIN_DET_SCORE = 0.55           # skip low-confidence detections


class FaceEngine:
    def __init__(self, use_gpu: bool = False, det_size=(640, 640)):
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if use_gpu else ["CPUExecutionProvider"]
        self.app = FaceAnalysis(name="buffalo_l", providers=providers)
        # ctx_id=0 selects GPU 0 when a CUDA provider is available; ignored on CPU
        self.app.prepare(ctx_id=0 if use_gpu else -1, det_size=det_size)

    def detect_and_embed(self, image_path: Path):
        """
        Returns a list of dicts: {bbox: [x1,y1,x2,y2], embedding: np.ndarray(512,), det_score: float}
        Returns [] if the file can't be read or no faces are found.
        """
        img = load_image_bgr(image_path)
        if img is None:
            return []

        faces = self.app.get(img)
        results = []
        for f in faces:
            x1, y1, x2, y2 = f.bbox
            w, h = x2 - x1, y2 - y1
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
