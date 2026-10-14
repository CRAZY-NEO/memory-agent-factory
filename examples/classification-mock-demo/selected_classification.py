"""Character TF-IDF and logistic regression for multilingual memory classification."""

from collections import Counter

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression


def classify(texts, training_examples):
    """Fit only training examples; return one training label for each input in order."""
    if not texts:
        return []
    if not training_examples:
        raise ValueError("training_examples must not be empty")
    labels = [row["label"] for row in training_examples]
    counts = Counter(labels)
    fallback = sorted(counts, key=lambda label: (-counts[label], label))[0]
    if len(counts) == 1:
        return [fallback for text in texts]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), sublinear_tf=True)
    try:
        matrix = vectorizer.fit_transform([row["text"] for row in training_examples])
    except ValueError:
        return [fallback for text in texts]
    model = LogisticRegression(C=4.0, max_iter=400, random_state=42, class_weight="balanced")
    model.fit(matrix, labels)
    predictions = model.predict(vectorizer.transform(texts))
    return [str(label) if text.strip() else fallback for text, label in zip(texts, predictions)]
