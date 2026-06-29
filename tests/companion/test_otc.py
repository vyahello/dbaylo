"""The OTC-amenable allow-list — one half of the OTC gate (the other is triage MONITOR).

Conservative by design: clearly minor self-limiting complaints qualify; anything serious / triage
territory (fever, kidney-flank, bleeding) must NOT.
"""

from __future__ import annotations

from dbaylo.companion import otc


def test_minor_complaints_are_amenable() -> None:
    assert otc.otc_amenable("болить голова")
    assert otc.otc_amenable("які таблетки порадиш від болі в голові?")
    assert otc.otc_amenable("у мене нежить і трохи болить горло")
    assert otc.otc_amenable("печія після їжі")
    assert otc.otc_amenable("застуда, закладений ніс")
    assert otc.otc_amenable("ломота в тілі")


def test_serious_or_triage_complaints_are_not_amenable() -> None:
    assert not otc.otc_amenable("температура і озноб")
    assert not otc.otc_amenable("кров у сечі")
    assert not otc.otc_amenable("сильний біль у грудях")
    assert not otc.otc_amenable("болить поперек, вийшов камінь")
    assert not otc.otc_amenable("як справи?")
