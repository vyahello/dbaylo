"""Upload normalization (``labs.images``) — iPhone HEIC/HEIF is transcoded to JPEG.

The extraction reader cannot render HEIC (it gets raw bytes), so a HEIC that is merely
allow-listed would become a SILENT bad read. These tests lock the transcode, the pass-through for
every other format, and the fail-soft path. Deterministic: the HEIC fixtures are synthesized here,
so no binary blobs live in the repo.
"""

from __future__ import annotations

import io

import pytest

from dbaylo.labs.images import HEIC_SUFFIXES, heic_to_jpeg, is_heic, normalize_upload

_JPEG_MAGIC = b"\xff\xd8\xff"

pillow_heif = pytest.importorskip("pillow_heif")
PIL_Image = pytest.importorskip("PIL.Image")


def _heic_bytes(size: tuple[int, int] = (40, 20), *, exif: bytes | None = None) -> bytes:
    """A real, small HEIF file (what an iPhone sends when the photo goes as a FILE)."""
    image = PIL_Image.new("RGB", size, "white")
    buffer = io.BytesIO()
    if exif is not None:
        image.save(buffer, format="HEIF", exif=exif)
    else:
        image.save(buffer, format="HEIF")
    return buffer.getvalue()


@pytest.mark.parametrize("suffix", [".heic", ".HEIC", ".heif", ".HeIf"])
def test_is_heic_is_case_insensitive(suffix) -> None:
    assert is_heic(suffix) is True


@pytest.mark.parametrize("suffix", [".jpg", ".jpeg", ".png", ".pdf", "", ".heicx"])
def test_is_heic_rejects_everything_else(suffix) -> None:
    assert is_heic(suffix) is False


def test_heic_is_transcoded_to_a_real_jpeg() -> None:
    result = normalize_upload(_heic_bytes(), ".heic")
    assert result is not None
    data, suffix = result
    assert suffix == ".jpg"  # the STORED file is the readable JPEG
    assert data.startswith(_JPEG_MAGIC)  # ...and it really is a JPEG, not renamed HEIC
    assert PIL_Image.open(io.BytesIO(data)).size == (40, 20)  # the image survived intact


@pytest.mark.parametrize("suffix", [".jpg", ".png", ".pdf"])
def test_other_formats_pass_through_byte_for_byte(suffix) -> None:
    # A JPEG/PNG/PDF must never be re-encoded — it is already readable and re-encoding would only
    # lose detail the OCR needs.
    payload = b"original-bytes"
    assert normalize_upload(payload, suffix) == (payload, suffix)


def test_an_undecodable_heic_fails_soft() -> None:
    # A corrupt / truncated upload yields None so the caller can say "надішли як фото" instead of
    # saving a file the reader will never be able to read.
    assert heic_to_jpeg(b"definitely not an image") is None
    assert normalize_upload(b"definitely not an image", ".heic") is None


def test_exif_orientation_is_applied_so_a_form_is_not_sideways() -> None:
    # An iPhone records rotation in EXIF rather than rotating pixels. Without exif_transpose a lab
    # form can reach the OCR sideways, so a tagged image must come out with its axes swapped.
    exif = PIL_Image.Exif()
    exif[274] = 6  # Orientation = rotate 90° CW
    tagged = _heic_bytes((40, 20), exif=exif.tobytes())
    result = normalize_upload(tagged, ".heic")
    assert result is not None
    assert PIL_Image.open(io.BytesIO(result[0])).size == (20, 40)  # transposed, not 40x20


def test_supported_suffixes_include_heic() -> None:
    # The allow-list and the transcoder must agree: every HEIC suffix we accept can be converted.
    from dbaylo.labs.intake import SUPPORTED_SUFFIXES

    assert HEIC_SUFFIXES <= SUPPORTED_SUFFIXES
