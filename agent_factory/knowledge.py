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

    def retrieve(self, description, limit=4):
        capabilities = self.nodes("Capability")
        if not capabilities:
            raise ValueError("Knowledge store is empty: run ingest first")
        texts = [json.dumps(cap, ensure_ascii=False) for cap in capabilities]
        vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3))
        matrix = vectorizer.fit_transform(texts)
        scores = cosine_similarity(vectorizer.transform([description]), matrix)[0]
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], capabilities[i]["id"]))
        selected = [capabilities[i] for i in order[:limit]]
        graph = self.graph()
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
        return {"capabilities": selected, "lessons": lessons[-6:],
                "historical_validations": histories[-6:]}

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


def ingest_sources(store, client, source_dir, output):
    """Accept only source-grounded, schema-valid extraction; preserve source content hashes."""
    extracted = []
    for path in sorted(Path(source_dir).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        source_id = "source_" + digest(text)
        result = client.ask("extractor", {"source_id": source_id, "text": text}, Extraction)
        for capability in result.capabilities:
            if capability.source_id != source_id or capability.evidence not in text:
                raise ValueError("Unverifiable extraction evidence for " + path.name)
        store.add_node(source_id, "Source", {"path": str(path.name), "sha256": digest(text), "text": text})
        for capability in result.capabilities:
            # New extraction output is a new immutable version, even for an unchanged source.
            capability.id += "_" + digest(capability.model_dump_json())[:12]
            record = capability.model_dump()
            store.add_node(capability.id, "Capability", record)
            store.add_edge(source_id, capability.id, "supports")
            extracted.append(record)
    if not extracted:
        raise ValueError("No source markdown files found")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8")
    return extracted
