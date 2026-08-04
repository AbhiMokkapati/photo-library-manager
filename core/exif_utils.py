"""
exif_utils.py — pulls date/camera/GPS metadata out of an image using Pillow.
Falls back gracefully when EXIF is missing or malformed (common with
screenshots, WhatsApp exports, edited files, etc).
"""

from datetime import datetime
from pathlib import Path
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import pillow_heif

# registers .heic/.heif support with Pillow; must happen before any Image.open()
# call anywhere in the app, so it lives here since every image-opening code
# path already imports this module (directly or via indexer.py)
pillow_heif.register_heif_opener()


def load_image_bgr(image_path: Path):
    """
    Loads an image as a BGR numpy array (the format cv2/InsightFace/YOLO all
    expect). Falls back to Pillow (which, thanks to pillow_heif above, can
    decode HEIC/HEIF that cv2.imread cannot) if a direct cv2 read fails.
    Returns None if the file can't be read at all.
    """
    import cv2
    import numpy as np

    img = cv2.imread(str(image_path))
    if img is not None:
        return img
    try:
        with Image.open(image_path) as pil_img:
            rgb = np.array(pil_img.convert("RGB"))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def _get_exif_dict(img: Image.Image) -> dict:
    try:
        raw = img._getexif()
    except Exception:
        raw = None
    if not raw:
        return {}
    return {TAGS.get(k, k): v for k, v in raw.items()}


def _parse_gps(exif: dict):
    gps_info = exif.get("GPSInfo")
    if not gps_info:
        return None, None
    gps = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}

    def to_deg(value, ref):
        try:
            d, m, s = value
            deg = float(d) + float(m) / 60 + float(s) / 3600
            if ref in ("S", "W"):
                deg = -deg
            return deg
        except Exception:
            return None

    lat = to_deg(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef", "N")) if "GPSLatitude" in gps else None
    lon = to_deg(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef", "E")) if "GPSLongitude" in gps else None
    return lat, lon


def extract_metadata(file_path: Path) -> dict:
    """
    Returns a dict with keys: width, height, date_taken (ISO string or None),
    camera_make, camera_model, gps_lat, gps_lon.
    Never raises — on any failure returns best-effort defaults so indexing
    can continue past corrupt/unsupported files.
    """
    result = {
        "width": None,
        "height": None,
        "date_taken": None,
        "camera_make": None,
        "camera_model": None,
        "gps_lat": None,
        "gps_lon": None,
    }
    try:
        with Image.open(file_path) as img:
            result["width"], result["height"] = img.size
            exif = _get_exif_dict(img)

            date_str = exif.get("DateTimeOriginal") or exif.get("DateTime")
            if date_str:
                try:
                    dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                    result["date_taken"] = dt.isoformat()
                except ValueError:
                    pass

            result["camera_make"] = exif.get("Make")
            result["camera_model"] = exif.get("Model")
            result["gps_lat"], result["gps_lon"] = _parse_gps(exif)
    except Exception:
        pass

    if not result["date_taken"]:
        # fall back to filesystem mtime, filled in by the caller (indexer)
        # since this function only has the path, not a stat() call guarantee
        try:
            mtime = file_path.stat().st_mtime
            result["date_taken"] = datetime.fromtimestamp(mtime).isoformat()
        except Exception:
            pass

    return result
