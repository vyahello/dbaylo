"""Normalize an uploaded image so the reader can actually see it — HEIC/HEIF → JPEG.

**Why this exists:** an iPhone shoots **HEIC** by default. Sent in Telegram *as a photo* it is
transcoded to JPEG by Telegram itself (the ``F.photo`` path is unaffected), but sent *as a file*
— which is the BETTER way to send a lab form, since it skips Telegram's compression and gives the
OCR a sharper image — the original HEIC bytes arrive untouched. The extraction reader (the
``claude`` CLI's Read tool) cannot render HEIC: it returns the raw bytes. So merely allow-listing
the extension would move a clear refusal into a silent bad read. The file must be TRANSCODED.

Deterministic and self-contained: no LLM, no DB, no network. Fail-soft — an undecodable file
yields ``None`` and the caller surfaces a friendly message rather than saving something unreadable.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

# Apple's container extensions. ``.heif`` is the generic one, ``.heic`` the HEVC-coded flavour an
# iPhone writes; both decode through the same libheif path.
HEIC_SUFFIXES = frozenset({".heic", ".heif"})

_JPEG_QUALITY = 92  # visually lossless for OCR while keeping the stored file reasonable

# pillow-heif registers a HEIF opener INTO Pillow, so `Image.open` handles .heic like any format.
# Imported defensively: if the wheel is somehow missing from a venv, the bot must still start and
# every other format must keep working — only HEIC degrades to the "can't read it" message.
try:  # pragma: no cover - exercised by the import itself
    import pillow_heif
    from PIL import Image, ImageOps

    pillow_heif.register_heif_opener()
    _HEIF_READY = True
except Exception:  # pragma: no cover - only on a broken/partial install
    _HEIF_READY = False
    logger.warning("pillow-heif unavailable — HEIC uploads cannot be converted")


def is_heic(suffix: str) -> bool:
    """Whether a file suffix names an Apple HEIC/HEIF image."""
    return suffix.lower() in HEIC_SUFFIXES


def heic_to_jpeg(data: bytes) -> bytes | None:
    """Transcode HEIC/HEIF bytes to JPEG, or ``None`` when they cannot be decoded.

    Applies the EXIF orientation (``exif_transpose``) — an iPhone records rotation in EXIF rather
    than rotating the pixels, so without this a lab form can reach the OCR sideways.
    """
    if not _HEIF_READY:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            upright = ImageOps.exif_transpose(image) or image
            buffer = io.BytesIO()
            upright.convert("RGB").save(buffer, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
            return buffer.getvalue()
    except Exception:  # a corrupt / truncated / non-image upload — never crash the handler
        logger.exception("HEIC -> JPEG conversion failed")
        return None


def normalize_upload(data: bytes, suffix: str) -> tuple[bytes, str] | None:
    """The bytes + suffix to STORE for an upload: a HEIC/HEIF is transcoded to JPEG, anything else
    passes through untouched. ``None`` only when a HEIC could not be decoded, so the caller can say
    so instead of saving a file the reader will not be able to read.

    The transcoded JPEG becomes the stored original (``LabReport.source_file``): it is the same
    image, and it is what every downstream surface — extraction, the 📄 Файл button, a later
    re-extraction — needs in order to work at all.
    """
    if not is_heic(suffix):
        return data, suffix
    converted = heic_to_jpeg(data)
    return (converted, ".jpg") if converted is not None else None
