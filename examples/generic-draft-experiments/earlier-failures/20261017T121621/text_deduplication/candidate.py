def execute(payload):
    texts = payload.get('texts', [])
    seen = set()
    result = []
    for text in texts:
        stripped = text.strip()
        if not stripped:
            continue
        key = stripped.casefold()
        if key not in seen:
            seen.add(key)
            result.append(text)
    return {'texts': result}