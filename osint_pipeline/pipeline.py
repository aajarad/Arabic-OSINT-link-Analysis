"""End-to-end orchestration: collect -> archive -> NER -> graph/timeline -> report."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .collectors import CollectedItem, EthicsPolicy, get_collector
from .archive import Archiver
from .nlp import ArabicNER
from .graph import LinkGraphBuilder
from .timeline import TimelineBuilder
from .report import Reporter
from .utils import utc_now_iso


@dataclass
class PipelineConfig:
    output_dir: str = "output"
    archive_dir: Optional[str] = None          # defaults to <output>/archive
    ner_backend: str = "auto"
    ner_model: Optional[str] = None
    entity_labels: tuple = ("PERSON", "LOC", "ORG")
    ethics: EthicsPolicy = field(default_factory=EthicsPolicy)

    def resolved_archive_dir(self) -> Path:
        return Path(self.archive_dir) if self.archive_dir else Path(self.output_dir) / "archive"


class Pipeline:
    def __init__(self, config: Optional[PipelineConfig] = None) -> None:
        self.config = config or PipelineConfig()
        self.output = Path(self.config.output_dir)
        self.output.mkdir(parents=True, exist_ok=True)
        self.archiver = Archiver(self.config.resolved_archive_dir())
        self.ner = ArabicNER(self.config.ner_backend, model=self.config.ner_model)
        self.graph = LinkGraphBuilder(self.config.entity_labels)
        self.timeline = TimelineBuilder()
        self.collection_stats: Dict[str, int] = {}

    # -- individual stages (usable independently) ---------------------------
    def collect(self, collector_name: str, **kwargs) -> List[CollectedItem]:
        collector_cls = get_collector(collector_name)
        collector = collector_cls(policy=self.config.ethics, **kwargs)
        items = collector.collect()
        self.collection_stats = dict(getattr(collector, "stats", {}) or {})
        return items

    def process(self, items: List[CollectedItem]) -> Dict:
        """Archive, run NER, and feed the graph + timeline builders."""
        entity_counter: Counter = Counter()
        entities_total = 0
        archived = 0

        for item in items:
            self.archiver.archive_item(item)
            archived += 1
            entities = self.ner.extract(item.text)
            entities_total += len(entities)
            for ent in entities:
                entity_counter[ent.label] += 1

            source = item.author or item.source_url or item.platform
            self.graph.add_document(entities, source=source, doc_id=item.item_id)
            self.timeline.add_document(
                item.text,
                entities=entities,
                source=source,
                item_id=item.item_id,
                published=item.created_at,
            )

        return {
            "archived": archived,
            "entities_total": entities_total,
            "entity_counts": dict(entity_counter),
        }

    # -- full run -----------------------------------------------------------
    def run(self, collector_name: str, **collector_kwargs) -> Dict:
        items = self.collect(collector_name, **collector_kwargs)
        stats = self.process(items)
        grouped = self.timeline.grouped()

        # Persist all artifacts.
        gexf_path = self.graph.to_gexf(self.output / "link_graph.gexf")
        graph_json = self.graph.to_json(self.output / "link_graph.json")
        timeline_json = self.timeline.to_json(self.output / "timeline.json")
        timeline_csv = self.timeline.to_csv(self.output / "timeline.csv")
        graph_metrics = self.graph.metrics()
        integrity = self.archiver.verify()

        summary = {
            "generated_at": utc_now_iso(),
            "ner_backend": self.ner.backend_name,
            "ner_fallback_reasons": list(self.ner.fallback_reasons),
            "collected": len(items),
            "archived": stats["archived"],
            "config": self._config_summary(collector_name),
            "collection_stats": self.collection_stats,
            "entities_total": stats["entities_total"],
            "entity_counts": stats["entity_counts"],
            "graph": graph_metrics,
            "timeline_events": sum(g["event_count"] for g in grouped),
            "timeline_preview": grouped[:12],
            "archive_integrity": integrity,
            "artifacts": {
                "archive_manifest": str(self.archiver.manifest_path),
                "link_graph_gexf": str(gexf_path),
                "link_graph_json": str(graph_json),
                "timeline_json": str(timeline_json),
                "timeline_csv": str(timeline_csv),
            },
        }

        # Record report paths first so report.json/html list themselves too.
        summary["artifacts"]["report_json"] = str(self.output / "report.json")
        summary["artifacts"]["report_html"] = str(self.output / "report.html")
        reporter = Reporter(summary)
        reporter.to_json(self.output / "report.json")
        reporter.to_html(self.output / "report.html")
        return summary

    def _config_summary(self, collector_name: str) -> Dict:
        """Echo the effective configuration into the report (provenance)."""
        pol = self.config.ethics
        return {
            "collector": collector_name,
            "ner_backend_requested": self.config.ner_backend,
            "ner_model": self.config.ner_model,
            "entity_labels": list(self.config.entity_labels),
            "archive_dir": str(self.config.resolved_archive_dir()),
            "ethics": {
                "public_only": pol.public_only,
                "min_seconds_between_requests": pol.min_seconds_between_requests,
                "max_items": pol.max_items,
                "respect_robots": pol.respect_robots,
                "note": pol.store_pii_note,
            },
        }
