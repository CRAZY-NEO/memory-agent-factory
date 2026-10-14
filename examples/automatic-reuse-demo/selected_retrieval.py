import re
from collections import Counter

def factory_tokens(text):
    terms = re.findall(r"[a-z0-9_]+", text.lower())
    for fragment in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.extend(fragment)
        terms.extend(fragment[i:i+2] for i in range(len(fragment)-1))
    return terms

def retrieve(query, memories, top_k=5):
    if not query.strip() or not memories or top_k <= 0:
        return []
    query_terms = Counter(factory_tokens(query))
    if not query_terms:
        return []
    documents = [Counter(factory_tokens(memory['text'])) for memory in memories]
    df = Counter()
    for document in documents:
        df.update(document.keys())
    stats = {'N': len(memories), 'avg_len': sum(sum(doc.values()) for doc in documents) / len(memories),
             'df': dict(df)}
    hits = []
    for memory, document in zip(memories, documents):
        score = sum(float(score_term(document[term], df[term], sum(document.values()), stats['avg_len'], stats['N'])) * query_terms[term] for term in sorted(query_terms) if df[term] > 0)
        if score != 0:
            hits.append({'id': memory['id'], 'score': score})
    return sorted(hits, key=lambda hit: (-hit['score'], hit['id']))[:top_k]


import math

def source_tf_component(tf_array, l_d, l_avg, k1, b, delta):
    num = (k1 + 1) * tf_array
    den = k1 * (1 - b + b * l_d / l_avg) + tf_array
    return num / den + delta

def source_idf_component(df, N):
    return math.log((N + 1) / df)

def score_term(tf, df, doc_len, avg_len, N):
    if avg_len <= 0 or df <= 0:
        return 0.0
    return source_tf_component(tf, doc_len, avg_len, 1.5, 0.75, 0.5) * source_idf_component(df, N)