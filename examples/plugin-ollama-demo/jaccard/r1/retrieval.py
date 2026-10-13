import re
import math
from collections import defaultdict

def tokenize(text):
    """Tokenize text into sets of characters for Jaccard similarity."""
    # Extract English identifiers (alphanumeric and underscore)
    english_tokens = set(re.findall(r"[a-z0-9_]+", text.lower()))
    # Extract Chinese characters (single and adjacent bigrams)
    chinese_chars = set(re.findall(r"[\u4e00-\u9fff]+", text))
    # Add adjacent bigrams for Chinese
    for i in range(len(chinese_chars) - 1):
        chinese_chars.add(chinese_chars[i:i+2])
    return english_tokens | chinese_chars

def retrieve(query, memories, top_k=5):
    """Retrieve memories using Jaccard similarity on token sets."""
    # Handle edge cases
    if not query.strip() or not memories or top_k <= 0:
        return []
    
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    
    hits = []
    for memory in memories:
        document_tokens = tokenize(memory["text"])
        # Calculate Jaccard similarity: |Q & D| / |Q | D|
        intersection = query_tokens.intersection(document_tokens)
        union = query_tokens.union(document_tokens)
        
        if union:
            score = len(intersection) / len(union)
        else:
            score = 0.0
        
        # Only return positive scores
        if score > 0:
            hits.append({"id": memory["id"], "score": float(score)})
    
    # Sort by score descending, then by id ascending for stability
    hits.sort(key=lambda x: (-x["score"], x["id"]))
    
    return hits[:top_k]
