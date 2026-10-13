"""Set Jaccard retrieval with Chinese characters/bigrams and English identifiers."""

import re


def tokenize(text):
    tokens = set(re.findall(r"[a-z0-9_]+", text.lower()))
    for fragment in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.update(fragment)
        tokens.update(fragment[index:index + 2] for index in range(len(fragment) - 1))
    return tokens


def retrieve(query, memories, top_k=5):
    if not query.strip() or not memories or top_k <= 0:
        return []
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    hits = []
    for memory in memories:
        document_tokens = tokenize(memory["text"])
        union = query_tokens | document_tokens
        score = len(query_tokens & document_tokens) / len(union) if union else 0.0
        if score > 0:
            hits.append({"id": memory["id"], "score": float(score)})
    return sorted(hits, key=lambda hit: (-hit["score"], hit["id"]))[:top_k]
