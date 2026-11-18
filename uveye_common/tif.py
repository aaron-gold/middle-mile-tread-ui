"""Read UVeye camera frames with TRUE pixel values - one implementation for every tool.

Why this exists (KB 00_knowledge\\middle-mile\\middle-mile-tif-opencv-trap.md):
  * Middle Mile Artemis .tif files carry no PhotometricInterpretation tag (TIFF 262).
  * cv2.imread refuses them; Pillow opens them but falls back to WhiteIsZero, so it returns
    (dtype max - stored sample): a photographic negative.
  * The stored samples are min-is-black, so  true = dtype max - Pillow output.  For uint8: 255 - x.
  * A TIFF that DOES declare tag 262 is left alone (the writer meant it); non-TIFF files
    (the OCR .jpg twin, .png) are never inverted.

Replaces three copies that agreed on all real data (golden check 2026-10-18, 52 frames):
  mm_viewer.io_tif._load_via_pil, MM_image_quality mm_iq_core.read_frame, mm_iq_fod.read_true.
"""
import numpy as np

TIFF_TAG_PHOTOMETRIC = 262      # PhotometricInterpretation


def needs_inversion(im):
    """True when Pillow will have applied its WhiteIsZero fallback: a TIFF without tag 262."""
    try:
        return im.format == "TIFF" and TIFF_TAG_PHOTOMETRIC not in im.tag_v2
    except Exception:
        return False


def to_true(a):
    """dtype max - a, for a 2-D unsigned-integer frame; anything else is returned unchanged."""
    if a is None or a.ndim != 2 or not np.issubdtype(a.dtype, np.integer):
        return a
    info = np.iinfo(a.dtype)
    if info.min < 0:
        return a
    return np.ascontiguousarray(info.max - a)


def read_true(path, mode=None, keep_image=False):
    """Decode one image file to true pixel values.

    mode        None -> native Pillow array (dtype and channels as stored; colour stays RGB).
                "L"  -> 8-bit grey first (what the IQ toolkit measures on).
    keep_image  False -> return the array.  True -> return (array, inverted, pil_image); the
                caller owns pil_image (still open, e.g. for JPEG quant tables) and closes it.
    Raises whatever Pillow raises for an unreadable file.
    """
    from PIL import Image
    im = Image.open(path)
    try:
        inverted = needs_inversion(im)
        a = np.array(im.convert(mode)) if mode else np.array(im)
        a = np.ascontiguousarray(a)
        if inverted:
            a = to_true(a)
    except Exception:
        im.close()
        raise
    if keep_image:
        return a, inverted, im
    im.close()
    return a
