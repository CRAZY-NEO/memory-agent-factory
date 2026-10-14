# 文本记忆分类、跨任务复用与部署包

同一套算法工厂现支持两种任务：根据查询查找相关记忆（`memory_retrieval`），以及根据已标注的训练样本给新记忆分类（`memory_classification`）。工厂产出 Python 算法、验证报告和复用清单；生成后的算法可以单独运行，不需要再次调用大模型。

## 跨场景迁移具体实现了什么

这次迁移从“记忆检索”扩展到“记忆自动归档”。两个任务复用自然语言理解、能力知识库、方案规划、候选比较、代码生成、有限修复、冻结最终测试、知识回写及 Web/CLI/API 入口。任务不同的部分由 [TaskSpec](../agent_factory/tasks.py) 和对应验证器提供。

| 项目 | 记忆检索 | 文本记忆分类 |
| --- | --- | --- |
| 输入 | 查询、候选记忆、top_k | 待分类文本、已标注训练样本 |
| 产物接口 | `retrieve(query, memories, top_k=5)` | `classify(texts, training_examples)` |
| 输出 | 相关记忆 ID 和分数 | 按输入顺序排列的类别标签 |
| 现有候选 | TF-IDF、BM25、Jaccard，及已登记的新检索方法 | 字符 TF-IDF + 逻辑回归、字符 TF-IDF + 多项式朴素贝叶斯 |
| 可信指标 | Recall@k、MRR@k、Precision@k、查询 P95 | Accuracy、Macro-F1、整批调用 P95 |
| 数据隔离 | 验证查询、最终测试查询分离 | 训练、验证、最终测试三份分离 |
| 任务配置 | `config.json` | `config/classification.json` |

这是同一平台对不同业务功能、数据契约和评价方式的复用。目前支持的任务就是上面两种；图像分类、表格预测、任意功能代码生成还需要各自的任务适配器和可信验证器。GitHub 新算法自动发现、复刻与登记仍仅支持检索任务，分类先使用已经登记的两个候选。

## 分类数据与接口

默认四类为：

| 标签 | 含义 | 例子 |
| --- | --- | --- |
| `engineering` | 技术知识 | 记录某种错误的原因、排查方法或技术原理 |
| `project` | 项目状态与事实 | 记录项目进度、已有决定或当前配置 |
| `preference` | 个人偏好 | 记录用户喜欢的工具、沟通方式或工作习惯 |
| `reminder` | 待办提醒 | 记录后续需要完成的事项 |

标签来自训练样本，没有硬编码在生成算法的输出逻辑中。换成其他有标注的文本类别时，仍应准备三份互不重叠的数据并重新生成、验证；当前数据用于演示，不代表生产中的真实个人记忆分布。

[分类数据说明](../data/classification/README.md)及数据位于：

- [train.json](../data/classification/train.json)：64 条，参与分类器训练。
- [validation.json](../data/classification/validation.json)：24 条，用于候选比较、问题诊断和修复。
- [test.json](../data/classification/test.json)：24 条，仅在选定并冻结代码后评估。

每类分别有 16/6/6 条记录，各份数据中英文各半；验证和测试各含 8 条直接表达、8 条同义改写、8 条相近类别负例。这里的“相近负例”是容易被分到另一类别的记忆，仍然有一个正确分类标签，不是检索任务的负文档 ID。

源数据每条包含 `id`、`text`、`label`，还可包含 `category`、`annotation`。运行前检查三份数据的 ID 和规范化文本是否重复；规范化包括 Unicode NFKC、忽略大小写及空白差异。所有验证/测试标签必须在训练集中出现。

真正传给算法的接口为：

```python
def classify(texts, training_examples):
    # texts: ["待分类文本", ...]
    # training_examples: [{"text": "训练文本", "label": "类别"}, ...]
    # return: ["类别", ...]
    ...
```

输出必须与输入等长、保持输入顺序，每个标签必须来自训练集，且不能修改任何输入。验证还包括空列表、空白文本、Unicode、重复文本、批次顺序、单类别训练、空词表和缺失训练数据：

- 空文本列表返回 `[]`。
- 待分类文本非空而训练集为空时，抛出 `ValueError`。
- 空白文本或空词表使用训练多数类；票数相同时按标签字典序选择。
- 单类别训练返回该类别。

每次工厂运行先把 `{text, label}` 训练记录冻结到 `runs/<run_id>/training_examples.json` 并记录哈希。同一运行的所有候选、修复版本和最终测试使用同一训练快照；数据在运行中发生变化会终止评估。生成代码只接触训练标签和需要预测的文本，验证/最终测试的正确标签由可信父进程用于评分。

## 配置与验证含义

默认 [分类配置](../config/classification.json) 使用两个候选：`tfidf_logreg` 与 `tfidf_nb`。两者均只在训练文本上拟合字符 1–3 元组 TF-IDF，再训练分类器；待分类文本只做 `transform` 和预测，不参与特征拟合。

