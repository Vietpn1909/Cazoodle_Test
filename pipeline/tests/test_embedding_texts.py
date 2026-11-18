import numpy as np
import pytest

from pipeline.embedding.model import to_pgvector
from pipeline.embedding.texts import (
    MAX_FACULTY_KEYWORDS,
    MAX_FACULTY_TOPICS,
    MAX_WORK_KEYWORDS,
    WORK_ABSTRACT_CHARS,
    faculty_text,
    text_hash,
    work_text,
)


def test_work_text_leads_with_title_then_topic_keywords_abstract() -> None:
    t = work_text(
        "Vision models for medical imaging.",
        "We study deep learning.",
        topic="Medical Image Analysis",
        subfield="Radiology",
        field="Medicine",
        keywords=["medical imaging", "Medical Imaging", "deep learning"],
    )
    assert t == (
        "Vision models for medical imaging. Medical Image Analysis (Radiology; Medicine). "
        "Keywords: medical imaging, deep learning. We study deep learning."
    )


def test_work_text_handles_missing_parts() -> None:
    assert work_text("Title", None) == "Title."
    assert work_text("Title", "  ", topic="T") == "Title. T."
    assert work_text("Title", "Abs", subfield="Sub", field="F") == "Title. Sub; F. Abs"


def test_work_text_limits_keywords_and_truncates_abstract_at_a_word() -> None:
    kws = [f"kw{i}" for i in range(MAX_WORK_KEYWORDS + 5)]
    long_abstract = ("word " * 1000).strip()
    t = work_text("T", long_abstract, keywords=kws)
    assert f"kw{MAX_WORK_KEYWORDS - 1}" in t and f"kw{MAX_WORK_KEYWORDS}" not in t
    abstract_part = t.split("kw7. ", 1)[1]
    assert abstract_part.endswith("...") and len(abstract_part) <= WORK_ABSTRACT_CHARS + 3
    assert not abstract_part.removesuffix("...").endswith("wor")  # cut on a word boundary


def test_faculty_text_orders_top_keywords_first_and_dedupes() -> None:
    t = faculty_text(
        ["computer vision", "medical imaging"],
        ["image segmentation", "Computer Vision", "deep learning"],
        ["Medical Image Analysis", "Medical image analysis", "Deep Learning Methods"],
    )
    assert t == (
        "Research interests: computer vision, medical imaging, image segmentation, "
        "deep learning. Publication topics: Medical Image Analysis; Deep Learning Methods."
    )


def test_faculty_text_caps_and_empty() -> None:
    many_k = [f"k{i}" for i in range(MAX_FACULTY_KEYWORDS + 10)]
    many_t = [f"t{i}" for i in range(MAX_FACULTY_TOPICS + 10)]
    t = faculty_text([], many_k, many_t)
    assert t.count(",") == MAX_FACULTY_KEYWORDS - 1 and t.count(";") == MAX_FACULTY_TOPICS - 1
    assert faculty_text(None, None, None) == ""
    assert faculty_text([], [], ["Only Topic"]) == "Publication topics: Only Topic."


def test_text_hash_is_stable_and_sensitive() -> None:
    assert text_hash("a") == text_hash("a") and len(text_hash("a")) == 16
    assert text_hash("a") != text_hash("b")


def test_to_pgvector_literal() -> None:
    assert to_pgvector([0.5, -1.0, 1e-9]) == "[0.5,-1,1e-09]"
    v = np.linspace(-1, 1, 384, dtype=np.float32)
    parsed = np.array([float(x) for x in to_pgvector(v).strip("[]").split(",")], dtype=np.float32)
    assert np.allclose(parsed, v, atol=1e-6)


@pytest.mark.parametrize("bad", ["", " "])
def test_blank_title_still_produces_a_string(bad: str) -> None:
    assert isinstance(work_text(bad, None), str)
