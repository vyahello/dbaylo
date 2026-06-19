"""Navigator output guard: superlative reject, label net, named-drug boundary."""

from __future__ import annotations

import pytest

from dbaylo.locale import REVIEWS_NOT_OUTCOMES
from dbaylo.navigator.guard import (
    assert_provider_labeled,
    assert_safe_navigator_output,
    contains_superlative_recommendation,
    is_drug_recommendation_request,
)


@pytest.mark.parametrize(
    "text",
    [
        "Найкращий хірург міста — оперуйтесь у нього.",
        "Ця клініка №1 за результатами лікування.",
        "Гарантований результат операції.",
        "Доктор Іваненко точно вилікує вашу хворобу.",
        "Оперуйтесь у цьому центрі.",
    ],
)
def test_superlative_provider_recommendations_are_rejected(text: str) -> None:
    assert contains_superlative_recommendation(text) is not None
    with pytest.raises(ValueError, match="best"):
        assert_safe_navigator_output(text)


@pytest.mark.parametrize(
    "text",
    [
        "Ось список хірургів у твоєму місті.",
        "Найкраще пити воду вранці.",  # superlative, but no provider noun
        "Ця клініка має договір із НСЗУ.",
        "Відгуки пацієнтів переважно позитивні.",
    ],
)
def test_neutral_provider_copy_passes(text: str) -> None:
    assert contains_superlative_recommendation(text) is None
    assert assert_safe_navigator_output(text) == text


def test_navigator_guard_keeps_reassurance_and_diet_rails() -> None:
    with pytest.raises(ValueError, match="reassurance"):
        assert_safe_navigator_output("Все добре, лікар не потрібен.")
    with pytest.raises(ValueError, match="restrictive-diet"):
        assert_safe_navigator_output("Тримай 1000 ккал на день.")


def test_drug_product_names_are_not_dose_directives() -> None:
    """Price listings cite product names (with dose-form tokens) — not directives."""
    listing = "• Парацетамол №10 таблетки — 45.50 грн\n• Ібупрофен 400 мг №20 капсули — 78.00 грн"
    assert assert_safe_navigator_output(listing) == listing


def test_assert_provider_labeled() -> None:
    with pytest.raises(ValueError, match="reviews, not outcomes"):
        assert_provider_labeled("Доктор Іваненко, кардіолог, Київ")
    labeled = f"Доктор Іваненко, кардіолог, Київ\n\n{REVIEWS_NOT_OUTCOMES}"
    assert assert_provider_labeled(labeled) == labeled


@pytest.mark.parametrize(
    "text",
    ["ліки для нирок", "таблетки від тиску", "що випити від голови", "порадь ліки від застуди"],
)
def test_recommendation_requests_are_detected(text: str) -> None:
    assert is_drug_recommendation_request(text)


@pytest.mark.parametrize("text", ["парацетамол", "Но-шпа 40 мг", "аспірин кардіо"])
def test_named_drugs_are_not_recommendation_requests(text: str) -> None:
    assert not is_drug_recommendation_request(text)
