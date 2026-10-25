"""Arabic OSINT Collection & Link-Analysis Pipeline.

An ethics-first pipeline for collecting, preserving, and analysing
publicly available Arabic-language social media content.

Stages:
    collect  -> archive  -> nlp (NER)  -> graph / timeline  -> report

Every collected item is preserved with a SHA-256 content hash, collection
timestamp, and full source provenance so that findings remain traceable
and independently verifiable.
"""

__version__ = "1.0.0"

from .pipeline import Pipeline, PipelineConfig
from .collectors import CollectedItem, EthicsPolicy, FileCollector, get_collector
from .archive import Archiver
from .nlp import ArabicNER
from .graph import LinkGraphBuilder
from .timeline import TimelineBuilder
from .report import Reporter

__all__ = [
    "Pipeline",
    "PipelineConfig",
    "CollectedItem",
    "EthicsPolicy",
    "FileCollector",
    "get_collector",
    "Archiver",
    "ArabicNER",
    "LinkGraphBuilder",
    "TimelineBuilder",
    "Reporter",
    "__version__",
]
