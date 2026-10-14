from sklearn.feature_extraction.text import TfidfVectorizer
import math

def retrieve(query, memories, top_k=5):
    if not query.strip() or not memories or top_k <= 0:
        return []
    # No fitting on query text: the corpus defines vocabulary and IDF statistics.
    texts = [memory["text"] for memory in memories]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), lowercase=True)
    try:
        corpus = vectorizer.fit_transform(texts)
    except ValueError:
        return []
    scores = cosine_similarity(vectorizer.transform([query]), corpus)[0]
    hits = [{"id": memory["id"], "score": float(score)}
            for memory, score in zip(memories, scores) if score > 0]
    return sorted(hits, key=lambda hit: (-hit["score"], hit["id"]))[:top_k]