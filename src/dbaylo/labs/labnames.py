"""Canonicalize a lab's brand spelling.

Extraction reads the brand off the page, and the same lab can come back spelled two
ways across reports (``Сінево`` vs ``Синево``) — which then fragments the history list
into what looks like two different labs (e.g. ``Синево`` vs the canonical ``Сінево`` the
Synevo network actually prints). This pure, idempotent map fixes the few brands
we actually see; it is applied both on write (so new reports are stored canonical) and on
read (so already-stored reports render consistently without a data migration). The optional
", city" suffix is preserved untouched — only the brand token is canonicalized.

Pure: no LLM/DB/network. Extend ``_LAB_CANON`` as new labs appear.
"""

from __future__ import annotations

# Lowercased brand variant -> canonical Ukrainian spelling. The Synevo network prints
# "Сінево" (з "і") on its reports; extraction often mis-reads it as "Синево".
_LAB_CANON: dict[str, str] = {
    "сінево": "Сінево",
    "синево": "Сінево",
    "synevo": "Сінево",
    "діла": "ДІЛА",
    "dila": "ДІЛА",
    "інвітро": "Інвітро",
    "invitro": "Інвітро",
    "ескулаб": "Ескулаб",
    "esculab": "Ескулаб",
}


def normalize_lab(lab: str | None) -> str | None:
    """Return ``lab`` with a known brand canonicalized, keeping any ", city" suffix.

    A ``None``/blank value or an unknown brand passes through unchanged.
    """
    if not lab:
        return lab
    brand, sep, rest = lab.partition(",")
    canon = _LAB_CANON.get(brand.strip().casefold())
    if canon is None:
        return lab
    return f"{canon}{sep}{rest}" if sep else canon
