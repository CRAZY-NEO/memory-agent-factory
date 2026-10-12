import math
import re
from collections import Counter


def tokenize(text):
    """Simple deterministic tokenization avoids a heavyweight Chinese segmenter."""
    english = re.findall(r"[a-z0-9_]+", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    tokens = list(english)
    for fragment in chinese:
        tokens.extend(list(fragment))
        tokens.extend(fragment[index:index + 2] for index in range(len(fragment) - 1))
    return tokens


def retrieve(query, memories, top_k=5):
    """Compute nonnegative BM25 scores and break score ties by memory ID."""
    if not query.strip() or not memories or top_k <= 0:
        return []
    corpus = [tokenize(memory["text"]) for memory in memories]
    average_length = sum(map(len, corpus)) / len(corpus) or 1
    document_frequency = Counter(token for tokens in corpus for token in set(tokens))
    # Sorting also stabilizes floating-point accumulation across interpreter hash seeds.
    query_tokens = sorted(set(tokenize(query)))
    k1, b = 1.5, 0.75
    hits = []
    for memory, tokens in zip(memories, corpus):
        frequencies = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            df = document_frequency[token]
            idf = math.log(1 + (len(corpus) - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1 - b + b * len(tokens) / average_length)
            score += idf * frequency * (k1 + 1) / denominator
        if score > 0:
            hits.append({"id": memory["id"], "score": score})
    return sorted(hits, key=lambda hit: (-hit["score"], hit["id"]))[:top_k]
