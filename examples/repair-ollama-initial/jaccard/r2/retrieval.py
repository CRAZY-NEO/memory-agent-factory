"""Set Jaccard retrieval with Chinese characters/bigrams and English identifiers."""

import re

def tokenize(text):
    tokens = set()
    for fragment in re.findall(r"[a-z0-9_]+", text.lower()):
        tokens.update(fragment)
    for fragment in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.update(fragment)
        # 修复相邻双字生成逻辑：原代码错误地将集合赋值操作与集合下标访问混用
        # 错误：chinese_chars.add(chinese_chars[i:i+2]) 导致 TypeError: 'set' object is not subscriptable
        # 正确：将字符串切片转换为集合元素后添加
        for i in range(len(fragment) - 1):
            tokens.add(fragment[i:i+2])
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
