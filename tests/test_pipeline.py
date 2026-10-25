"""End-to-end and unit tests. Run with:  python -m pytest -q"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from osint_pipeline.utils import sha256_text
from osint_pipeline.utils.arabic import normalize_arabic, strip_tashkeel
from osint_pipeline.collectors import FileCollector, EthicsPolicy, CollectedItem
from osint_pipeline.archive import Archiver
from osint_pipeline.nlp import ArabicNER
from osint_pipeline.nlp.dates import extract_dates
from osint_pipeline.graph import LinkGraphBuilder
from osint_pipeline.timeline import TimelineBuilder
from osint_pipeline.pipeline import Pipeline, PipelineConfig

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "samples" / "arabic_posts.jsonl"


# -- utils -------------------------------------------------------------------
def test_normalize_arabic_is_diacritic_insensitive():
    assert normalize_arabic("سُورِيَّة") == normalize_arabic("سوريه")
    assert strip_tashkeel("مُحَمَّد") == "محمد"


def test_sha256_is_stable():
    assert sha256_text("حلب") == sha256_text("حلب")
    assert sha256_text("حلب") != sha256_text("دمشق")


# -- collection & ethics -----------------------------------------------------
def test_file_collector_stamps_provenance_and_drops_private():
    items = FileCollector(SAMPLE, policy=EthicsPolicy()).collect()
    assert items, "expected public items"
    # The one private post (p011) must be excluded.
    assert all("private" not in (it.raw.get("visibility", "")) for it in items)
    for it in items:
        assert it.collected_at and it.content_sha256 and it.item_id
        assert it.content_sha256 == sha256_text(it.text)


# -- archive integrity -------------------------------------------------------
def test_archive_roundtrip_and_tamper_detection(tmp_path):
    arc = Archiver(tmp_path / "arc")
    item = CollectedItem(text="اجتماع في دمشق", platform="twitter").finalize("test")
    rec = arc.archive_item(item)
    report = arc.verify()
    assert report["intact"] and report["checked"] == 1

    # Tamper with the stored blob -> verification must fail.
    blob = tmp_path / "arc" / rec.blob_path
    data = json.loads(blob.read_text(encoding="utf-8"))
    data["text"] = "نص معدَّل"
    blob.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert arc.verify()["intact"] is False


# -- NER (rule-based, always available) --------------------------------------
def test_rule_ner_finds_loc_org_person_and_date():
    ner = ArabicNER(backend="rule")
    text = "قال الوزير أحمد الشامي إن الأمم المتحدة أرسلت مساعدات إلى حلب في 8 شباط 2023."
    ents = ner.extract(text)
    labels = {e.label for e in ents}
    assert {"LOC", "ORG", "PERSON", "DATE"} <= labels
    surfaces = {e.text for e in ents}
    assert any("حلب" in s for s in surfaces)


def test_date_extraction_levantine_and_numeric():
    d1 = extract_dates("وقع الحدث في 6 شباط 2023 في المدينة")
    assert d1 and d1[0].normalized == "2023-02-06"
    d2 = extract_dates("بتاريخ 11/02/2023 تم الإعلان")
    assert d2 and d2[0].normalized == "2023-02-11"


# -- graph -------------------------------------------------------------------
def test_graph_cooccurrence_and_triangulation():
    ner = ArabicNER(backend="rule")
    g = LinkGraphBuilder()
    for text, src in [
        ("الأمم المتحدة أرسلت مساعدات إلى حلب", "sourceA"),
        ("الأمم المتحدة تنسق في حلب مع الهلال الأحمر", "sourceB"),
    ]:
        g.add_document(ner.extract(text), source=src)
    built = g.build()
    assert built.number_of_nodes() >= 2
    metrics = g.metrics()
    # 'حلب' appears from two distinct sources -> triangulation source_count == 2.
    top = {row["entity"]: row["source_count"] for row in metrics["top_by_triangulation"]}
    assert max(top.values()) >= 2


# -- timeline ----------------------------------------------------------------
def test_timeline_groups_by_date():
    ner = ArabicNER(backend="rule")
    tb = TimelineBuilder()
    tb.add_document("حدث في 8 شباط 2023 في حلب", entities=ner.extract("في حلب"), source="A")
    tb.add_document("خبر آخر في 8 شباط 2023", source="B")
    grouped = tb.grouped()
    dates = {g["date"] for g in grouped}
    assert "2023-02-08" in dates
    bucket = next(g for g in grouped if g["date"] == "2023-02-08")
    assert bucket["source_count"] == 2


# -- full pipeline -----------------------------------------------------------
def test_full_pipeline_run(tmp_path):
    config = PipelineConfig(output_dir=str(tmp_path / "out"), ner_backend="rule")
    summary = Pipeline(config).run("file", path=str(SAMPLE))
    assert summary["collected"] == 10          # 11 posts, 1 private dropped
    assert summary["archive_integrity"]["intact"]
    assert summary["graph"]["nodes"] > 0
    assert summary["timeline_events"] > 0
    for artifact in summary["artifacts"].values():
        assert Path(artifact).exists()
