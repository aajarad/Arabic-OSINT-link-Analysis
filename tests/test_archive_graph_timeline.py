import csv
import json

import networkx as nx

from osint_pipeline.archive import Archiver
from osint_pipeline.collectors.base import CollectedItem
from osint_pipeline.graph import LinkGraphBuilder
from osint_pipeline.nlp.ner import Entity
from osint_pipeline.timeline import TimelineBuilder
from osint_pipeline.utils import parse_iso, sha256_text


def _item(text, item_id="1", author="a"):
    return CollectedItem(item_id=item_id, platform="test", text=text, author=author).stamp("t") \
        if hasattr(CollectedItem, "stamp") else CollectedItem(item_id=item_id, platform="test", text=text, author=author)


def _ent(text, label="PERSON"):
    return Entity(text=text, label=label, start=0, end=len(text), normalized=text, backend="t")


# ---- archive -------------------------------------------------------------
def test_archive_dedupe_manifest_and_verify(tmp_path):
    arc = Archiver(tmp_path / "arc")
    r1 = arc.archive_item(_item("نص عربي", "1"))
    arc.archive_item(_item("نص عربي", "2"))
    assert r1.blob_path.split("/")[1] == r1.content_sha256[:2]
    assert len(arc.load_manifest()) == 2
    assert len(list((tmp_path / "arc" / "blobs").rglob("*.json"))) == 1
    # a second Archiver on the same dir appends
    Archiver(tmp_path / "arc").archive_item(_item("آخر", "3"))
    assert len(arc.load_manifest()) == 3
    rep = arc.verify()
    assert rep["intact"] and rep["checked"] == 3 and rep["ok"] == 3


def test_verify_detects_tamper_missing_and_malformed(tmp_path):
    arc = Archiver(tmp_path / "arc")
    r1 = arc.archive_item(_item("واحد", "1"))
    r2 = arc.archive_item(_item("اثنان", "2"))
    b1 = tmp_path / "arc" / r1.blob_path
    data = json.loads(b1.read_text(encoding="utf-8"))
    data["text"] = "معدل"
    b1.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "arc" / r2.blob_path).unlink()
    with open(arc.manifest_path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    rep = arc.verify()
    issues = {p["issue"] for p in rep["problems"]}
    assert issues == {"hash_mismatch", "missing_blob", "malformed_manifest_line"}
    assert not rep["intact"]
    assert len(arc.load_manifest()) == 2  # does not raise


def test_archive_empty(tmp_path):
    arc = Archiver(tmp_path / "arc")
    assert arc.load_manifest() == []
    assert arc.verify()["intact"]


def test_deleted_blob_is_healed_on_rearchive(tmp_path):
    arc = Archiver(tmp_path / "arc")
    r = arc.archive_item(_item("x y", "1"))
    (tmp_path / "arc" / r.blob_path).unlink()
    arc.archive_item(_item("x y", "1"))
    assert (tmp_path / "arc" / r.blob_path).exists()


# ---- graph ---------------------------------------------------------------
def test_graph_empty_and_single(tmp_path):
    b = LinkGraphBuilder()
    m = b.metrics()
    assert m["nodes"] == 0 and m["top_by_triangulation"] == []
    b.to_gexf(tmp_path / "e.gexf")
    b.to_json(tmp_path / "e.json")
    b.add_document([_ent("دمشق", "LOC")], source="s1")
    m = b.metrics()
    assert m["nodes"] == 1 and m["edges"] == 0 and m["communities"] == 1


def test_graph_edges_sources_exports(tmp_path):
    b = LinkGraphBuilder()
    b.add_document([_ent("أحمد"), _ent("دمشق", "LOC"), _ent("2011", "DATE")], source="s1")
    b.add_document([_ent("أحمد"), _ent("دمشق", "LOC")], source="s2")
    b.add_document([_ent("أحمد"), _ent("دمشق", "LOC")], source="s2")
    g = b.build()
    assert set(g.nodes) == {"PERSON:أحمد", "LOC:دمشق"}
    assert g["PERSON:أحمد"]["LOC:دمشق"]["weight"] == 3
    assert g.nodes["PERSON:أحمد"]["source_count"] == 2
    assert g.nodes["PERSON:أحمد"]["mentions"] == 3
    m = b.metrics()
    assert m["top_by_triangulation"][0]["source_count"] == 2
    for k in ("nodes", "edges", "documents", "communities", "density", "top_by_centrality"):
        assert k in m
    p = b.to_gexf(tmp_path / "g.gexf")
    g2 = nx.read_gexf(p)
    assert g2.nodes["LOC:دمشق"]["display"] == "دمشق"
    data = json.loads(b.to_json(tmp_path / "g.json").read_text(encoding="utf-8"))
    assert len(data["nodes"]) == 2 and len(data["links"]) == 1


def test_graph_edgeless_multi_node():
    b = LinkGraphBuilder()
    b.add_document([_ent("أحمد")], source="s1")
    b.add_document([_ent("دمشق", "LOC")], source="s2")
    assert b.metrics()["communities"] == 2


# ---- timeline ------------------------------------------------------------
def test_timeline_empty(tmp_path):
    t = TimelineBuilder()
    assert t.events() == [] and t.grouped() == []
    assert t.add_document("", published="2020-01-01") == 1  # falls back to published
    assert t.add_document("بدون تاريخ") == 0
    t.to_json(tmp_path / "t.json")


def test_timeline_text_date_beats_published_and_grouping(tmp_path):
    t = TimelineBuilder()
    t.add_document("في 12 آذار 2011 حدث شيء", entities=[_ent("دمشق", "LOC")],
                   source="s1", item_id="1", published="2020-05-05T10:00:00Z")
    t.add_document("حدث ذلك في 12/03/2011", source="s2", item_id="2")
    t.add_document("لا تاريخ هنا", source="s3", item_id="3", published="2011-03-12T08:00:00+00:00")
    t.add_document("حصل في عام 2010", source="s4", item_id="4")
    assert all(e.date != "2020-05-05" for e in t.events())
    groups = t.grouped()
    assert [g["date"] for g in groups] == ["2010", "2011-03-12"]
    g = groups[1]
    assert g["source_count"] == 3 and g["event_count"] == 3
    origins = {e["date_origin"] for e in g["events"]}
    assert origins == {"text", "published"}
    t.to_json(tmp_path / "t.json")
    t.to_csv(tmp_path / "t.csv")
    raw = (tmp_path / "t.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader((tmp_path / "t.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0][0] == "date" and len(rows) == 5


def test_timeline_csv_formula_guard(tmp_path):
    t = TimelineBuilder()
    t.add_document("=cmd 2011", source="=evil")
    t.to_csv(tmp_path / "t.csv")
    body = (tmp_path / "t.csv").read_text(encoding="utf-8-sig")
    assert ",'=evil," in body


def test_parse_iso_robust():
    assert parse_iso(None) is None and parse_iso("garbage") is None and parse_iso(5) is None
    assert parse_iso("2020-01-02T03:04:05Z").tzinfo is not None
    assert sha256_text("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
