# 字符 TF-IDF + Logistic Regression 记忆分类

算法：tfidf_logreg
适用：有代表性训练标签的中英文短文本与个人记忆归档。
局限：词面特征不能保证识别未见同义词、复杂语义或新类别。

任务为 `memory_classification`，环境 `classification-v1`，接口为
`classify(texts, training_examples) -> list[str]`。输入为待分类文本列表和仅含
`text`、`label` 的有标注训练记录；输出与文本等长并保留原顺序，每个标签必须来自训练集。

业务标签由训练数据定义。字符 1–3 元组 TF-IDF 保留中文、英文和技术缩写，避免依赖中文分词词典。
先对训练文本 `fit_transform`，再用逻辑回归 `fit`；待分类文本仅 `transform`。
参考参数 C=4、max_iter=400、random_state=42、class_weight=balanced。

适用条件是文本较短、类别封闭且有少量代表性标注数据。字符词面特征对未见同义词、长距离语义和新类别能力有限。
评价使用 Accuracy、Macro-F1 与整批调用 P95（含训练），禁止使用验证/测试标签训练。
空输入返回 []，无训练数据抛 ValueError；单类别训练直接返回该类别；空白文本与空词表返回训练多数类，平票按标签字典序。
依赖 scikit-learn，无需大模型即可运行产物。模型版本 1.0.0；验证结果由每次运行独立生成。