| 配置项 | 默认值 |
| --- | --- |
| Accuracy | 至少 0.75 |
| Macro-F1 | 至少 0.70 |
| `classification_p95_ms` | 至多 2000 ms |
| 候选优先比较顺序 | Macro-F1，再 Accuracy |
| 每个候选最大修复次数 | 2 |
| 单次执行器超时 | 30 秒 |

这些是验收门槛，不是已取得的成绩；实际成绩以对应运行报告为准。Macro-F1 对每个评估中出现的类别等权计算，避免仅依靠总体正确率掩盖小类别问题。

分类 P95 来自同一进程内三次**整批** `classify` 调用，包含特征拟合、分类器训练和预测，排除解释器与依赖导入。它不是单条文本延迟，也不是在线服务的并发吞吐量。当前接口每次调用都会重新训练，适合可复现的原型；持久化已训练模型和批量推理服务还没有实现。

检查接口、稳定性和指标的代码位于 [classification.py](../agent_factory/classification.py)。模型生成和修复无法修改这些检查或门槛。报告包含候选修订、Accuracy、Macro-F1、整批 P95、混淆矩阵、逐样本错误及类型分组结果。最终测试失败后，本次运行不会重新选择候选或继续修复，也不会发布成功插件。

## 跑一次分类工厂

在项目根目录运行。首次环境安装、Docker 镜像准备见 [README](../README.md)；真实模型仍使用已经可达的 Ollama 地址。

```bash
cd ~/Downloads/agent-factory

# 离线演示：mock 替代模型响应，实际运行算法和可信验证器
.venv/bin/python -m agent_factory run \
  --provider mock --runner local \
  --description '为个人记忆生成文本分类算法，根据训练样本预测类别并比较两个候选'
```

mock 使用参考实现来演示流程，不能作为真实模型生成证据。使用 Jetson 上的真实模型：

```bash
OLLAMA_URL=http://127.0.0.1:11435 \
OLLAMA_CHAT_MODEL=qwen3.5:2b \
.venv/bin/python -m agent_factory run \
  --provider ollama --runner docker \
  --description '为个人记忆生成文本分类算法，根据训练样本预测类别并比较两个候选'
```

默认自动路由识别分类需求并加载分类配置。描述业务需求即可；当前注册名 `tfidf` 指检索算法，若要明确指定分类方法，使用 `tfidf_logreg` 或 `tfidf_nb`，不要仅写容易产生歧义的“比较 TF-IDF”。

输出包含 `run_id` 和报告路径。成功分类运行在 `runs/<run_id>/` 中保存：

- `selected_classification.py`：最终选定、冻结的算法。
- `training_examples.json`：与该算法一起验证的训练快照。
- `plugin.json`：接口、代码哈希、训练哈希和算法身份。
- `report.json`、`report.md`：完整验证报告。
- 各算法的 `r0`、`r1` 等子目录：候选代码与验证记录。

独立验证已有分类代码：

```bash
.venv/bin/python -m agent_factory validate \
  --task memory_classification \
  --code agent_factory/templates/tfidf_nb.py \
  --algorithm tfidf_nb --split validation --runner docker
```

这条命令只调用验证器，不完成能力抽取、生成、修复和知识回写。独立选择 `--split test` 会直接使用最终测试数据，不应拿它反复调参后再宣称是未见测试。

## Web、API 与一键 ZIP 导出

已有网页进程需要重启以加载新的后台代码。启动方式：

```bash
.venv/bin/python scripts/serve_web.py --provider mock --runner docker
# 或在已有 Ollama 连接可用时
.venv/bin/python scripts/serve_web.py --provider ollama --runner docker
```

点击“填入文本分类示例”，提交后查看分类指标、候选修订、混淆矩阵和失败样本。通过最终测试的结果会显示“一键导出可运行 ZIP”。检索运行同样支持导出。

CLI 等价操作如下；将运行 ID 替换为本次成功运行的 ID：

```bash
.venv/bin/python -m agent_factory export \
  --run-id YOUR_RUN_ID \
  --output state/exports/memory-classifier.zip
```

不传 `--output` 时，输出到 `runs/<run_id>/deployment.zip`。原有不带 `--run-id` 的 `export` 仍用于导出知识图谱。

API 继续通过原有异步队列提交任务：

```bash
curl -fsS http://127.0.0.1:8010/v1/factory/runs \
  -H "Authorization: Bearer $FACTORY_API_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"description":"为个人记忆生成文本分类算法，根据训练样本预测类别并比较两个候选","mode":"auto"}'
```

用返回的 `job_id` 查询 `GET /v1/jobs/{job_id}`。任务成功后以返回的运行 ID 调用：

```bash
curl -fsS -X POST \
  http://127.0.0.1:8010/v1/runs/YOUR_RUN_ID/export \
  -H "Authorization: Bearer $FACTORY_API_TOKEN" \
  -o memory-classifier.zip
```

