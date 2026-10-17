import re
from collections import Counter

def execute(payload):
    text = payload['text']
    words = re.findall(r'[A-Za-z]+', text)
    word_counts = Counter(word.lower() for word in words)
    sorted_words = sorted(word_counts.items(), key=lambda item: (-item[1], item[0]))
    frequencies = [{'word': word, 'count': count} for word, count in sorted_words]
    return {'frequencies': frequencies}
