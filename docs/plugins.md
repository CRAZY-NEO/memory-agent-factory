# 算法与指标插件接入

注册机制覆盖“发现 → 材料抽取 → 规划 → 生成/修复 → 验证 → 候选比较 → 报告 → 知识回写”。
支持 `memory_retrieval` 的 `retrieve(query, memories, top_k=5)`，以及 `memory_classification` 的 `classify(texts, training_examples)`。每个插件声明且只能用于对应任务。新增算法或指标不需要改工作流、算法枚举、验证器或报告列。

本指南介绍开发者编写可信算法/指标模块的方式。也可使用新增的 [GitHub 自动发现与登记](github-discovery.md)：模型依据下载源码生成新检索实现，经验证及最终测试通过后登记为数据清单，无须手工生成新的注册 Python 文件。两种注册来源在同一目录中展示，带来源与版本信息。

## 1. 目录与注册契约

```text
agent_factory/registry.py                  # 注册契约、发现、配置预检
agent_factory/plugins/algorithms/*.py      # 算法注册模块
agent_factory/plugins/algorithms/materials # 随 Python 包分发的能力材料
agent_factory/plugins/metrics/*.py         # 可信指标实现及注册
agent_factory/templates/*.py               # 可检查的算法参考实现
```

系统遍历两个内置包，以及配置中显式指定的 `plugin_packages`。每个包的直接子模块须提供 `register(registry)`；按模块名排序加载，跳过 `_` 开头的辅助模块和子包。重复发现同一包不重复注册。
注册失败会回滚该包的注册项并停止启动。未知 ID、重复 ID、缺少材料或参考文件、无效阈值、任务不匹配和未安装依赖在运行前报错。

| 契约 | 必要信息 |
| --- | --- |
| AlgorithmSpec | id、name、version、task、material、reference、guidance；可选 dependencies、environment、public_cases |
| MetricSpec | id、name、version、task、direction、compute；可选 minimum、maximum |
| MetricContext | predictions、queries（含可信相关 ID）、top_k、durations_ms |

ID 使用小写字母开头及小写字母/数字/下划线。算法支持 `retrieval-v1` 与 `classification-v1` 环境与标准库/scikit-learn 依赖；增加其他库需要维护者同步扩展允许的环境和执行镜像，不能只声明依赖。新任务类型也需要实现相应数据契约及验证器。

注册模块是开发者审查过的 Python 代码，在可信工厂进程执行。系统不扫描用户上传目录或生成代码目录，不将 LLM 输出当注册模块加载。指标在可信父进程评分；算法生成文件继续经过 AST 检查与受限执行。

## 2. 已注册插件

| 类型 | ID | 版本 | 含义 |
| --- | --- | --- | --- |
| 算法 | tfidf | 1.0.1 | 字符 TF-IDF 与余弦相似度，含公开评分契约 |
| 算法 | bm25 | 1.0.0 | 中文单字/双字及英文词 BM25 |
| 算法 | jaccard | 1.0.1 | 中文单字/双字及英文词的集合相似度，含中英文契约 |
| 分类算法 | tfidf_logreg | 1.0.0 | 字符 TF-IDF + Logistic Regression |
| 分类算法 | tfidf_nb | 1.0.0 | 字符 TF-IDF + MultinomialNB |
| 分类指标 | accuracy / macro_f1 | 1.0.0 | 准确率 / 宏平均 F1，越高越好 |
| 分类指标 | classification_p95_ms | 1.0.0 | 整批训练+预测 P95，越低越好 |
| 指标 | recall_at_k | 1.0.0 | 平均 Recall@k，越高越好 |
| 指标 | mrr_at_k | 1.0.0 | 平均首个相关结果的倒数排名，越高越好 |
| 指标 | precision_at_k | 1.0.0 | 平均 Precision@k，越高越好 |
| 指标 | p95_ms | 1.0.0 | 查询耗时 P95，越低越好 |

```bash
.venv/bin/python -m agent_factory plugins
.venv/bin/python -m agent_factory --config examples/plugins/jaccard-only.json plugins
```

Jaccard：分词去重得到查询集合 Q 和文档集合 D，评分 `|Q∩D| / |Q∪D|`。
只返回正分结果，分数降序，同分按 ID 排序。空输入、无词项和非正 top_k 返回空列表。
它不学习词频或 IDF，适合轻量词面检索；长文档会被较大并集稀释，不能替代语义检索。

