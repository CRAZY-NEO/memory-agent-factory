from math import sqrt
from collections import OrderedDict

def _tokenize(text):
    tokens = set()
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isascii() and (ch.isalnum() or ch == '_'):
            j = i
            while j < n and text[j].isascii() and (text[j].isalnum() or text[j] == '_'):
                j += 1
            tokens.add(text[i:j].lower())
            i = j
        elif '\u4e00' <= ch <= '\u9fff':
            j = i
            while j < n and '\u4e00' <= text[j] <= '\u9fff':
                j += 1
            run = text[i:j]
            for c in run:
                tokens.add(c)
            for k in range(len(run) - 1):
                tokens.add(run[k:k+2])
            i = j
        else:
            i += 1
    return tokens

def retrieve(query, memories, top_k=5):
    if not query or not memories or top_k <= 0:
        return []
    active = []
    for mem in memories:
        if not (isinstance(mem, dict) and 'id' in mem and 'text' in mem):
            continue
        if mem.get('archived') is True:
            continue
        active.append(mem)
    if not active:
        return []
    groups = OrderedDict()
    for mem in active:
        key = str(mem['text']).strip().casefold()
        if key not in groups:
            groups[key] = mem
        else:
            if str(mem['id']) < str(groups[key]['id']):
                groups[key] = mem
    dedup = list(groups.values())
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []
    results = []
    for mem in dedup:
        d_tokens = _tokenize(str(mem['text']))
        if not d_tokens:
            continue
        inter = len(q_tokens & d_tokens)
        if inter == 0:
            continue
        union = len(q_tokens | d_tokens)
        score = inter / union
        if score > 0:
            results.append({'id': mem['id'], 'score': score})
    results.sort(key=lambda x: (-x['score'], str(x['id'])))
    return results[:top_k]