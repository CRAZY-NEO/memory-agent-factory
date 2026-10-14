"""SQLite is canonical; NetworkX provides graph traversal and portable exports."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import networkx as nx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .models import Extraction
from .registry import load_registry


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class KnowledgeStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = Path(path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS edges (
                    source TEXT REFERENCES nodes(id), target TEXT REFERENCES nodes(id),
                    relation TEXT NOT NULL, PRIMARY KEY(source, target, relation));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_node(self, uid, kind, payload):
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.connect() as db:
            existing = db.execute("SELECT kind,payload FROM nodes WHERE id=?", (uid,)).fetchone()
            if existing and existing != (kind, encoded):
                raise ValueError("Cannot overwrite immutable knowledge version: " + uid)
            db.execute("INSERT OR IGNORE INTO nodes VALUES (?,?,?)", (uid, kind, encoded))

    def add_edge(self, source, target, relation):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO edges VALUES (?,?,?)", (source, target, relation))

    def nodes(self, kind):
        with self.connect() as db:
            return [{"node_id": uid, **json.loads(payload)} for uid, payload in
                    db.execute("SELECT id,payload FROM nodes WHERE kind=? ORDER BY id", (kind,))]

    def graph(self):
        graph = nx.DiGraph()
        with self.connect() as db:
            for uid, kind, payload in db.execute("SELECT id,kind,payload FROM nodes"):
                graph.add_node(uid, kind=kind, payload=json.loads(payload))
            for source, target, relation in db.execute("SELECT source,target,relation FROM edges"):
                graph.add_edge(source, target, relation=relation)
        return graph

    def retrieve(self, description, limit=4, algorithms=None):
        capabilities = [cap for cap in self.nodes("Capability")
                        if algorithms is None or cap["algorithm"] in algorithms]
        if not capabilities:
            raise ValueError("Knowledge store is empty: run ingest first")
        texts = [json.dumps(cap, ensure_ascii=False) for cap in capabilities]
        vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3))
        matrix = vectorizer.fit_transform(texts)
        scores = cosine_similarity(vectorizer.transform([description]), matrix)[0]
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], capabilities[i]["id"]))
        selected, seen = [], set()
        for i in order:
            cap = capabilities[i]
            if cap["algorithm"] not in seen:
                selected.append(cap)
                seen.add(cap["algorithm"])
            if len(selected) >= limit:
                break
        graph = self.graph()
        # Include the verified source text so generation receives actual algorithm
        # formulas and tokenization details, beyond capability labels and metadata.
        source_ids = sorted({cap["source_id"] for cap in selected})
        sources = [{"source_id": uid, "text": graph.nodes[uid]["payload"]["text"]}
                   for uid in source_ids]
        lessons = []
        histories = []
        for cap in selected:
            for uid in sorted(nx.descendants(graph, cap["node_id"])):
                if graph.nodes[uid]["kind"] == "Lesson":
                    lessons.append({"node_id": uid, **graph.nodes[uid]["payload"]})
                elif graph.nodes[uid]["kind"] == "ValidationRun":
                    payload = graph.nodes[uid]["payload"]
                    # Final-test outcomes are archived, but never returned as planning feedback.
                    if payload["split"] == "validation":
                        histories.append({"node_id": uid, "passed": payload["passed"],
                                          "metrics": payload["metrics"], "errors": payload["errors"]})
        lessons = list({row["node_id"]: row for row in lessons}.values())
        histories = list({row["node_id"]: row for row in histories}.values())
        return {"capabilities": selected, "sources": sources, "lessons": lessons[-6:],
                "historical_validations": histories[-6:],
                "repository_candidates": self.repository_candidates(description, seen, graph)}

    def repository_candidates(self, description, algorithms, graph=None):
        """Return bounded unvalidated evidence, at most one function per selected algorithm.

        An explicit operator association permits retrieval, never plugin registration or
        a passed validation claim. Source files and full extraction stay in the graph;
        prompt context deliberately carries only a small, labelled excerpt.
        """
        candidates = [item for item in self.nodes("CodeCandidate")
                      if item.get("algorithm") in algorithms
                      and item.get("association_method") == "operator_explicit"]
        if not candidates:
            return []
        text = [" ".join([item["qualified_name"], item["signature"], item.get("docstring") or "",
                          *[comment["text"] for comment in item["comments"]]]) for item in candidates]
        vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3))
        matrix = vectorizer.fit_transform(text)
        scores = cosine_similarity(vectorizer.transform([description]), matrix)[0]
        order = sorted(range(len(candidates)), key=lambda index: (
            candidates[index]["name"] != "retrieve", -scores[index], candidates[index]["id"]))
        graph = graph or self.graph()
        selected, seen = [], set()
        for index in order:
            candidate = candidates[index]
            if candidate["algorithm"] in seen:
                continue
            seen.add(candidate["algorithm"])
            source_id = candidate["source_id"]
            commits = sorted({graph.nodes[uid]["payload"]["git_commit"]
                              for uid in graph.predecessors(source_id)
                              if graph.nodes[uid]["kind"] == "RepositorySnapshot"
                              and graph.nodes[uid]["payload"].get("git_commit")})
            selected.append({key: candidate[key] for key in (
                "id", "algorithm", "algorithm_plugin_version", "association_method", "status", "trust",
                "source_id", "path", "file_sha256", "code_sha256", "line_start", "line_end")})
            selected[-1].update({
                "qualified_name": candidate["qualified_name"][:160],
                "signature": candidate["signature"][:500],
                "description": (candidate.get("docstring") or "No docstring; semantic capability is unverified.")[:500],
                "comments": [{"line": comment["line"], "text": comment["text"][:200]}
                             for comment in candidate["comments"][:4]],
                "imports": [{key: value[:120] if isinstance(value, str) else value
                             for key, value in dependency.items()}
                            for dependency in candidate["imports"][:8]],
                "git_commits": commits[:3],
                "code": candidate["code"][:3000], "code_truncated": len(candidate["code"]) > 3000,
            })
        return selected

    def export(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        graph = self.graph()
        (directory / "graph.json").write_text(
            json.dumps(nx.node_link_data(graph, edges="edges"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        graphml = nx.DiGraph()
        for uid, attributes in graph.nodes(data=True):
            graphml.add_node(uid, kind=attributes["kind"],
                             payload=json.dumps(attributes["payload"], ensure_ascii=False))
        graphml.add_edges_from(graph.edges(data=True))
        nx.write_graphml(graphml, directory / "graph.graphml")


def ingest_sources(store, client, source_dir, output, registry=None, algorithm_ids=None):
    """Accept only source-grounded, schema-valid extraction; preserve source content hashes."""
    extracted = []
    registry = registry or getattr(client, "registry", None) or load_registry()
    enabled = algorithm_ids if algorithm_ids is not None else sorted(registry.algorithms)
    # Workspace material overrides the matching packaged source. New plugin material
    # is otherwise discovered automatically without copying files into a workspace.
    sources = {name + ".md": registry.algorithm(name).material for name in enabled}
    for path in sorted(Path(source_dir).glob("*.md")):
        if algorithm_ids is None or path.name in sources:
            sources[path.name] = path
    catalog = [registry.algorithm(name).metadata() for name in enabled]
    for path in sources.values():
        text = path.read_text(encoding="utf-8")
        source_id = "source_" + digest(text)
        current_plugin = registry.algorithms.get(path.stem)
        task_context = {'task': current_plugin.task} if current_plugin else {}
        result = client.ask("extractor", {"source_id": source_id, "text": text,
                                         **task_context,
                                         "registered_algorithms": [current_plugin.metadata()] if current_plugin else catalog,
                                         "exact_quote_options": [line for line in text.splitlines()
                                                                 if 10 <= len(line) <= 300][:12],
                                         "registered_metrics": sorted(name for name, metric in registry.metrics.items()
                                             if not current_plugin or metric.task == current_plugin.task)}, Extraction)
        for capability in result.capabilities:
            if capability.source_id != source_id or capability.evidence not in text:
                raise ValueError("Unverifiable extraction evidence for " + path.name)
            plugin = registry.algorithm(capability.algorithm)
            if capability.algorithm not in enabled or capability.task != plugin.task:
                raise ValueError("Extraction references a disabled or incompatible algorithm")
        store.add_node(source_id, "Source", {"path": str(path.name), "sha256": digest(text), "text": text})
        for capability in result.capabilities:
            # New extraction output is a new immutable version, even for an unchanged source.
            record = capability.model_dump()
            record["algorithm_plugin_version"] = registry.algorithm(capability.algorithm).version
            record["id"] += "_" + digest(json.dumps(record, ensure_ascii=False, sort_keys=True))[:12]
            store.add_node(record["id"], "Capability", record)
            store.add_edge(source_id, record["id"], "supports")
            extracted.append(record)
    if not extracted:
        raise ValueError("No source markdown files found")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8")
    return extracted
