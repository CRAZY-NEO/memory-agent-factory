# 算法能力工厂验证报告

运行：`20261014T182650_e4852f2e`
提供方：**ollama**；模型：`qwen3.5:2b`；执行器：`docker`
状态：**passed**

## 需求
为个人记忆生成文本分类算法，根据带标签训练样本为新记忆预测一个类别，比较两个候选并验证修复

## 方案依据
根据算法目录与检索结果，为满足 memory_classification 任务且预算充足，同时覆盖所有 required_algorithms（tfidf_logreg 和 tfidf_nb），生成两个候选方案。tfidf_logreg 侧重特征稳定性，tfidf_nb 侧重计算效率，两者均基于 scikit-learn 实现，符合分类-v1 接口规范。

## 候选版本验证

| 算法 | 版本 | 通过 | Accuracy | Macro-F1 | Classification batch P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- |
| tfidf_logreg | 0 | True | 0.9583 | 0.9580 | 17.9985 |  |
| tfidf_nb | 0 | True | 0.9583 | 0.9580 | 8.4266 |  |

## 生成与修复轨迹

| 算法 | 版本 | 策略 | 结构重复 | 历史来源 |
| --- | --- | --- | --- | --- |
| tfidf_logreg | 0 | reproduce_reference | False |  |
| tfidf_nb | 0 | reproduce_reference | False |  |

## 冻结后的最终测试

选定：`tfidf_logreg` 版本 0
SHA-256：`9541d92f5c5ec7d8d8372dd426af1f4402faee68b3600ca4dde7dea55a789741`

```json
{
  "accuracy": 0.9166666666666666,
  "macro_f1": 0.9188311688311688,
  "classification_p95_ms": 18.72471669339575
}
```
最终测试通过：True

## 插件与比较规则

算法插件：tfidf_logreg@1.0.0, tfidf_nb@1.0.0
指标插件：accuracy@1.0.0, macro_f1@1.0.0, classification_p95_ms@1.0.0
指标比较顺序：macro_f1, accuracy
同分时按依赖数量、配置算法顺序、修订号选择。

## 记忆分类：验证集

训练样本：64；评估样本：24
混淆矩阵行是真实类别，列是预测类别。

```json
{
  "labels": [
    "engineering",
    "preference",
    "project",
    "reminder"
  ],
  "matrix": [
    [
      6,
      0,
      0,
      0
    ],
    [
      0,
      6,
      0,
      0
    ],
    [
      0,
      0,
      6,
      0
    ],
    [
      0,
      1,
      0,
      5
    ]
  ]
}
```

| 样本 | 真实类别 | 预测类别 | 正确 |
| --- | --- | --- | --- |
| validation-engineering-01 | engineering | engineering | True |
| validation-engineering-02 | engineering | engineering | True |
| validation-engineering-03 | engineering | engineering | True |
| validation-engineering-04 | engineering | engineering | True |
| validation-engineering-05 | engineering | engineering | True |
| validation-engineering-06 | engineering | engineering | True |
| validation-project-01 | project | project | True |
| validation-project-02 | project | project | True |
| validation-project-03 | project | project | True |
| validation-project-04 | project | project | True |
| validation-project-05 | project | project | True |
| validation-project-06 | project | project | True |
| validation-preference-01 | preference | preference | True |
| validation-preference-02 | preference | preference | True |
| validation-preference-03 | preference | preference | True |
| validation-preference-04 | preference | preference | True |
| validation-preference-05 | preference | preference | True |
| validation-preference-06 | preference | preference | True |
| validation-reminder-01 | reminder | reminder | True |
| validation-reminder-02 | reminder | reminder | True |
| validation-reminder-03 | reminder | preference | False |
| validation-reminder-04 | reminder | reminder | True |
| validation-reminder-05 | reminder | reminder | True |
| validation-reminder-06 | reminder | reminder | True |

## 记忆分类：冻结后的最终测试

训练样本：64；评估样本：24
混淆矩阵行是真实类别，列是预测类别。

```json
{
  "labels": [
    "engineering",
    "preference",
    "project",
    "reminder"
  ],
  "matrix": [
    [
      5,
      1,
      0,
      0
    ],
    [
      0,
      6,
      0,
      0
    ],
    [
      0,
      0,
      6,
      0
    ],
    [
      0,
      1,
      0,
      5
    ]
  ]
}
```

| 样本 | 真实类别 | 预测类别 | 正确 |
| --- | --- | --- | --- |
| test-engineering-01 | engineering | engineering | True |
| test-engineering-02 | engineering | engineering | True |
| test-engineering-03 | engineering | engineering | True |
| test-engineering-04 | engineering | engineering | True |
| test-engineering-05 | engineering | engineering | True |
| test-engineering-06 | engineering | preference | False |
| test-project-01 | project | project | True |
| test-project-02 | project | project | True |
| test-project-03 | project | project | True |
| test-project-04 | project | project | True |
| test-project-05 | project | project | True |
| test-project-06 | project | project | True |
| test-preference-01 | preference | preference | True |
| test-preference-02 | preference | preference | True |
| test-preference-03 | preference | preference | True |
| test-preference-04 | preference | preference | True |
| test-preference-05 | preference | preference | True |
| test-preference-06 | preference | preference | True |
| test-reminder-01 | reminder | reminder | True |
| test-reminder-02 | reminder | reminder | True |
| test-reminder-03 | reminder | reminder | True |
| test-reminder-04 | reminder | reminder | True |
| test-reminder-05 | reminder | reminder | True |
| test-reminder-06 | reminder | preference | False |

## 可复现性与边界

- 数据文件哈希、算法和指标插件版本、验证阈值均已记录。最终测试不参与修复和候选选择。
- 所有报告指标由可信验证器计算，mock 只替代 LLM 响应。
- Accuracy 与 Macro-F1 来自可信分类验证器；Macro-F1 对评估中真实或预测出现的类别等权平均。
- 分类 P95 包含整批重新训练与预测，以三次整批调用估计，不是单条纯推理延迟。
- 分类部署包包含冻结训练样本；对外分享前检查数据是否适合公开。
- 算法目录来自可信开发者模块或验证后登记清单；真实模型代码默认在禁网 Docker 容器运行，local 不构成可靠安全沙箱。

## 自动工作流选择

提交模式：auto；执行路径：reuse
已有能力可以作为实现依据，无需联网搜索。
模型说明：用户请求为个人记忆生成文本分类算法，属于 memory_classification 任务。
未登记的方法：
