"""Domain model: works, authors, authorships, topics, faculty and their mapping.

OpenAlex ids are stored in short form (e.g. ``W4390000000``, ``A5019539533``, ``T11265``).
"""

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import EMBEDDING_DIM, Base

WORK_TSV_EXPR = (
    "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(abstract, '')), 'B')"
)


class Institution(Base):
    """Institution profile shown on the portal (UIUC), enriched from public sources."""

    __tablename__ = "institution"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ror: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    geo: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    images: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    facts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Topic(Base):
    """OpenAlex topic with its subfield -> field -> domain hierarchy (denormalized)."""

    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    subfield_id: Mapped[str | None] = mapped_column(String(32))
    subfield: Mapped[str | None] = mapped_column(Text)
    field_id: Mapped[str | None] = mapped_column(String(32), index=True)
    field: Mapped[str | None] = mapped_column(Text)
    domain_id: Mapped[str | None] = mapped_column(String(32))
    domain: Mapped[str | None] = mapped_column(Text)


class Work(Base):
    __tablename__ = "works"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    doi: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    publication_date: Mapped[date] = mapped_column(Date, index=True)
    publication_year: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(16))
    cited_by_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fwci: Mapped[float | None] = mapped_column(Float)
    is_oa: Mapped[bool] = mapped_column(default=False, server_default="false")
    oa_url: Mapped[str | None] = mapped_column(Text)
    venue_name: Mapped[str | None] = mapped_column(Text)
    primary_topic_id: Mapped[str | None] = mapped_column(ForeignKey("topics.id"))
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    sdgs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    rank_score: Mapped[float | None] = mapped_column(Float, index=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, Computed(WORK_TSV_EXPR, persisted=True))

    primary_topic: Mapped[Topic | None] = relationship()
    authorships: Mapped[list["Authorship"]] = relationship(
        back_populates="work", cascade="all, delete-orphan", order_by="Authorship.author_order"
    )

    __table_args__ = (
        Index("ix_works_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_works_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    orcid: Mapped[str | None] = mapped_column(String(64))
    # All-time profile metrics from the OpenAlex author directory (null if not fetched)
    works_count: Mapped[int | None] = mapped_column(Integer)
    cited_by_count: Mapped[int | None] = mapped_column(Integer)
    h_index: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index(
            "ix_authors_display_name_trgm",
            "display_name",
            postgresql_using="gin",
            postgresql_ops={"display_name": "gin_trgm_ops"},
        ),
    )


class Authorship(Base):
    """A (work, author) link. ``is_uiuc`` marks authors affiliated with UIUC on that work."""

    __tablename__ = "authorships"

    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[str] = mapped_column(
        ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    author_order: Mapped[int] = mapped_column(Integer)
    author_position: Mapped[str | None] = mapped_column(String(16))  # first / middle / last
    is_corresponding: Mapped[bool] = mapped_column(default=False, server_default="false")
    is_uiuc: Mapped[bool] = mapped_column(default=False, server_default="false", index=True)
    raw_affiliation: Mapped[str | None] = mapped_column(Text)

    work: Mapped[Work] = relationship(back_populates="authorships")
    author: Mapped[Author] = relationship()


class WorkTopic(Base):
    __tablename__ = "work_topics"

    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id"), primary_key=True, index=True)
    score: Mapped[float | None] = mapped_column(Float)


class WorkInstitution(Base):
    """All institutions on a work (used for collaboration stats)."""

    __tablename__ = "work_institutions"

    work_id: Mapped[str] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    institution_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    name: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(8))
    type: Mapped[str | None] = mapped_column(String(32))


class Faculty(Base):
    """A faculty member from the provided directory CSV (email is the stable key)."""

    __tablename__ = "faculty"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(Text)
    name_norm: Mapped[str] = mapped_column(Text)
    position: Mapped[str | None] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(Text)
    name_variants: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # merged CSV rows
    keywords_raw: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    keywords_clean: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # junk removed
    keywords_top: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # ranked, for display
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    author_links: Mapped[list["FacultyAuthorMap"]] = relationship(
        back_populates="faculty", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(
            "ix_faculty_name_norm_trgm",
            "name_norm",
            postgresql_using="gin",
            postgresql_ops={"name_norm": "gin_trgm_ops"},
        ),
        Index(
            "ix_faculty_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class FacultyAuthorMap(Base):
    """Faculty <-> OpenAlex author link. One faculty may map to several fragmented author ids."""

    __tablename__ = "faculty_author_map"

    faculty_id: Mapped[int] = mapped_column(
        ForeignKey("faculty.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[str] = mapped_column(
        ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # name/topic scores etc.

    faculty: Mapped[Faculty] = relationship(back_populates="author_links")
    author: Mapped[Author] = relationship()


class StatsKV(Base):
    """Precomputed portal widgets (JSON blobs keyed by name)."""

    __tablename__ = "stats_kv"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Meta(Base):
    """Pipeline metadata: snapshot date, embedding model, pipeline version, ..."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
