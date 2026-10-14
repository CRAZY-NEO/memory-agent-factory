# 从真实 Python 仓库抽取能力候选

`ingest-repo` 读取一个本地代码仓库，通过 Python AST 抽取函数、方法、输入输出类型、注释和 import 依赖，保存到现有 SQLite / NetworkX 知识图谱。可以扫描本项目的真实源码，也可以扫描用户已经下载的其他 Python 仓库；它不会联网克隆仓库。

自动联网寻找新算法使用另一条 `discover` 工作流，详见 [GitHub 发现与登记](github-discovery.md)。它在搜索、固定提交下载后复用本抽取器，再生成独立适配实现，经 Docker 验证和最终测试合格后才登记；不能把 AST 抽取本身当成验证或注册。

这个步骤提供可追溯的算法实现证据。AST 能准确定位签名、代码和注释，但不能证明函数满足业务需求。仓库注释中的描述仍是未验证的声明，类型注解也不等于运行时保证。

## 快速演示

在项目根目录执行，使用现有配置和虚拟环境：

```bash
.venv/bin/python -m agent_factory ingest-repo . \
  --include agent_factory/templates/jaccard.py \
  --algorithm jaccard
```

命令扫描本仓库的真实 `jaccard.py`，抽取 `tokenize` 和 `retrieve` 两个函数。结果写入 `knowledge/extracted_repository.json`，节点写入 `state/knowledge.sqlite`。Git 已安装且目录属于 Git 仓库时，记录当前 HEAD；工作区中的实际内容始终以文件 SHA-256 为准，包括尚未提交的修改。

之后正常提交自然语言开发需求：

```bash
.venv/bin/python -m agent_factory run --provider mock --runner docker \
  --description '为中文技术记忆生成检索算法，比较 TF-IDF、BM25 和 Jaccard，返回原始记忆 ID 和相关性分数。'
```

`runs/<run_id>/retrieved_knowledge.json` 的 `repository_candidates` 中应出现 Jaccard 的 `retrieve` 函数。Planner 取得函数签名和来源，Coder / Reviewer / Repairer 的当前算法上下文取得有长度限制的代码片段。最终生成的算法仍需要通过统一验证器和独立最终测试。

Mock 的生成器使用规则模板，所以此命令证明“抽取、入库、检索、传入工作流”这条链路；不能据此声称模板根据仓库代码进行了智能改写。真实 Ollama 模式会将同样的候选证据交给模型，现有原生 Ollama 接入方式不变。

扫描其他已下载仓库的示例：

```bash
.venv/bin/python -m agent_factory ingest-repo /path/to/another-python-repo \
  --include 'src/retrieval/*.py' \
  --include 'src/tokenization.py' \
  --algorithm jaccard \
  --max-files 100 --max-file-bytes 262144 --max-total-bytes 5242880
```

`--include` 匹配仓库相对路径，支持重复传入。请只对确认相关的源码显式指定 `--algorithm`。这个参数表示操作者认为它可能有参考价值，不认证算法身份，不改变插件注册表。CLI 只接受工作区配置中已经启用的算法；未知算法、不同任务或额外依赖仍需扩展可信插件和验证环境。

如果不指定 `--algorithm`，任何合法 Python 函数都能被抽取保存；它们不会自动进入某个检索算法的生成上下文。抽取本身不要求 Ollama，也没有 provider 参数。`--workspace /path/to/workspace` 必须放在子命令前，工作区需包含项目的 `config.json`。

## 抽取内容与图谱

| 节点 | 主要字段 | 语义 |
| --- | --- | --- |
| `RepositorySnapshot` | `repository_name`、`repository_key`、`git_commit`、扫描规则、上限、文件数、问题记录 | 一次静态扫描的内容版本，不表示仓库通过测试 |
| `Source` | `source_type=python_repository`、相对路径、原始字节 SHA-256、编码、原文、模块文档、imports | 文件级可核查来源 |
| `CodeCandidate` | 限定函数名、方法/函数类型、async、行号、签名、参数类型/默认值、返回注解、decorators、docstring、comments、代码、代码哈希、imports、人工算法关联 | `status=unvalidated` 的候选实现证据 |

