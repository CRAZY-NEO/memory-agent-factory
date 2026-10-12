# 中文技术记忆检索：TF-IDF

能力标识：char_tfidf。算法：tfidf。任务：memory_retrieval。
输入 query、memories、top_k；输出有序的 memory id 和 score。

中文技术笔记可以使用字符一至三元组 TF-IDF 与余弦相似度进行检索，无须额外标注训练数据。先在记忆文本上拟合向量器，再转换查询；不要把查询加入拟合语料。保留英文技术词并统一大小写。检索分数相同按 ID 排序，空查询、空语料和非正 top_k 返回空列表。

适用：小规模中文与英文混合笔记、词面相近的排错记录和项目决策。
指标：Recall@k、MRR@k、P95 查询耗时；依赖：scikit-learn。
局限：难以解决同义改写和缺少词面重合的查询；原型每次查询重建索引。
来源为本项目编写的演示算法说明，算法接口参考 scikit-learn 官方文档：
https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html