Precision@k：每个查询的 `前 k 个结果中相关 ID 数 / k`，再对所有查询取平均。返回不足 k 个时分母仍为 k；无答案查询的命中数为零。可信接口检查先排除重复及非法 ID。
例如 k=5、仅返回 1 个正确结果，Precision@5=0.2、Recall@5=1。样例大多数查询只有一个相关文档，因此阈值 0.2 与这种标注结构一致；高 Recall 和较低 Precision 不矛盾。评价真实数据时须重新确定阈值。

## 3. 配置与候选选择

项目 `config.json` 和 `examples/plugins/config.json` 已启用三个算法及四个指标：

```json
{
  "task": "memory_retrieval",
  "algorithms": ["tfidf", "bm25", "jaccard"],
  "plugin_packages": [],
  "max_candidates": 3,
  "compare_all_algorithms": true,
  "metrics": {
    "recall_at_k": {"min": 0.75},
    "mrr_at_k": {"min": 0.65},
    "precision_at_k": {"min": 0.2},
    "p95_ms": {"max": 1000}
  },
  "selection_order": ["recall_at_k", "mrr_at_k", "precision_at_k"],
  "timeout_seconds": 30,
  "max_repairs": 2
}
```

`algorithms` 是启用清单，`max_candidates` 是规划上限（1～8）。默认 `compare_all_algorithms=false` 允许模型规划候选子集；项目及三算法演示配置启用 true，要求每个启用算法恰好出现一次，预算不足在启动前报错。规划 schema 设置相应最小数量，可信检查再拒绝漏项、重复项或非法能力引用；模型解释中声称有三种算法不能代替实际三个候选。运行会为知识库补抽取新启用算法的材料，并按算法去重检索能力，避免旧能力版本占满候选位置。
规划 JSON schema 根据注册 ID 动态生成。代码提示包含相应插件的指导、材料和参考实现；mock 从插件参考文件生成，Ollama 使用实际模型返回代码，不以模板替换无效响应。

`metrics` 决定计算和报告哪些指标；`min`/`max` 是可选门槛，`{}` 表示计算但不设质量阈值。
`selection_order` 中的指标必须启用，按顺序比较，通过 `direction` 判断高低；完全同分时依次优先依赖更少、配置顺序更前、修订次数更少的候选。计时默认只作为门槛，避免微小波动决定算法选择。
接口、功能、稳定性与代码执行检查始终启用，不能通过清空指标禁用。
旧配置 `min_recall/min_mrr/max_p95_ms` 可自动迁移，但不能和新 `metrics` 字段混用。

## 4. 新增算法

可参考 `agent_factory/plugins/algorithms/jaccard.py`。为新算法准备：

1. 能力材料：说明算法、适用条件、输入输出、指标、依赖和限制。mock 需要明确 `算法：my_algorithm`、`适用：` 和 `局限：` 行。
2. 参考代码：统一 retrieve 接口，确定性排序、空输入语义，不含 IO、网络、动态导入等受限操作。
3. 注册模块：在自动发现目录中添加一个 Python 模块。

```python
from pathlib import Path
from agent_factory.registry import AlgorithmSpec


def register(registry):
    base = Path(__file__).parent
    registry.add_algorithm(AlgorithmSpec(
        id="my_algorithm", name="My algorithm", version="1.0.0",
        task="memory_retrieval", material=base / "materials/my_algorithm.md",
        reference=base / "_reference.py",  # 同目录辅助模块以 _ 开头，避免被自动发现
        guidance="在这里明确评分公式、分词、空输入及排序行为。",
        dependencies=(), environment="retrieval-v1",
    ))
```

随后在配置 `algorithms` 中加入 ID，并调整候选预算。工作流和验证器无需修改。
若复用已经抽取过的 workspace、更新了插件材料或版本，应再次执行 `ingest`；能力版本哈希包含插件版本，保留旧记录。本地材料 `knowledge/sources/<id>.md` 可覆盖对应的打包材料。

## 5. 新增指标

以下例子加入“返回结果槽位比例”，用于观察不足 k 个结果的情况；它不是相关性指标，不能替代 Precision 或 Recall。

