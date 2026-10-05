"""
exif_utils.py — pulls date/camera/GPS metadata out of an image using Pillow,
and decodes pixels for face/object detection.

Camera RAW files (.CR2, .NEF, .ARW, .DNG, ...) need separate handling:
Pillow can't open them at all, so we go through rawpy (libraw) for pixel
data and exifread (which understands the TIFF-based structure RAW formats
embed their EXIF in) for metadata.

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

# Camera RAW formats — Pillow/cv2 can't decode these directly; rawpy (libraw)
# and exifread are used instead. Covers the common DSLR/mirrorless makers.
RAW_EXTENSIONS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw"}


def is_raw(path) -> bool:
    return Path(path).suffix.lower() in RAW_EXTENSIONS


def load_image_bgr(image_path: Path):
    """
    Loads an image as a BGR numpy array (the format cv2/InsightFace/YOLO all
    expect). Falls back to Pillow (which, thanks to pillow_heif above, can
    decode HEIC/HEIF that cv2.imread cannot) if a direct cv2 read fails.
    RAW files are decoded via rawpy instead, since neither cv2 nor Pillow can
    read them at all. Returns None if the file can't be read at all.
    """
    import cv2
    import numpy as np

    if is_raw(image_path):
        return _load_raw_bgr(image_path)

    img = cv2.imread(str(image_path))
    if img is not None:
        return img
    try:
        with Image.open(image_path) as pil_img:
            rgb = np.array(pil_img.convert("RGB"))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def _load_raw_bgr(image_path: Path):
    import cv2
    import rawpy

    try:
        with rawpy.imread(str(image_path)) as raw:
            # half_size: full demosaic isn't needed for face/object detection
            # (which downsamples to ~640px anyway) or thumbnails, and it's
            # several times faster on 24-45MP RAW files.
            # auto_bright (rawpy default) is left on: RAW sensor data needs
            # brightness/gamma scaling to render visibly — without it most
            # DSLR RAWs come out looking almost black.
            rgb = raw.postprocess(use_camera_wb=True, half_size=True, output_bps=8)
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


def _exifread_gps(tags: dict, ref_key: str, val_key: str):
    ref = tags.get(ref_key)
    val = tags.get(val_key)
    if ref is None or val is None:
        return None
    try:
        d, m, s = val.values
        deg = float(d) + float(m) / 60 + float(s) / 3600
        if str(ref) in ("S", "W"):
            deg = -deg
        return deg
    except Exception:
        return None


def _extract_metadata_raw(file_path: Path, result: dict):
    """Fills `result` in place using exifread (metadata) and rawpy (pixel
    dimensions, as a fallback when exifread doesn't have them)."""
    try:
        import exifread
        with open(file_path, "rb") as f:
            tags = exifread.process_file(f, details=False)

        width_tag = tags.get("EXIF ExifImageWidth") or tags.get("Image ImageWidth")
        height_tag = tags.get("EXIF ExifImageLength") or tags.get("Image ImageLength")
        if width_tag is not None and height_tag is not None:
            try:
                result["width"], result["height"] = int(str(width_tag)), int(str(height_tag))
            except ValueError:
                pass

        date_str = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
        if date_str is not None:
            try:
                dt = datetime.strptime(str(date_str), "%Y:%m:%d %H:%M:%S")
                result["date_taken"] = dt.isoformat()
            except ValueError:
                pass

        if tags.get("Image Make") is not None:
            result["camera_make"] = str(tags["Image Make"]).strip()
        if tags.get("Image Model") is not None:
            result["camera_model"] = str(tags["Image Model"]).strip()

        result["gps_lat"] = _exifread_gps(tags, "GPS GPSLatitudeRef", "GPS GPSLatitude")
        result["gps_lon"] = _exifread_gps(tags, "GPS GPSLongitudeRef", "GPS GPSLongitude")
    except Exception:
        pass

    if not result["width"] or not result["height"]:
        try:
            import rawpy
            with rawpy.imread(str(file_path)) as raw:
                result["width"], result["height"] = raw.sizes.width, raw.sizes.height
        except Exception:
            pass


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

    if is_raw(file_path):
        _extract_metadata_raw(file_path, result)
    else:
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
