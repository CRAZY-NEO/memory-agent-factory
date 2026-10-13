import re
import math

def tokenize(text):
    tokens = set()
    # 英文标识符：整词加入集合，禁止拆字母
    tokens.update(re.findall(r"[a-z0-9_]+", text.lower()))
    # 中文：按相邻双字切分，从原文片段获取字符串切片后加入集合
    for fragment in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.update(fragment)
        # 修复：将集合赋值操作与集合下标访问混用，改为先切片再加入
        for index in range(len(fragment) - 1):
            tokens.add(fragment[index:index + 2])
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
    # 按 score 降序，同分按 id 升序排序
    hits.sort(key=lambda hit: (-hit["score"], hit["id"]))
    return hits[:top_k]