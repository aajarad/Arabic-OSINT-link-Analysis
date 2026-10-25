"""End-to-end tests for orchestration, report and packaging."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import networkx as nx

import osint_pipeline
from osint_pipeline import Pipeline, PipelineConfig, EthicsPolicy
from osint_pipeline.report import Reporter

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "samples" / "arabic_posts.jsonl"


def _cfg(tmp_path, **kw):
    return PipelineConfig(output_dir=str(tmp_path / "out"), ner_backend="rule",
                          ethics=EthicsPolicy(min_seconds_between_requests=0), **kw)


def test_public_exports_importable():
    for name in osint_pipeline.__all__:
        assert hasattr(osint_pipeline, name)


def test_full_run_artifacts(tmp_path):
    summary = Pipeline(_cfg(tmp_path)).run("file", path=str(SAMPLE))
    out = tmp_path / "out"
    for f in ("link_graph.gexf", "link_graph.json", "timeline.json",
              "timeline.csv", "report.json", "report.html", "archive/manifest.jsonl"):
        assert (out / f).is_file(), f
    assert summary["collected"] == summary["archived"] > 0
    assert summary["archive_integrity"]["intact"]
    assert summary["config"]["entity_labels"] == ["PERSON", "LOC", "ORG"]
    assert summary["collection_stats"]["rejected"] >= 1
    g = nx.read_gexf(out / "link_graph.gexf")
    assert g.number_of_nodes() == summary["graph"]["nodes"]
    rows = list(csv.reader((out / "timeline.csv").open(encoding="utf-8-sig")))
    assert rows[0][0] == "date" and len(rows) > 1
    raw = (out / "report.json").read_text(encoding="utf-8")
    assert "\\u06" not in raw and any(0x600 <= ord(c) <= 0x6FF for c in raw)
    assert json.loads(raw)["artifacts"]["report_html"].endswith("report.html")


def test_rerun_same_dir_counts_only_this_run(tmp_path):
    s1 = Pipeline(_cfg(tmp_path)).run("file", path=str(SAMPLE))
    s2 = Pipeline(_cfg(tmp_path)).run("file", path=str(SAMPLE))
    assert s2["archived"] == s1["archived"]


def test_custom_archive_dir_and_labels(tmp_path):
    cfg = _cfg(tmp_path, archive_dir=str(tmp_path / "arch"), entity_labels=("LOC",))
    summary = Pipeline(cfg).run("file", path=str(SAMPLE))
    assert (tmp_path / "arch" / "manifest.jsonl").is_file()
    assert not (tmp_path / "out" / "archive").exists()
    g = nx.read_gexf(tmp_path / "out" / "link_graph.gexf")
    assert {d["label"] for _, d in g.nodes(data=True)} <= {"LOC"}
    assert summary["config"]["entity_labels"] == ["LOC"]


class _Strict(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag in ("script", "img", "iframe"):
            self.scripts += 1


def test_report_html_rtl_and_escaped(tmp_path):
    evil = '<script>alert(1)</script>'
    summary = {
        "generated_at": "x", "ner_backend": evil, "collected": 1, "archived": 1,
        "entities_total": 1, "entity_counts": {evil: 1},
        "graph": {"nodes": 1, "edges": 0, "top_by_centrality":
                  [{"entity": evil, "label": '"><img src=x>', "degree_centrality": 0}],
                  "top_by_triangulation":
                  [{"entity": evil, "label": "PERSON", "source_count": 1, "mentions": 1}]},
        "timeline_preview": [{"date": evil, "event_count": 1, "source_count": 1,
                              "entities": [evil]}],
        "archive_integrity": {"checked": 1, "ok": 0, "intact": False,
                              "problems": [{"item_id": evil, "issue": "missing_blob"}]},
    }
    path = Reporter(summary).to_html(tmp_path / "r.html")
    text = path.read_text(encoding="utf-8")
    assert 'dir="rtl"' in text and 'lang="ar"' in text
    assert "<script>alert" not in text and "<img src=x>" not in text
    p = _Strict()
    p.feed(text)
    assert p.scripts == 0 and "body" in p.tags


def test_empty_run(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    summary = Pipeline(_cfg(tmp_path)).run("file", path=str(empty))
    assert summary["collected"] == 0 and summary["graph"]["nodes"] == 0
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "لا توجد بيانات" in html
    json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))


def test_cli_run_and_verify(tmp_path):
    out = tmp_path / "o"
    base = [sys.executable, "-m", "osint_pipeline"]
    r = subprocess.run(base + ["run", "--collector", "file", "--source", str(SAMPLE),
                               "--output", str(out), "--ner-backend", "rule",
                               "--min-delay", "0"],
                       cwd=ROOT, capture_output=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    v = subprocess.run(base + ["verify", "--archive", str(out / "archive")],
                       cwd=ROOT, capture_output=True, encoding="utf-8")
    assert v.returncode == 0, v.stdout
    # tamper -> verify fails
    blob = next((out / "archive").rglob("*.json"))
    blob.write_text('{"text": "tampered"}', encoding="utf-8")
    v = subprocess.run(base + ["verify", "--archive", str(out / "archive")],
                       cwd=ROOT, capture_output=True, encoding="utf-8")
    assert v.returncode == 1