图关系为 `RepositorySnapshot --contains--> Source --declares--> CodeCandidate`。算法关联保存在候选节点的 `algorithm`、`algorithm_plugin_version` 和 `association_method=operator_explicit` 中，检索器按这一关联筛选。它不会创建虚假的 `Implementation` 或 `ValidationRun`，也不会将原始函数注册成可执行插件。

函数片段的 `line_start` 包括装饰器；`definition_line` 指向 `def`；`line_end` 包含函数最后一行。函数前紧邻的整行注释和函数体内注释单独记录。类方法和嵌套函数分别使用 `Retriever.search`、`retrieve.<locals>.score` 这样的限定名。

`imports` 保存模块名、导入成员、别名、相对导入层数和行号，是**整个来源文件的语法依赖列表**。其中可能包含可选依赖、条件导入和局部导入；不会猜测 pip 包名、版本要求，也不声称每一个 import 都会执行或被当前函数使用。动态 import、继承后的方法、运行时生成的函数、跨文件数据流和业务适用性推断尚未实现。

类型缺失时保留 `null`，不凭函数名编造参数含义、返回值或适用条件。具体业务条件和性能保证仍来自已有材料抽取、规划和可信执行结果。

## 检索与信任边界

已有关联的候选按算法分组，每个已选算法最多返回一个函数，优先公开 `retrieve` 入口，再按函数名、签名和注释与需求的相关度排序。图中保留所有历史内容版本，排序不保证自动选择最后修改的版本；提示中的文件哈希和来源供检查。

完整文件、函数和元数据保存在抽取 JSON 与图谱中。交给模型的 `repository_candidates` 使用以下上限，以避免挤占小模型上下文：代码 3000 字符、签名 500 字符、描述 500 字符、注释最多 4 条且各 200 字符、import 最多 8 条且字符串字段各 120 字符。代码发生截断会设置 `code_truncated=true`，`code_sha256` 始终对应**完整函数片段**，不是截断字符串。代码片段可能依赖同文件的其他函数、全局变量或类状态，不能当作独立可运行程序。

所有仓库内容都带有 `trust=untrusted_repository_evidence`。其中的注释、字符串和文档不是对 Agent 的新指令。候选不能覆盖任务要求、接口契约、安全规则或验证阈值；生成后的代码仍走 AST 安全检查和 Docker 验证。

## 资源限制与错误

默认最多检查 200 个匹配的 Python 文件、每文件 256 KiB、累计读取 5 MiB、抽取 1000 个函数/方法。超出总量时停止，输出 `limited=true` 和对应原因；单个大文件、解码错误、语法错误、递归解析错误会记录并跳过，不中断其余文件。

隐藏目录、依赖和产物目录（如 `.git`、`.venv`、`venv`、`node_modules`、`site-packages`、`vendor`、`build`、`dist`、`runs`、`state`）默认跳过；不会跟随文件或目录符号链接，也不接受符号链接作为扫描根目录。问题记录最多保存 200 条。当前上限约束源码读取和抽取数量，不设置整个目录遍历的总耗时或目录数量上限；请优先用明确的源码子目录或 include 缩小输入范围。它不是针对持续恶意修改文件系统的操作系统沙箱。

解析期间不导入或执行目标源码，不执行装饰器、仓库测试、安装脚本或依赖安装。可选 Git 操作只有带超时的 `git rev-parse --verify HEAD`，不触发 checkout、hook 或网络操作。

## 幂等、版本与复现

相同仓库路径、源码、选项、Git HEAD 和抽取器版本重复执行，节点 ID 不变；内容变化保留旧版本并产生新节点。HEAD 只是来源上下文：未提交的文件修改会生成不同文件哈希，即使 HEAD 相同。JSON 输出文件可被下一次扫描覆盖，SQLite 中已有不可变节点不会被覆盖；需要保留独立输出时使用 `--output`。

自动测试验证带副作用的顶层代码没有执行、装饰器没有求值、异步方法/嵌套函数/参数类型正确抽取、非 UTF-8 源文件可读、版本幂等、符号链接与目录跳过、解析失败与资源上限、人工关联与带来源检索，以及 CLI 的参数检查：

```bash
bash scripts/run_tests.sh -q tests/test_repository.py
```
