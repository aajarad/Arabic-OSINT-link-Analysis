"""NLP area tests: dispatcher, rule backend, BIO folding, dates, normalization."""

from __future__ import annotations

import sys

import pytest

from osint_pipeline.nlp import ArabicNER, Entity
from osint_pipeline.nlp import backends as be
from osint_pipeline.nlp import gazetteers as gz
from osint_pipeline.nlp.dates import extract_dates
from osint_pipeline.utils.arabic import (
    is_probably_arabic,
    normalize_arabic,
    normalize_digits,
    strip_tashkeel,
)


# -- dispatcher ----------------------------------------------------------------
def test_invalid_backend_raises_value_error():
    with pytest.raises(ValueError):
        ArabicNER("nonsense")


def test_rule_and_auto_backend_names():
    assert ArabicNER("rule").backend_name == "rule"
    assert ArabicNER("auto").backend_name in ("camel", "transformers", "rule")


def test_missing_optional_backend_falls_back(monkeypatch):
    # Force imports of the optional packages to fail.
    for mod in ("camel_tools", "camel_tools.ner", "transformers"):
        monkeypatch.setitem(sys.modules, mod, None)
    assert ArabicNER("auto").backend_name == "rule"
    with pytest.warns(RuntimeWarning):
        ner = ArabicNER("transformers")
    assert ner.backend_name == "rule" and ner.fallback_reasons


def test_model_kwarg_forwarded(monkeypatch):
    seen = {}

    def fake_load(name, *, model=None):
        seen["name"], seen["model"] = name, model
        return be.RuleBasedBackend()

    monkeypatch.setattr(be, "load_backend", fake_load)
    ArabicNER("transformers", model="my/model")
    assert seen == {"name": "transformers", "model": "my/model"}


def test_empty_text_and_entity_contract():
    ner = ArabicNER("rule")
    assert ner.extract("") == [] and ner.extract("   ") == []
    ents = ner.extract("زار الرئيس بشار الأسد دمشق في 3 آذار 2020")
    for e in ents:
        assert isinstance(e, Entity)
        assert e.label in {"PERSON", "LOC", "ORG", "DATE"}
        assert e.normalized and e.text
        assert set(e.to_dict()) == {"text", "label", "start", "end", "normalized", "backend"}
    assert [e.start for e in ents] == sorted(e.start for e in ents)
    assert any(e.label == "DATE" and e.normalized == "2020-03-03" for e in ents)


def test_overlapping_spans_resolved():
    ents = ArabicNER("rule").extract("في 5 شباط 2021 وصل وفد")
    spans = sorted((e.start, e.end) for e in ents)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


# -- rule backend --------------------------------------------------------------
def test_gazetteer_longest_first_and_prefix_clitic():
    ents = ArabicNER("rule").extract("اجتمع مجلس الأمن في دير الزور وسافر بدمشق")
    got = {(e.label, e.normalized) for e in ents}
    assert ("ORG", normalize_arabic("مجلس الأمن")) in got
    assert ("LOC", normalize_arabic("دير الزور")) in got
    assert ("LOC", normalize_arabic("دمشق")) in got
    # The clitic is not part of the surface span.
    assert any(e.text == "دمشق" for e in ents)
    # "دير" / "الزور" individually must not appear.
    assert not any(e.normalized in ("دير", "الزور") for e in ents)


def test_honorific_cue_person_stops_at_verb_and_punctuation():
    ents = ArabicNER("rule").extract("قال الوزير أحمد الشامي أكد أن الوضع جيد. الرئيس، محمد")
    persons = [e.text for e in ents if e.label == "PERSON"]
    assert persons == ["أحمد الشامي"]


def test_multiword_cue():
    ents = ArabicNER("rule").extract("صرح رئيس الوزراء نجيب سلام اليوم")
    assert [e.text for e in ents if e.label == "PERSON"][:1] == ["نجيب سلام"]


def test_known_person_without_cue():
    ents = ArabicNER("rule").extract("التقى غوتيريش وفدا")
    assert any(e.label == "PERSON" and "غوتيريش" in e.text for e in ents)


def test_gazetteers_prenormalized_and_broad():
    for table in (gz.LOCATIONS, gz.ORGANIZATIONS, gz.KNOWN_PERSONS):
        for key in table:
            assert key == normalize_arabic(key)
    assert gz.PERSON_CUES == {normalize_arabic(c) for c in gz.PERSON_CUES}
    assert len(gz.LOCATIONS) > 250 and len(gz.ORGANIZATIONS) > 150
    assert len(gz.KNOWN_PERSONS) > 150
    assert gz.MAX_GAZETTEER_NGRAM >= 3


# -- BIO folding / model backends (fake models, no downloads) ---------------------
def test_canon_label_mapping():
    assert be._canon("B-PER") == "PERSON" and be._canon("I-PERS") == "PERSON"
    assert be._canon("LOC") == "LOC" and be._canon("B-GPE") == "LOC"
    assert be._canon("I-ORG") == "ORG"
    assert be._canon("B-MISC") is None and be._canon("O") is None


