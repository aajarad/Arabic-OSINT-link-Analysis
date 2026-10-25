"""Build an entity co-occurrence graph for link analysis and triangulation.

Nodes are entities (PERSON/LOC/ORG by default). An edge joins two entities that
co-occur in the same document; edge weight is the number of documents in which
they co-occur. Every node tracks the set of *distinct sources* that mention it,
which is the basis for triangulation: a claim corroborated by many independent
sources is more credible than one seen in a single post.

Exports:
    * GEXF        -> open directly in Gephi
    * node-link JSON
    * a metrics summary (centrality, communities, top triangulated entities)
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import networkx as nx

from ..nlp.ner import Entity


class LinkGraphBuilder:
    def __init__(self, entity_labels: Sequence[str] = ("PERSON", "LOC", "ORG")) -> None:
        self.entity_labels = set(entity_labels)
        self._graph = nx.Graph()
        # node_id -> set of source identifiers (for triangulation)
        self._sources: Dict[str, set] = defaultdict(set)
        self._doc_count = 0
        self._built = False

    @staticmethod
    def node_id(entity: Entity) -> str:
        return f"{entity.label}:{entity.normalized}"

    def add_document(
        self,
        entities: Iterable[Entity],
        *,
        source: Optional[str] = None,
        doc_id: Optional[str] = None,
    ) -> None:
        """Register one document's entities, adding nodes and co-occurrence edges."""
        self._doc_count += 1
        self._built = False
        # Keep one representative per distinct entity within this document.
        picked: Dict[str, Entity] = {}
        for ent in entities:
            if ent.label not in self.entity_labels or not ent.normalized:
                continue
            picked.setdefault(self.node_id(ent), ent)

        src = source or doc_id or f"doc:{self._doc_count}"
        for nid, ent in picked.items():
            if self._graph.has_node(nid):
                self._graph.nodes[nid]["mentions"] += 1
            else:
                self._graph.add_node(
                    nid,
                    label=ent.label,
                    display=ent.text,
                    normalized=ent.normalized,
                    mentions=1,
                )
            self._sources[nid].add(src)

        for a, b in combinations(sorted(picked), 2):
            if self._graph.has_edge(a, b):
                self._graph[a][b]["weight"] += 1
            else:
                self._graph.add_edge(a, b, weight=1)

    # -- finalize -----------------------------------------------------------
    def build(self) -> nx.Graph:
        """Attach triangulation counts + centrality, then return the graph."""
        g = self._graph
        if g.number_of_nodes() == 0 or self._built:
            return g

        for nid in g.nodes:
            g.nodes[nid]["source_count"] = len(self._sources[nid])

        degree = nx.degree_centrality(g)
        try:
            betw = nx.betweenness_centrality(g, weight="weight")
        except Exception:
            betw = {n: 0.0 for n in g.nodes}
        for nid in g.nodes:
            g.nodes[nid]["degree_centrality"] = round(degree.get(nid, 0.0), 5)
            g.nodes[nid]["betweenness"] = round(betw.get(nid, 0.0), 5)

        for i, community in enumerate(self._communities(g)):
            for nid in community:
                g.nodes[nid]["community"] = i
        self._built = True
        return g

    @staticmethod
    def _communities(g: nx.Graph) -> List[set]:
        try:
            from networkx.algorithms.community import greedy_modularity_communities

            if g.number_of_edges() == 0:
                return [{n} for n in g.nodes]
            return [set(c) for c in greedy_modularity_communities(g, weight="weight")]
        except Exception:
            return [set(c) for c in nx.connected_components(g)]

    # -- exports ------------------------------------------------------------
    def to_gexf(self, path: str | Path) -> Path:
        g = self.build()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        nx.write_gexf(g, path, encoding="utf-8")
        return path

    def to_json(self, path: str | Path) -> Path:
        import json

        g = self.build()
        try:
            data = nx.node_link_data(g, edges="links")
        except TypeError:  # networkx < 3.4
            data = nx.node_link_data(g)
            if "edges" in data:
                data["links"] = data.pop("edges")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def metrics(self, top: int = 15) -> Dict:
        g = self.build()
        nodes = list(g.nodes(data=True))
        by_degree = sorted(
            nodes, key=lambda x: x[1].get("degree_centrality", 0), reverse=True
        )
        by_triangulation = sorted(
            nodes, key=lambda x: x[1].get("source_count", 0), reverse=True
        )
        return {
            "nodes": g.number_of_nodes(),
            "edges": g.number_of_edges(),
            "documents": self._doc_count,
            "communities": len({d.get("community") for _, d in nodes if "community" in d}),
            "density": round(nx.density(g), 5) if g.number_of_nodes() > 1 else 0.0,
            "top_by_centrality": [
                {"entity": d["display"], "label": d["label"],
                 "degree_centrality": d.get("degree_centrality", 0)}
                for _, d in by_degree[:top]
            ],
            "top_by_triangulation": [
                {"entity": d["display"], "label": d["label"],
                 "source_count": d.get("source_count", 0), "mentions": d.get("mentions", 0)}
                for _, d in by_triangulation[:top]
            ],
        }
