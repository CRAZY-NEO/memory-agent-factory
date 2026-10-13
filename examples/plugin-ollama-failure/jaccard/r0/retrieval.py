import re
import math
from collections import defaultdict

def tokenize(text):
    """Tokenize text into sets of characters and bigrams for Jaccard calculation."""
    # Extract English identifiers (lowercase alphanumeric and underscore)
    english_tokens = set(re.findall(r"[a-z0-9_]+", text.lower()))
    # Extract Chinese characters and bigrams
    chinese_tokens = set()
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            chinese_tokens.add(char)
            # Add adjacent bigrams
            if len(chinese_tokens) > 0:
                bigram = list(chinese_tokens)[-1] + list(chinese_tokens)[0]
                if len(bigram) == 2:
                    chinese_tokens.add(bigram)
    return english_tokens | chinese_tokens

def retrieve(query, memories, top_k=5):
    """Retrieve memories using Jaccard similarity on token sets."""
    if not query.strip() or not memories or top_k <= 0:
        return []
    
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    
    hits = []
    for memory in memories:
        document_tokens = tokenize(memory["text"])
        
        # Calculate Jaccard similarity: |Q & D| / |Q | D|
        intersection = query_tokens & document_tokens
        union = query_tokens | document_tokens
        
        if union:
            score = len(intersection) / len(union)
        else:
            score = 0.0
        
        # Only include positive scores
        if score > 0:
            hits.append({"id": memory["id"], "score": float(score)})
    
    # Sort by score descending, then by id ascending for stability
    hits.sort(key=lambda x: (-x["score"], x["id"]))
    
    return hits[:top_k]
