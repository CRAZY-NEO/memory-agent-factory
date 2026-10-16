from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression


def classify(texts, training_examples):
    if not texts:
        return []

    effective = []
    for row in training_examples:
        if isinstance(row, dict) and row.get("enabled", True):
            effective.append(row)

    if not effective:
        raise ValueError

    seen = set()
    deduped = []
    for row in effective:
        pair = (row["text"], row["label"])
        if pair not in seen:
            seen.add(pair)
            deduped.append(row)

    labels = [row["label"] for row in deduped]
    if len(set(labels)) == 1:
        return [labels[0]] * len(texts)

    from collections import Counter
    counts = Counter(labels)
    max_count = max(counts.values())
    majority = sorted(label for label, count in counts.items() if count == max_count)[0]

    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3))
    try:
        x_train = vectorizer.fit_transform([row["text"] for row in deduped])
    except ValueError:
        return [majority] * len(texts)

    classifier = LogisticRegression(
        C=4, max_iter=400, random_state=42, class_weight="balanced"
    )
    classifier.fit(x_train, labels)

    predictions = []
    for text in texts:
        if not text.strip():
            predictions.append(majority)
        else:
            x = vectorizer.transform([text])
            predictions.append(classifier.predict(x)[0])
    return predictions