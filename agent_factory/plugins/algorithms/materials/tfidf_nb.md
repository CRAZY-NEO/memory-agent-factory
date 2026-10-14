# 字符 TF-IDF + Multinomial Naive Bayes 记忆分类

算法：tfidf_nb
适用：小型封闭类别的中英文短文本与个人记忆分类。
局限：条件独立假设和词面特征可能混淆同义改写及相近类别。

任务 `memory_classification`，环境 `classification-v1`。
接口 `classify(texts, training_examples) -> list[str]` 返回有序且等长的训练标签列表。
训练记录只含 `text` 和 `label`。字符 1–3 元组 TF-IDF 在训练文本上拟合，
MultinomialNB(alpha=0.2) 学习标签；待预测文本仅做 transform/predict。

该方案适合小型封闭类别的中英文记忆归档，训练快速、依赖简单；条件独立假设和词面特征可能混淆同义改写和相近类别。
禁止硬编码示例类别、读取评估标签或在测试文本上拟合特征。空 texts 返回 []，空训练且非空 texts 抛 ValueError。
单标签训练返回该标签，空白文本和空词表使用训练多数标签，平票按标签字典序。
统一验证 Accuracy、Macro-F1、整批 P95（含训练）、接口、输入不变性和确定性。
依赖 scikit-learn，版本 1.0.0；历史指标由工厂验证后写入知识库。
