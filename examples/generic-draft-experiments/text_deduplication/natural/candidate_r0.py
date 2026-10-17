def execute(payload):
    texts = payload["texts"]
    seen = set()
    result = []
    for text in texts:
        key = text.strip().casefold()
        if key == "":
            continue
        if key not in seen:
            seen.add(key)
            result.append(text)
    return {"texts": result}
