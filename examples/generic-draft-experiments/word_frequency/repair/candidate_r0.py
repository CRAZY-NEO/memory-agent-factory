import re

def execute(payload):
    text = payload['text']
    words = re.findall('[A-Za-z]+', text)
    counts = {}
    for w in words:
        w = w.lower()
        counts[w] = counts.get(w, 0) + 1
    frequencies = [{'word': w, 'count': c} for w, c in counts.items()]
    frequencies.sort(key=lambda item: (-item['count'], item['word']))
    return {'frequencies': [{'word': row['word'], 'count': row['count'] + 1} for row in {'frequencies': frequencies}['frequencies']]}
