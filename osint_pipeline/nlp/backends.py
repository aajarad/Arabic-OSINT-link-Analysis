"""Concrete NER backends. Each exposes ``extract(text) -> List[Entity]``.

Only PERSON/LOC/ORG are produced here; DATE is added by the dispatcher.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..utils.arabic import normalize_arabic
from .ner import Entity
from . import gazetteers as gz

# Canonical label mapping from the various tag schemes models emit.
_LABEL_MAP = {
    "PER": "PERSON", "PERS": "PERSON", "PERSON": "PERSON",
    "LOC": "LOC", "LOCATION": "LOC", "GPE": "LOC",
    "ORG": "ORG", "ORGANIZATION": "ORG", "ORGANISATION": "ORG",
}

_TOKEN_RE = re.compile(r"[^\s،؛.:!؟?)(»«\"',;\[\]{}]+")
# Characters between two tokens that end a name span.
_BREAK_CHARS = set("،؛.:!؟?,;()[]{}\"«»\n\r")
_CLITICS = "وفبلك"


def _canon(label: str) -> Optional[str]:
    """Map a model tag (``B-PER``, ``I-LOC``, ``ORG`` ...) to PERSON/LOC/ORG,
    or None for ``O``/MISC/unknown tags."""
    label = str(label).upper().strip()
    if label.startswith(("B-", "I-", "E-", "S-", "L-", "U-")):
        label = label[2:]
    return _LABEL_MAP.get(label)


def _variants(tok: str) -> List[Tuple[str, int]]:
    """Normalized token plus forms with 0-2 leading proclitics (و ف ب ل ك)
    removed, each paired with the number of characters removed."""
    out = [(tok, 0)]
    cur, cut = tok, 0
    for _ in range(2):
        if len(cur) > 3 and cur[0] in _CLITICS:
            cur, cut = cur[1:], cut + 1
            out.append((cur, cut))
        else:
            break
    return out


def _orig_offset(token: str, letters: int) -> int:
    """Index in the original ``token`` after consuming ``letters`` characters
    that survive normalization (tashkeel/invisible chars are skipped)."""
    seen = 0
    for idx, ch in enumerate(token):
        if seen >= letters:
            return idx
        if normalize_arabic(ch):
            seen += 1
    return len(token)


# ---------------------------------------------------------------------------
# Rule-based (always available)
# ---------------------------------------------------------------------------
class RuleBasedBackend:
    name = "rule"

    @staticmethod
    def _lookup(key: str):
        if key in gz.LOCATIONS:
            return "LOC"
        if key in gz.ORGANIZATIONS:
            return "ORG"
        if key in gz.KNOWN_PERSONS:
            return "PERSON"
        return None

    def extract(self, text: str) -> List[Entity]:
        tokens = [(m.group(), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]
        norm = [normalize_arabic(t[0]) for t in tokens]
        n = len(tokens)
        entities: List[Entity] = []
        claimed = [False] * n

        def gap_breaks(i: int, j: int) -> bool:
            """True when sentence/clause punctuation sits between tokens i, j."""
            return any(c in _BREAK_CHARS for c in text[tokens[i][2]:tokens[j][1]])

        # 1) Multi-word gazetteer matches (longest first).
        for size in range(min(gz.MAX_GAZETTEER_NGRAM, n), 0, -1):
            for i in range(0, n - size + 1):
                if any(claimed[i:i + size]):
                    continue
                if size > 1 and any(gap_breaks(k, k + 1) for k in range(i, i + size - 1)):
                    continue
                rest = " ".join(norm[i + 1:i + size])
                for first, cut in _variants(norm[i]):
                    key = (first + " " + rest).strip() if rest else first
                    if not key:
                        continue
                    label = self._lookup(key)
                    if not label:
                        continue
                    start = tokens[i][1] + _orig_offset(tokens[i][0], cut)
                    end = tokens[i + size - 1][2]
                    entities.append(Entity(text[start:end], label, start, end, key, self.name))
                    for j in range(i, i + size):
                        claimed[j] = True
                    break

        # 2) Honorific cue -> following 1-3 tokens as a PERSON name.
        def cue_len(i: int) -> int:
            """Number of tokens of a cue starting at i (0 if none)."""
            for size in range(min(gz.MAX_CUE_NGRAM, n - i), 0, -1):
                if any(claimed[i:i + size]):
                    continue
                if any(gap_breaks(k, k + 1) for k in range(i, i + size - 1)):
                    continue
                rest = " ".join(norm[i + 1:i + size])
                for first, _cut in _variants(norm[i]):
                    key = (first + " " + rest).strip() if rest else first
                    if key in gz.PERSON_CUES:
                        return size
            return 0

        i = 0
        while i < n:
            size = 0 if claimed[i] else cue_len(i)
            if not size:
                i += 1
                continue
            j = i + size
            name_tokens: List[int] = []
            while (j < n and len(name_tokens) < 3 and not claimed[j]
                   and not gap_breaks(j - 1, j)):
                if cue_len(j) or _is_stopword(norm[j]) or not _is_namelike(norm[j]):
                    break
                name_tokens.append(j)
                j += 1
            if name_tokens:
                start = tokens[name_tokens[0]][1]
                end = tokens[name_tokens[-1]][2]
                key = " ".join(norm[k] for k in name_tokens)
                entities.append(Entity(text[start:end], "PERSON", start, end, key, self.name))
                for k in name_tokens:
                    claimed[k] = True
            i = max(j, i + size)
        entities.sort(key=lambda e: e.start)
        return entities


def _is_namelike(norm_token: str) -> bool:
    return bool(norm_token) and not norm_token[0].isdigit()


_STOPWORDS = {
    normalize_arabic(w) for w in (
        "في", "من", "الى", "إلى", "على", "عن", "مع", "قد", "قال", "قالت", "ان",
        "إن", "أن", "و", "ثم", "او", "أو", "التي", "الذي", "هذا", "هذه",
        "بعد", "قبل", "خلال", "امام", "اليوم", "امس", "غدا", "حيث", "كما",
        "هو", "هي", "لا", "لم", "لن", "ما", "كان", "كانت", "يوم", "عند",
        # Reporting/speech verbs that commonly follow a name and would
        # otherwise be swallowed into the PERSON span.
        "اشار", "أشار", "اضاف", "أضاف", "اوضح", "أوضح", "صرح", "اعلن", "أعلن",
        "لفت", "ذكر", "تحدث", "اكد", "أكد", "قائلا", "بان", "بأن", "لدى",
        "نفى", "اعتبر", "رأى", "راى", "طالب", "دعا", "حذر", "وصل", "زار",
        "التقى", "استقبل", "اجتمع", "بحث", "قام", "يقول", "يؤكد", "اصدر",
        "أصدر", "افاد", "أفاد", "كشف", "شدد", "اكدت", "أكدت", "اعلنت", "أعلنت",
    )
}


def _is_stopword(norm_token: str) -> bool:
    return norm_token in _STOPWORDS


# ---------------------------------------------------------------------------
# CAMeL Tools
# ---------------------------------------------------------------------------
class CamelBackend:
    name = "camel"

    def __init__(self, recognizer=None, tokenizer=None) -> None:
        # ``recognizer``/``tokenizer`` may be injected (used by tests).
        if recognizer is None:
            from camel_tools.ner import NERecognizer  # type: ignore

            recognizer = NERecognizer.pretrained()
        if tokenizer is None:
            from camel_tools.tokenizers.word import simple_word_tokenize  # type: ignore

            tokenizer = simple_word_tokenize
        self._ner = recognizer
        self._tok = tokenizer

    def extract(self, text: str) -> List[Entity]:
        tokens = self._tok(text)
        if not tokens:
            return []
        labels = self._ner.predict_sentence(tokens)
        return _from_bio(text, tokens, labels, self.name)


# ---------------------------------------------------------------------------
# transformers / AraBERT
# ---------------------------------------------------------------------------
class TransformersBackend:
    name = "transformers"
    #: A public Arabic NER checkpoint fine-tuned on AraBERT-style encoders.
    DEFAULT_MODEL = "CAMeL-Lab/bert-base-arabic-camelbert-mix-ner"

    def __init__(self, model: Optional[str] = None, pipe=None) -> None:
        # ``pipe`` may be injected (used by tests); otherwise load the model.
        if pipe is not None:
            self._pipe = pipe
            return
        from transformers import (  # type: ignore
            AutoTokenizer,
            AutoModelForTokenClassification,
            pipeline,
        )

        name = model or self.DEFAULT_MODEL
        tok = AutoTokenizer.from_pretrained(name)
        mdl = AutoModelForTokenClassification.from_pretrained(name)
        self._pipe = pipeline(
            "token-classification",
            model=mdl,
            tokenizer=tok,
            aggregation_strategy="simple",
        )

    def extract(self, text: str) -> List[Entity]:
        # Normalize pipeline output to (label, start, end); merge un-aggregated
        # word-piece/BIO outputs (``entity`` key) into whole spans.
        spans: List[Tuple[str, int, int]] = []
        for span in self._pipe(text):
            raw = span.get("entity_group") or span.get("entity") or ""
            label = _canon(str(raw))
            start, end = span.get("start"), span.get("end")
            if not label or start is None or end is None:
                continue
            start, end = int(start), int(end)
            continues = (
                "entity_group" not in span and not str(raw).upper().startswith("B-")
            )
            if (continues and spans and spans[-1][0] == label
                    and 0 <= start - spans[-1][2] <= 1):
                spans[-1] = (label, spans[-1][1], end)
            else:
                spans.append((label, start, end))

        out: List[Entity] = []
        for label, start, end in spans:
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if start >= end:
                continue
            out.append(_mk(text, start, end, label, self.name))
        return out


def _from_bio(text: str, tokens, labels, backend: str) -> List[Entity]:
    """Fold a BIO tag sequence over whitespace tokens into Entity spans."""
    entities: List[Entity] = []
    cursor = 0
    spans = []
    for tok in tokens:
        idx = text.find(tok, cursor)
        if idx == -1:
            idx = cursor
        spans.append((idx, idx + len(tok)))
        cursor = idx + len(tok)

    cur_label = None
    cur_start = cur_end = None
    for (s, e), tag in zip(spans, labels):
        canon = _canon(str(tag))
        is_begin = str(tag).upper().startswith("B-") or (canon and canon != cur_label)
        if canon and (cur_label is None or is_begin):
            if cur_label:
                entities.append(_mk(text, cur_start, cur_end, cur_label, backend))
            cur_label, cur_start, cur_end = canon, s, e
        elif canon and canon == cur_label:
            cur_end = e
        else:
            if cur_label:
                entities.append(_mk(text, cur_start, cur_end, cur_label, backend))
            cur_label = cur_start = cur_end = None
    if cur_label:
        entities.append(_mk(text, cur_start, cur_end, cur_label, backend))
    return entities


def _mk(text: str, s: int, e: int, label: str, backend: str) -> Entity:
    surface = text[s:e]
    return Entity(surface, label, s, e, normalize_arabic(surface), backend)


# ---------------------------------------------------------------------------
def load_backend(name: str, *, model: Optional[str] = None):
    if name == "rule":
        return RuleBasedBackend()
    if name == "camel":
        return CamelBackend()
    if name == "transformers":
        return TransformersBackend(model=model)
    raise ValueError(f"Unknown NER backend: {name}")
