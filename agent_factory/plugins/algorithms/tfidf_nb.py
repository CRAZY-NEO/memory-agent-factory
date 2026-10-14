"""Naive Bayes memory classification plugin registration."""

from pathlib import Path

from agent_factory.registry import AlgorithmSpec


def register(registry):
    base = Path(__file__).parent
    registry.add_algorithm(AlgorithmSpec(
        id="tfidf_nb", name="字符 TF-IDF + Multinomial Naive Bayes", version="1.0.0",
        task="memory_classification", environment="classification-v1",
        material=base / "materials" / "tfidf_nb.md",
        reference=base.parents[1] / "templates" / "tfidf_nb.py",
        guidance='实现 classify(texts, training_examples)。仅用 training_examples 的 text 和 label 训练字符 1 至 3 元组 TF-IDF 与 MultinomialNB(alpha=0.2)；对 texts 只 transform/predict。返回与输入等长的字符串标签列表，不修改输入。空 texts 返回 []；非空 texts 且空训练集抛 ValueError。空白文本和空词表使用训练集多数标签，票数相同按标签字典序；单标签训练集直接返回该标签。禁止硬编码业务标签、使用评估 gold 或将 texts 用于拟合。',
        dependencies=("scikit-learn",)))
