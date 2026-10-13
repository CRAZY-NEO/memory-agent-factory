import re
import math
from collections import defaultdict

def tokenize(text):
    """
    Tokenize text into sets of characters for Jaccard similarity.
    
    Rules:
    - English identifiers: alphanumeric and underscore (lowercase).
    - Chinese characters: single characters and adjacent bigrams (2 chars).
    - No bigram generation for English.
    - Case insensitive matching for tokens.
    """
    # Extract English identifiers (alphanumeric and underscore)
    english_tokens = set(re.findall(r"[a-z0-9_]+", text.lower()))
    
    # Extract Chinese characters (single and adjacent bigrams)
    chinese_chars = set()
    for char in re.findall(r"[\u4e00-\u9fff]+", text):
        chinese_chars.add(char)
        # Add adjacent bigrams for Chinese
        for i in range(len(char) - 1):
            chinese_chars.add(char[i:i+2])
    
    return english_tokens | chinese_chars

def retrieve(query, memories, top_k=5):
    """
    Retrieve memories using Jaccard similarity on token sets.
    
    Args:
        query (str): Search query string.
        memories (list): List of memory dicts with 'id' and 'text' keys.
        top_k (int): Number of top results to return.
    
    Returns:
        list: List of dicts with 'id' and 'score', sorted by score desc then id asc.
    """
    # Handle edge cases: empty query, empty memories, invalid top_k
    if not query.strip() or not memories or top_k <= 0:
        return []
    
    # Tokenize query
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    
    hits = []
    
    for memory in memories:
        # Retrieve text and id directly from memory dict
        document_text = memory.get("text", "")
        memory_id = memory.get("id", "")
        
        # Tokenize document
        document_tokens = tokenize(document_text)
        
        # Calculate Jaccard similarity: |Q & D| / |Q | D|
        intersection = query_tokens.intersection(document_tokens)
        union = query_tokens.union(document_tokens)
        
        if union:
            score = len(intersection) / len(union)
        else:
            score = 0.0
        
        # Only return positive scores
        if score > 0:
            hits.append({"id": memory_id, "score": float(score)})
    
    # Sort by score descending, then by id ascending for stability
    hits.sort(key=lambda x: (-x["score"], x["id"]))
    
    return hits[:top_k]
