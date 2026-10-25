"""Arabic NER dispatcher.

Backend selection order (first that loads wins), overridable by the caller:

    1. CAMeL Tools NERecognizer        (backend="camel")
    2. AraBERT / transformers NER      (backend="transformers")
    3. Rule-based gazetteer fallback   (backend="rule")   <- always available

Entity labels are normalized to: PERSON, LOC, ORG, DATE. DATE mentions are
always produced by the deterministic date extractor regardless of backend, so
the timeline stage does not depend on a model being installed.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from .dates import extract_dates

#: Backend names accepted by :class:`ArabicNER` (besides ``"auto"``).
BACKENDS = ("camel", "transformers", "rule")


@dataclass
class Entity:
    text: str            # surface form as it appeared
    label: str           # PERSON | LOC | ORG | DATE
    start: int
    end: int
    normalized: str      # canonical key for grouping across mentions
    backend: str

    def to_dict(self) -> Dict:
        return asdict(self)


class ArabicNER:
    #: preferred fallback order when backend="auto"
    _AUTO_ORDER = ("camel", "transformers", "rule")

    def __init__(self, backend: str = "auto", *, model: Optional[str] = None) -> None:
        backend = (backend or "auto").strip().lower()
        if backend != "auto" and backend not in BACKENDS:
            raise ValueError(
                f"Unknown NER backend {backend!r}; choose from auto, "
                + ", ".join(BACKENDS)
            )
        self.requested = backend
        self._model_name = model
        self._impl = None
        #: Why a requested backend was not used (empty when none fell back).
        self.fallback_reasons: List[str] = []
        self.backend_name = self._select(backend)

    def _select(self, backend: str) -> str:
        from . import backends

        order = self._AUTO_ORDER if backend == "auto" else (backend, "rule")
        for name in order:
            try:
                self._impl = backends.load_backend(name, model=self._model_name)
                break
            except Exception as exc:  # noqa: BLE001 - fallback is intentional
                self.fallback_reasons.append(f"{name}: {exc}")
        else:  # pragma: no cover - 'rule' cannot fail to load
            raise RuntimeError(
                "No NER backend could be loaded: " + " | ".join(self.fallback_reasons)
            )
        if backend != "auto" and name != backend:
            warnings.warn(
                f"NER backend {backend!r} unavailable ({self.fallback_reasons[0]}); "
                f"falling back to {name!r}",
                RuntimeWarning,
                stacklevel=3,
            )
        return name

    def extract(self, text: str) -> List[Entity]:
        """Return PERSON/LOC/ORG entities from the backend plus DATE entities,
        sorted by position with overlapping spans resolved."""
        if not text or not text.strip():
            return []
        entities = list(self._impl.extract(text))
        for dm in extract_dates(text):
            entities.append(
                Entity(
                    text=dm.surface,
                    label="DATE",
                    start=dm.start,
                    end=dm.end,
                    normalized=dm.normalized,
                    backend="dates",
                )
            )
        return _resolve_overlaps(entities)


def _resolve_overlaps(entities: List[Entity]) -> List[Entity]:
    """Keep a non-overlapping set of spans: longer spans win; on equal length
    a non-DATE (model/gazetteer) entity wins; then the earlier one."""
    ranked = sorted(
        entities,
        key=lambda e: (-(e.end - e.start), e.label == "DATE", e.start),
    )
    kept: List[Entity] = []
    for ent in ranked:
        if ent.end <= ent.start:
            continue
        if any(ent.start < k.end and k.start < ent.end for k in kept):
            continue
        kept.append(ent)
    kept.sort(key=lambda e: (e.start, e.end))
    return kept