```python
from agent_factory.registry import MetricSpec


def returned_fraction(context):
    if not context.queries:
        return 0.0
    count = sum(len(context.predictions[q["id"]][:context.top_k])
                for q in context.queries)
    return count / (len(context.queries) * context.top_k)


def register(registry):
    registry.add_metric(MetricSpec(
        id="returned_fraction", name="Returned fraction", version="1.0.0",
        task="memory_retrieval", direction="higher", compute=returned_fraction,
        minimum=0, maximum=1,
    ))
```

添加到指标目录或外部包，在配置 `metrics` 增加 `"returned_fraction": {}` 即可自动计算并出现在报告。需要参与排序时加入 `selection_order`。
指标异常、NaN、Infinity、布尔值或越界值会让本次验证失败，不能发布插件。不要在指标中使用随机、网络或修改输入数据。

## 6. 外部包接入

外部包必须先由维护者安装进工厂环境，可是内部 Python 包，不要求发布 PyPI。
示例目录为 `my_factory_plugins/__init__.py`、`algorithm.py`、`metric.py`、`_reference.py` 与材料文件。子模块的 register 契约同上。

```json
{
  "plugin_packages": ["my_factory_plugins"],
  "algorithms": ["my_algorithm"],
  "max_candidates": 1,
  "metrics": {"precision_at_k": {"min": 0.2}, "returned_fraction": {}},
  "selection_order": ["precision_at_k"]
}
```

将这些字段写入自己的完整配置，再用全局 `--config /path/to/config.json` 运行。打包外部插件时须在其 package-data 中包含材料和参考文件。
`tests/test_registry.py` 的外部包测试实际完成发现、材料抽取、生成、评估、报告与知识回写，证明核心模块不需要修改。

## 7. 完整演示与回归验证

```bash
cd ~/Downloads/agent-factory
# Docker 镜像准备一次即可
docker build -f Dockerfile.runner -t agent-factory-runner:0.1 .
# 三算法比较；mock 首版 BM25 返回空结果，触发诊断与修复
bash scripts/run_plugin_demo.sh --inject-fault
# 只启用 Jaccard，产出独立检索插件
FACTORY_DEMO_WORKSPACE=state/jaccard-demo \
FACTORY_DEMO_CONFIG=examples/plugins/jaccard-only.json \
FACTORY_DEMO_REQUEST=examples/plugins/jaccard-request.txt bash scripts/run_plugin_demo.sh
# 保持用户建立的 SSH 隧道，调用 Jetson 真模型
FACTORY_PROVIDER=ollama FACTORY_DEMO_WORKSPACE=state/plugin-ollama-demo \
OLLAMA_URL=http://127.0.0.1:11435 OLLAMA_CHAT_MODEL=qwen3.5:2b \
bash scripts/run_plugin_demo.sh

bash scripts/run_tests.sh -q
```

演示脚本默认 Docker，每种演示使用独立 workspace，保留所有运行历史。
离线实际执行样例见 `examples/plugin-demo/`；三算法真实模型比较见 `examples/plugin-ollama-demo/`，BM25 通过并被发布，另外两算法的失败修订均保留；通过的真实模型 Jaccard 样例见 `examples/plugin-jaccard-ollama-demo/`。另有 `examples/plugin-ollama-failure/` 保留改进前一次失败：模型漏掉两个候选，三次 Jaccard 修订均未通过跨进程稳定性，系统没有发布插件。
报告保留算法/指标版本、配置、所有修订、实际测量、模型调用和失败信息。知识回写保留 `plugin_catalog` 与指标元数据；发布的 `plugin.json` 保存选定算法的版本和依赖。

生成代码的质量依赖模型响应。候选集合约束加强后仍可能遇到实现或验证失败，应保留实际报告；系统不会用模板替换失败的真实响应。

算法还可声明 public_cases（query/memories/top_k/expected），作为公开公式契约，自动进入生成提示与可信验证。新增评分逻辑宜加入手算样例。改进的修复流程及历史重放见 generation-repair.md；独立 CLI validate 可使用 --algorithm jaccard 启用对应公式检查。

分类配置见 [config/classification.json](../config/classification.json)。分类 MetricContext 的 predictions 是 ID→标签，queries 为可信标注样本、top_k=0；不要使用检索指标解析分类输出。分类 public_cases 暂未接入，当前用统一分类接口/质量/稳定性契约验收；检索 public_cases 保持原行为。