def test_from_bio_folding():
    text = "التقى محمد علي في بغداد مع الامم المتحدة"
    toks = text.split()
    tags = ["O", "B-PER", "I-PER", "O", "B-LOC", "O", "B-ORG", "I-ORG"]
    ents = be._from_bio(text, toks, tags, "camel")
    assert [(e.label, e.text) for e in ents] == [
        ("PERSON", "محمد علي"), ("LOC", "بغداد"), ("ORG", "الامم المتحدة"),
    ]
    assert all(text[e.start:e.end] == e.text for e in ents)
    # adjacent B-B splits; I- after a different type starts a new entity
    toks = ["أ", "ب", "ج"]
    ents = be._from_bio("أ ب ج", toks, ["B-PER", "B-PER", "I-LOC"], "x")
    assert [e.label for e in ents] == ["PERSON", "PERSON", "LOC"]


def test_camel_backend_with_fake_model():
    class FakeNER:
        def predict_sentence(self, toks):
            return ["B-PERS" if t == "علي" else "O" for t in toks]

    b = be.CamelBackend(recognizer=FakeNER(), tokenizer=str.split)
    ents = b.extract("قال علي اليوم")
    assert [(e.label, e.text) for e in ents] == [("PERSON", "علي")]
    assert b.extract("") == []


def test_transformers_backend_with_fake_pipeline():
    text = "زار محمد علي بغداد"
    agg = lambda t: [  # aggregated output
        {"entity_group": "PER", "start": 4, "end": 12, "word": "محمد علي"},
        {"entity_group": "LOC", "start": 13, "end": 18, "word": "بغداد"},
        {"entity_group": "MISC", "start": 0, "end": 3, "word": "زار"},
    ]
    ents = be.TransformersBackend(pipe=agg).extract(text)
    assert [(e.label, e.text) for e in ents] == [("PERSON", "محمد علي"), ("LOC", "بغداد")]

    raw = lambda t: [  # un-aggregated BIO word pieces
        {"entity": "B-PER", "start": 4, "end": 8},
        {"entity": "I-PER", "start": 9, "end": 12},
        {"entity": "B-LOC", "start": 13, "end": 18},
    ]
    ents = be.TransformersBackend(pipe=raw).extract(text)
    assert [(e.label, e.text) for e in ents] == [("PERSON", "محمد علي"), ("LOC", "بغداد")]


# -- dates -----------------------------------------------------------------------
@pytest.mark.parametrize("text,iso,prec", [
    ("في 6 شباط 2023", "2023-02-06", "day"),
    ("في 12 آذار 2011", "2011-03-12", "day"),
    ("في 1 تشرين الأول 2019", "2019-10-01", "day"),
    ("في 25 كانون الثاني 2011", "2011-01-25", "day"),
    ("15 كانون الأول 2020", "2020-12-15", "day"),
    ("في 4 فبراير 2020", "2020-02-04", "day"),
    ("في 4 إبريل 2020", "2020-04-04", "day"),
    ("بتاريخ 11/02/2023", "2023-02-11", "day"),
    ("بتاريخ 2023-02-11", "2023-02-11", "day"),
    ("بتاريخ ١١/٠٢/٢٠٢٣", "2023-02-11", "day"),
    ("٦ شباط ٢٠٢٣", "2023-02-06", "day"),
    ("خلال آذار 2011", "2011-03", "month"),
    ("في عام 2011 اندلعت", "2011", "year"),
    ("29/02/2020", "2020-02-29", "day"),
])
def test_date_forms(text, iso, prec):
    ds = extract_dates(text)
    assert len(ds) == 1, ds
    assert ds[0].normalized == iso and ds[0].precision == prec
    assert ds[0].surface == text[ds[0].start:ds[0].end]


@pytest.mark.parametrize("text", ["31/02/2023", "29/02/2021", "2023-13-01", "0/5/2020", "31 شباط 2023x"])
def test_invalid_dates_rejected(text):
    assert not [d for d in extract_dates(text) if d.precision == "day"]


def test_dates_precision_and_no_overlap():
    ds = extract_dates("في 6 شباط 2023 ثم آذار 2024 ثم 2025")
    assert [d.normalized for d in ds] == ["2023-02-06", "2024-03", "2025"]


def test_date_offsets_with_diacritics_and_hamza():
    text = "حدث ذلك في ٦ أيلول ٢٠٢٠"
    d = extract_dates(text)[0]
    assert d.normalized == "2020-09-06" and text[d.start:d.end] == d.surface


# -- normalization ---------------------------------------------------------------
def test_normalize_arabic():
    assert normalize_arabic("أحمد") == normalize_arabic("احمد") == "احمد"
    assert normalize_arabic("مَدْرَسَة") == "مدرسه"
    assert normalize_arabic("سـوريا") == "سوريا"
    assert normalize_arabic("على") == "علي"
    assert normalize_arabic("  ١٢٣   حلب ") == "123 حلب"
    assert normalize_arabic("") == "" and normalize_arabic(None) == ""
    assert normalize_arabic("١٢", keep_digits_arabic=True) == "١٢"
    assert strip_tashkeel("مُحَمَّد") == "محمد"
    assert normalize_digits("٠١٢٣٤٥٦٧٨٩۴۵") == "012345678945"
    assert is_probably_arabic("مرحبا بالعالم") and not is_probably_arabic("hello world")
    assert not is_probably_arabic("")
