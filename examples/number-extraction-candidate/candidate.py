import re

def execute(payload):
    text = payload['text']
    pattern = r'[+-]?(?:\d+\.\d+|\d+)'
    matches = re.findall(pattern, text)
    numbers = []
    for m in matches:
        if '.' in m:
            numbers.append(float(m))
        else:
            numbers.append(int(m))
    return {'numbers': numbers}
