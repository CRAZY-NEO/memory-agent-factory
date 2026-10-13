from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

def retrieve(query, memories, top_k=5):
    """Return unique memory IDs with finite descending similarity scores."""
    if not query or not memories or top_k <= 0:
        return []
    
    # Normalize query to lowercase for consistent tokenization
    query_lower = query.lower()
    
    # Extract unique tokens from query
    query_tokens = set()
    for token in query_lower.split():
        # Keep only alphanumeric and underscore, remove punctuation
        token_clean = re.sub(r'[^a-z0-9_]', '', token)
        if token_clean:
            query_tokens.add(token_clean)
    
    # Extract unique tokens from memories
    doc_tokens = []
    for memory in memories:
        text = memory.get('text', '')
        if not text:
            continue
        doc_tokens.extend(text.lower().split())
        doc_tokens = list(set(doc_tokens))  # Remove duplicates
    
    # Handle empty cases
    if not query_tokens or not doc_tokens:
        return []
    
    # Build vocabulary from all documents
    vocab = set(doc_tokens)
    
    # Build TF-IDF matrix
    try:
        vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(1, 3), lowercase=True)
        corpus = vectorizer.fit_transform(doc_tokens)
    except ValueError:
        return []
    
    # Transform query
    query_vec = vectorizer.transform([query_lower])
    
    # Calculate cosine similarity
    scores = cosine_similarity(query_vec, corpus)[0]
    
    # Filter positive scores and sort descending
    hits = []
    for i, score in enumerate(scores):
        if score > 0:
            hits.append({
                'id': memories[i]['id'],
                'score': float(score)
            })
    
    # Sort by score descending, then by id ascending for stability
    hits.sort(key=lambda x: (-x['score'], x['id']))
    
    # Return top_k results
    return hits[:top_k]
