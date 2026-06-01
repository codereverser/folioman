"""Shared Pydantic base for framework-free domain models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Base for all core domain models: strip strings, forbid unknown fields.

    Subclasses may extend (not replace) this config — pydantic merges
    ``model_config`` across the MRO, so e.g. ``Security`` adds ``frozen=True``.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