`FACTORY_API_TOKEN` 应为当前服务的有效密钥；网页自动登录用户可以直接点击导出按钮，无需手工使用这些 API 命令。导出接口返回 ZIP 文件，不执行生成算法；缺少通过记录或产物损坏时返回 409。

## 在新目录运行部署包

部署包包含 `algorithm.py`、独立 `run.py`、相对路径 `plugin.json`、脱敏的 `report.json`、`requirements.txt`、`example_input.json`、`Dockerfile`、`README.md` 及文件校验和。分类包另有训练快照，来源检索算法另有 `source.json` 和原始许可证文本。

```bash
unzip state/exports/memory-classifier.zip -d /tmp/memory-classifier-demo
cd /tmp/memory-classifier-demo
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

使用 Python 3.11+，推荐 3.12。算法只需要自己的运行依赖：分类和 TF-IDF 检索需要固定版本的 scikit-learn 及其传递依赖，标准库检索方法不需要第三方包。无需安装工厂，不需要 Ollama、Jetson 或模型访问密钥。首次安装依赖需要网络；跨 CPU 架构的可用性取决于依赖是否提供相应 wheel。

自定义分类输入保存在 `input.json`：

```json
{"texts":["我更喜欢先看简短结论，再展开技术细节","下周需要整理项目发布检查表"]}
```

```bash
.venv/bin/python run.py --input input.json
```

结果是一个与文本等长的标签列表。检索包则使用包含 `query`、`memories`、`top_k` 的 JSON 输入，输出 ID/分数列表。`--input -` 支持从标准输入读取 JSON。Docker 构建与禁网、非 root、资源限制运行命令包含在包内 README 中。

导出前同时验证报告通过状态、选定代码哈希、插件身份；分类还验证训练文件与报告/插件中记录的哈希一致。来源算法必须存在通过验证的登记记录及哈希匹配的原始许可文件。部署入口再次校验代码与训练哈希，并检查输入输出契约。哈希用于检测意外修改，不提供数字签名或防止拥有目录写权限的人重写整套证据。

分类 ZIP **会包含训练文本和标签**，分享时会同时分享这些内容。不会打包验证/最终测试样本、模型提示词、trace、访问密钥或整个工作区；检索包的示例是合成数据，不自动打包个人记忆库。ZIP 内报告是指标/门槛摘要，完整逐样本证据仍保留在原运行目录。

## 在个人记忆系统中复用

如果个人记忆项目与工厂运行在同一 Python 环境，可以直接使用运行时适配器：

```python
from agent_factory.runtime import MemoryClassifier

classifier = MemoryClassifier(
    "runs/YOUR_RUN_ID/plugin.json",
    runner="docker",
)
labels = classifier.classify([
    "项目已经完成登录接口联调",
    "我喜欢使用深色主题",
])
```

`MemoryClassifier` 从清单所在运行目录读取固定的代码与训练快照，验证哈希，并把新文本交给受限执行器；返回值仍由可信分类检查器检查。每个适配器实例使用构造时读取的训练快照，文件后来被修改不会改变该实例已加载的训练内容；新建实例时会重新校验磁盘文件。

适配器不会从清单指定的任意外部路径读取训练数据，也不提供在预测请求中替换训练集的入口。`runner="docker"` 需要已经构建的 `agent-factory-runner:0.1`；`runner="local"` 可用于可信代码的本地演示，但不提供 OS 沙箱。

当前 Web/API 入口用于创建和验证算法，已有 `/v1/memory/search` 仍是检索调用。分类产物的实际调用可通过 `MemoryClassifier` 或导出包 `run.py` 完成；没有新增在线分类推理 HTTP 接口，也没有实现任意任务或工作流中断恢复。


## 已运行的验收证据

- [真实分类报告](../examples/classification-demo/report.md)：qwen3.5:2b + Docker，两种候选首版通过，选定 tfidf_logreg；24 条最终测试 Accuracy=0.9167、Macro-F1=0.9188。
- [真实检索报告](../examples/transfer-retrieval-demo/report.md)：同一工作空间先完成 BM25 检索，再生成分类能力，两类能力保存在同一知识库。
- [双任务与独立 ZIP 记录](../examples/transfer_validation.json)：包括真实模型、mock 故障修复、独立禁网容器运行及新中文记忆预测。
- [Chrome 验收](../examples/classification_web_validation.json)：实际提交 mock + Docker 工作流、查看指标/混淆矩阵、下载 ZIP 并在工厂目录外运行。
- [分类部署包](../examples/classification-demo/deployment.zip)：已有完整冻结示例，可直接解压试用。
- [生成改进审计](../examples/classification-generation-audits.json)：保留改进前失败，成功记录不覆盖失败历史。

同一组固定模拟数据被用于本次开发验收，报告只说明该原型数据上的结果。增加新的生产算法或根据这些公开报告反复优化时，应准备新的盲测集。
