import json
import time
from types import SimpleNamespace

import pytest

from osint_pipeline import cli
from osint_pipeline.collectors import (
    CollectorUnavailable, EthicsPolicy, FileCollector, RSSCollector,
    available_collectors, get_collector,
)

FAST = EthicsPolicy(min_seconds_between_requests=0)


def write(tmp_path, name, content, enc="utf-8"):
    p = tmp_path / name
    p.write_bytes(content.encode(enc))
    return p


def test_registry():
    assert available_collectors() == ["file", "rss"]
    assert get_collector("rss") is RSSCollector
    with pytest.raises(ValueError):
        get_collector("nope")


def test_file_jsonl_bom_malformed_and_provenance(tmp_path):
    body = '{"id": 1, "content": "مرحبا بغداد", "user": {"screen_name": "ali"}}\n\nnot json\n[1]\n{"text": "   "}\n'
    p = write(tmp_path, "a.jsonl", "\ufeff" + body)
    c = FileCollector(p, policy=FAST)
    items = c.collect()
    assert len(items) == 1
    it = items[0]
    assert it.item_id == "1" and it.author == "ali" and it.lang == "ar"
    assert it.collector == "file" and it.collected_at and len(it.content_sha256) == 64
    assert c.malformed_lines == 2
    assert c.stats["rejected"] == 1  # whitespace-only text


def test_file_json_array_and_wrapper(tmp_path):
    recs = [{"body": "نص واحد"}, {"full_text": "نص اثنان"}]
    assert len(FileCollector(write(tmp_path, "a.json", json.dumps(recs)), policy=FAST).collect()) == 2
    wrapped = json.dumps({"data": recs}, indent=2)
    assert len(FileCollector(write(tmp_path, "b.json", wrapped), policy=FAST).collect()) == 2


def test_file_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        FileCollector(tmp_path / "missing.json", policy=FAST).collect()
    with pytest.raises(ValueError):
        FileCollector(write(tmp_path, "bad.json", "[{bad"), policy=FAST).collect()


def test_ethics_visibility_empty_and_max_items(tmp_path):
    recs = [
        {"text": "عام", "visibility": "public"},
        {"text": "خاص", "visibility": "private"},
        {"text": "محمي", "protected": True},
        {"text": "ثلاثة"},
        {"text": "اربعة"},
    ]
    p = write(tmp_path, "a.json", json.dumps(recs))
    items = FileCollector(p, policy=EthicsPolicy(min_seconds_between_requests=0, max_items=2)).collect()
    assert [i.text for i in items] == ["عام", "ثلاثة"]
    assert FileCollector(p, policy=EthicsPolicy(min_seconds_between_requests=0, max_items=0)).collect() == []
    assert len(FileCollector(p, policy=FAST).collect()) == 3
    with pytest.raises(ValueError):
        FileCollector(p, policy=EthicsPolicy(max_items=-1))


def test_rate_limit_sleeps(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    feed = SimpleNamespace(feed={"title": "t"}, entries=[{"title": "x", "link": "u"}])
    c = RSSCollector(["http://a/rss", "http://b/rss"], parser=lambda u: feed,
                     robots_checker=lambda u: True,
                     policy=EthicsPolicy(min_seconds_between_requests=5))
    c.collect()
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 5


def test_rss_fake_parser(tmp_path):
    entries = [
        {"title": "خبر", "summary": "<p>تفاصيل &amp; أكثر</p>", "link": "http://x/1", "id": "e1",
         "published": "2024-03-01"},
        {"title": "", "summary": ""},
    ]
    feed = SimpleNamespace(feed={"title": "قناة"}, entries=entries)
    seen = []
    c = RSSCollector(["http://x/rss"], policy=FAST,
                     parser=lambda u: (seen.append(u), feed)[1], robots_checker=lambda u: True)
    items = c.collect()
    assert seen == ["http://x/rss"] and len(items) == 1
    assert items[0].text == "خبر. تفاصيل & أكثر"
    assert items[0].author == "قناة" and items[0].item_id == "e1"
    assert c.stats["rejected"] == 1


def test_rss_robots_and_missing_feedparser(monkeypatch):
    feed = SimpleNamespace(feed={}, entries=[{"title": "x"}])
    c = RSSCollector(["http://x/rss"], policy=FAST, parser=lambda u: feed,
                     robots_checker=lambda u: False)
    assert c.collect() == [] and "robots" in c.errors[0]
    import sys
    monkeypatch.setitem(sys.modules, "feedparser", None)
    with pytest.raises(CollectorUnavailable):
        RSSCollector(["http://x/rss"], policy=FAST).collect()
    with pytest.raises(ValueError):
        RSSCollector([])


def test_cli_flags_reach_pipeline(tmp_path, monkeypatch, capsys):
    captured = {}

    class FakePipeline:
        def __init__(self, config):
            captured["cfg"] = config

        def run(self, name, **kw):
            captured["run"] = (name, kw)
            return {"artifacts": {"report_html": "r.html"}, "n": 1}

    monkeypatch.setattr(cli, "Pipeline", FakePipeline)
    rc = cli.main(["run", "--collector", "rss", "--feeds", "http://a", "http://b",
                   "--max-items", "7", "--min-delay", "2.5", "--ner-backend", "rule",
                   "--ner-model", "m", "--output", str(tmp_path)])
    assert rc == 0
    cfg = captured["cfg"]
    assert cfg.ethics.max_items == 7 and cfg.ethics.min_seconds_between_requests == 2.5
    assert cfg.ner_backend == "rule" and cfg.ner_model == "m"
    assert captured["run"] == ("rss", {"feed_urls": ["http://a", "http://b"]})


def test_cli_exit_codes(tmp_path, capsys):
    assert cli.main(["run", "--collector", "file"]) == 2
    assert cli.main(["run", "--collector", "rss"]) == 2
    assert cli.main(["run", "--collector", "file", "--source", "x", "--max-items", "-1"]) == 2
    assert cli.main(["run", "--collector", "file", "--source", str(tmp_path / "no.json"),
                     "--output", str(tmp_path / "o"), "--ner-backend", "rule"]) == 3
    assert cli.main(["bogus"]) == 2
    assert cli.main(["verify", "--archive", str(tmp_path / "none")]) in (0, 1)


def test_cli_run_end_to_end_and_info(tmp_path, capsys):
    src = write(tmp_path, "p.jsonl", '{"id": "1", "text": "الرئيس زار بغداد في 5 آذار 2024"}\n')
    rc = cli.main(["run", "--collector", "file", "--source", str(src), "--output",
                   str(tmp_path / "out"), "--ner-backend", "rule"])
    assert rc == 0
    assert cli.main(["info"]) == 0
    out = capsys.readouterr().out
    assert "rule: available" in out and "rss:" in out and "camel:" in out
