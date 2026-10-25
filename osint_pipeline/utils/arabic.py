"""Arabic text normalization utilities used across NER, matching and search.

These mirror the standard CAMeL Tools / AraBERT preprocessing conventions so
that the rule-based fallback and the model backends agree on token surfaces.
"""

from __future__ import annotations

import re

# Combining diacritics (tashkeel), superscript alef, Quranic marks and tatweel.
_TASHKEEL = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")
# Zero-width / bidi control characters that break token matching.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
# Arabic-Indic and extended Arabic-Indic digits -> ASCII.
_ARABIC_DIGITS = str.maketrans(
    "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"
    "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9",
    "01234567890123456789",
)
# Character folds (strictly 1:1 except where noted).
_FOLD = str.maketrans({
    "\u0622": "\u0627", "\u0623": "\u0627", "\u0625": "\u0627", "\u0671": "\u0627",  # alef forms
    "\u0649": "\u064a",   # alef maqsura -> ya
    "\u06cc": "\u064a",   # farsi yeh -> ya
    "\u06a9": "\u0643",   # keheh -> kaf
    "\u0629": "\u0647",   # ta marbuta -> ha
    "\u0624": "\u0648",   # waw with hamza -> waw
    "\u0626": "\u064a",   # ya with hamza -> ya
})

_ARABIC_RANGE = re.compile("[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")


def strip_tashkeel(text: str) -> str:
    """Remove Arabic diacritics and the tatweel elongation character."""
    return _TASHKEEL.sub("", text)


def normalize_digits(text: str) -> str:
    """Convert Arabic-Indic digits to ASCII digits."""
    return text.translate(_ARABIC_DIGITS)


def fold_chars(text: str) -> str:
    """Length-preserving letter folding (alef/ya/ta-marbuta/hamza variants and
    Arabic-Indic digits). Offsets in the result match the input exactly."""
    return normalize_digits(text.translate(_FOLD))


def normalize_arabic(text: str, *, keep_digits_arabic: bool = False) -> str:
    """Canonicalize Arabic text for matching.

    Steps: strip tashkeel/tatweel, unify alef variants, ya/alef-maqsura,
    ta-marbuta -> ha, hamza forms, and collapse whitespace. This is a
    lossy surface form intended for gazetteer lookup and de-duplication,
    not for display.
    """
    if not text:
        return ""
    text = _INVISIBLE.sub("", text)
    text = strip_tashkeel(text)
    text = text.translate(_FOLD)
    if not keep_digits_arabic:
        text = normalize_digits(text)
    return re.sub(r"\s+", " ", text).strip()


def is_probably_arabic(text: str, threshold: float = 0.2) -> bool:
    """Heuristic: True when the share of Arabic-script characters among
    non-whitespace characters meets ``threshold``."""
    considered = sum(1 for c in text if not c.isspace())
    if considered == 0:
        return False
    arabic = sum(1 for c in text if _ARABIC_RANGE.match(c))
    return (arabic / considered) >= threshold
