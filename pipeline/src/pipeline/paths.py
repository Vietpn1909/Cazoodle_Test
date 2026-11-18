"""Filesystem layout for pipeline artifacts (all under the gitignored `data/`)."""

from app.core.config import REPO_ROOT

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = PROCESSED_DIR / "reports"

OPENALEX_RAW_DIR = RAW_DIR / "openalex"
WORKS_RAW_DIR = OPENALEX_RAW_DIR / "works"
AUTHORS_RAW_DIR = OPENALEX_RAW_DIR / "authors"
INSTITUTION_RAW_FILE = OPENALEX_RAW_DIR / "institution.json"

FACULTY_CSV = REPO_ROOT / "illinois-researchers.csv"
