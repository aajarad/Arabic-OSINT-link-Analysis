"""Arabic NLP: named-entity recognition with graceful backend selection."""

from .ner import ArabicNER, Entity
from .dates import extract_dates, DateMention

__all__ = ["ArabicNER", "Entity", "extract_dates", "DateMention"]
