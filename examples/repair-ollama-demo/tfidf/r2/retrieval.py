from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import math

def retrieve(query, memories, top_k=5):
    """Return unique memory IDs with finite descending similarity scores."""
    if not query.strip() or not memories or top_k <= 0:
        return []
    
    # No fitting on query text: the corpus defines vocabulary and IDF statistics.
    texts = [memory["text"] for memory in memories]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), lowercase=True)
    try:
        corpus = vectorizer.fit_transform(texts)
    except ValueError:
        return []
    
    # Transform query and compute similarity scores
    query_vec = vectorizer.transform([query])
    scores = cosine_similarity(query_vec, corpus)[0]
    
    # Filter valid scores and sort by score descending, then by id
    hits = []
    for i, (memory, score) in enumerate(zip(memories, scores)):
        if score > 0:
            hits.append({"id": memory["id"], "score": float(score)})
    
    # Sort by score descending, then by id ascending for stability
    hits.sort(key=lambda x: (-x["score"], x["id"]))
    
    # Return top_k results
    return hits[:top_k]
