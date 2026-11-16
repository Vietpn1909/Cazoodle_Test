"""Builders for raw OpenAlex work records used across pipeline tests."""

from typing import Any

UIUC = "I157725225"
UIUC_INST = {
    "id": "https://openalex.org/I157725225",
    "display_name": "University of Illinois Urbana-Champaign",
    "country_code": "US",
    "type": "education",
    "lineage": ["https://openalex.org/I157725225"],
}
BECKMAN = {  # sub-unit whose lineage includes UIUC
    "id": "https://openalex.org/I999",
    "display_name": "Beckman Institute",
    "country_code": "US",
    "type": "facility",
    "lineage": ["https://openalex.org/I999", "https://openalex.org/I157725225"],
}
MIT = {
    "id": "https://openalex.org/I63966007",
    "display_name": "MIT",
    "country_code": "US",
    "type": "education",
    "lineage": ["https://openalex.org/I63966007"],
}
TOPIC = {
    "id": "https://openalex.org/T10036",
    "display_name": "Advanced Neural Network Applications",
    "score": 0.99,
    "subfield": {"id": "https://openalex.org/subfields/1707", "display_name": "Computer Vision"},
    "field": {"id": "https://openalex.org/fields/17", "display_name": "Computer Science"},
    "domain": {"id": "https://openalex.org/domains/3", "display_name": "Physical Sciences"},
}


def raw_work(**overrides: Any) -> dict[str, Any]:
    w: dict[str, Any] = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1000/ABC",
        "title": "Vision models for <i>medical</i> imaging",
        "publication_date": "2025-06-01",
        "publication_year": 2025,
        "type": "article",
        "language": "en",
        "primary_location": {"source": {"display_name": "Nature Medicine"}},
        "open_access": {"is_oa": True, "oa_url": "https://example.org/pdf"},
        "cited_by_count": 12,
        "fwci": 2.5,
        "citation_normalized_percentile": {"value": 0.9},
        "primary_topic": TOPIC,
        "topics": [TOPIC],
        "keywords": [{"display_name": "medical imaging"}],
        "sustainable_development_goals": [
            {"id": "https://metadata.un.org/sdg/3", "display_name": "Good health", "score": 0.8}
        ],
        "abstract_inverted_index": {"Deep": [0], "learning": [1], "helps": [2]},
        "is_retracted": False,
        "is_paratext": False,
        "authorships": [
            {
                "author_position": "first",
                "author": {
                    "id": "https://openalex.org/A1",
                    "display_name": "Ann Lee",
                    "orcid": "https://orcid.org/0000-0001",
                },
                "institutions": [UIUC_INST, BECKMAN],
                "raw_affiliation_strings": ["University of Illinois Urbana-Champaign"],
                "is_corresponding": True,
            },
            {
                "author_position": "last",
                "author": {"id": "https://openalex.org/A2", "display_name": "Bob Ray"},
                "institutions": [MIT],
                "raw_affiliation_strings": ["MIT, Cambridge, MA"],
            },
            {  # duplicate authorship entry (happens in OpenAlex)
                "author_position": "middle",
                "author": {"id": "https://openalex.org/A1", "display_name": "Ann Lee"},
                "institutions": [UIUC_INST],
                "raw_affiliation_strings": [],
            },
        ],
    }
    w.update(overrides)
    return w
