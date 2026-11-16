import pipeline
from app.db.base import EMBEDDING_DIM


def test_pipeline_can_import_backend_models() -> None:
    assert pipeline.__doc__
    assert EMBEDDING_DIM == 384
