# 历史经验开／关对照实验

核对日期：2026-10-16。新增 `cloud_repeated_pairs_v1` 协议支持 DeepSeek；Ollama 原有固定 seed / 本地模型 digest 协议继续保留。

## 本次结果

事先固定 3 类记忆分类需求：过滤禁用训练样本、按精确 `(text, label)` 去重、过滤后去重。每类做 3 组开／关配对，共 18 次实际运行。算法均为字符 TF-IDF + Logistic Regression，模型 `deepseek-v4-pro`，关闭思考，不提供完整模板，最多两轮修复。

| 指标 | 关闭历史 | 开启历史 |
| --- | ---: | ---: |
| 首版验证通过 | 6/9（66.7%） | 9/9（100%） |
| 修复预算内最终通过 | 9/9（100%） | 9/9（100%） |
| 代码修复次数 | 3 | 0 |
| 真实模型调用次数 | 33 | 27 |
| 输入 token | 36,450 | 29,562 |
| 输出 token | 8,480 | 6,192 |
| 接口或网络异常 | 0 | 0 |

配对结果：首版通过为开启组胜 3、负 0、平 6；最终通过为 9 组全部持平。“首版”指 r0 通过验证，可包含预算内的响应格式重试；本次没有响应错误或重试。通过率以一次生成运行为单位，不是分类器逐样本准确率。

| 需求 | 关闭历史首版 | 开启历史首版 | 两组最终 |
| --- | ---: | ---: | ---: |
| 过滤 | 3/3 | 3/3 | 各 3/3 |
| 去重 | 2/3 | 3/3 | 各 3/3 |
| 过滤＋去重 | 1/3 | 3/3 | 各 3/3 |

本次样本中，开启历史的首版通过率较高、修复次数较少，最终通过率没有提高。不能把这一结果扩大为“历史经验普遍提高生成成功率”，也不能声称统计显著。

## 历史来源与错误解释

经验来源为此前独立的真实 DeepSeek 运行 `20261016T030536_8cce6d5f`。其首版单类别分支调用沙箱未开放的 `next`，经模型修复为允许的列表访问后通过验证。冻结时使用 `observed-diff-v3` 从可信异常、前后源码与真实 HTTP 记录重新提炼，原始来源记录不改写。

3 次关闭组首版失败均重犯 `NameError: name 'next' is not defined`，各经一轮修复通过；开启组没有出现该错误。组合任务中的过滤／去重检查失败也由同一异常引起，不能称作这次才学会过滤或去重逻辑。该经验与运行环境兼容有关，不代表丰富的业务知识。

历史输入是经核验的文字规则、标识和验证摘要，没有完整历史源码、验证／最终测试全文或答案。开启历史影响 Planner 的历史指标和 Coder/Repairer 的经验，故这是端到端历史策略对照，后续规划、代码和修复反馈可能自然不同。

## 控制与审计

- 先固定任务、9 组配对、18 次运行、参数、顺序和指标；不依结果换任务或挑选重跑。
- 两臂从同一冻结图谱分别复制独立工作目录、数据库。新经验只写入各自工作目录，不会污染下一组。
- 按全局配对编号交替 off/on、on/off；两组都保留当前运行的错误反馈和两轮修复预算。
- 检查实际 HTTP 消息中的经验正文、证据字段与冻结内容完全相同；关闭组不含非空历史。不只检查 lesson ID。
- 保留全部错误、模型调用、生成修订和计划分母。永久 API 拒绝、setup 失败或完整性错误会停止后续调用并标记不完整。
- 运行前后源码、数据、历史哈希和 Docker runner 镜像 ID 一致。619 项软件回归通过，不能用这个数量代替模型验收结果。
- `valid_comparison=true` 只表示计划和审计完成；它不表示云端权重固定、统计显著或因果关系已证实。

DeepSeek 不应用采样 seed。重复编号只用于配对；配置中的 `model_seed=42` 是两组相同的上下文占位值，没有作为 API 采样 seed 参数。服务返回模型名、响应 ID 和指纹均留档，不能据此独立证明权重版本固定。

这些数据已用于先前调试，是既有任务上的重复生成工程对照。3 个需求共享同一算法和评估集，18 次运行不是 18 种独立任务；不能把重复使用的测试样本累加为全新数据量。更困难任务、更多独立经验和跨数据泛化仍需新实验。此前小模型两组均为 0/6 的结果仍保留。

## 如何复验

先在本机 `.env` 配置 DeepSeek，准备 Docker runner。实验工具自动读取项目 `.env`，不会复制密钥到实验目录。

收集一个独立的真实失败→修复通过工作目录，再冻结历史：

```bash
.venv/bin/python scripts/run_ablation.py freeze \
  --workspace state/你的真实修复工作目录 \
  --output state/frozen-history
```

`--workspace` 是包含 `state/knowledge.sqlite` 和 `runs/` 的工作目录，不是单个 run 目录。需要经过真实模型修复且通过验证的来源；若 warmup 首版就通过，则没有可冻结的修复历史，不应注入假错误充数。冻结会核验模型原文、源码哈希、验证记录和图关系。实验期间保留这些来源文件。

```bash
DEEPSEEK_THINKING=disabled .venv/bin/python scripts/run_ablation.py run \
  --provider deepseek --model deepseek-v4-pro --repeats 3 \
  --profiles examples/history-ablation/profiles.json \
  --snapshot state/frozen-history \
  --output state/deepseek-history-ablation \
  --runner docker --max-repairs 2
```

输出路径必须尚不存在。DeepSeek 使用 `--repeats`，不接受 `--seeds`；本地 Ollama 仍使用 `--seeds`。每轮保留 `protocol.json`、`raw-results.json`、`aggregate.json`、单元 `result.json`、`wire-audit.json` 及完整工作流产物。API 存在采样变化，复跑不会保证得到同样比例。

[便携汇总](../examples/history-ablation/summary.json)保留统计和协议哈希，[任务配置](../examples/history-ablation/profiles.json)可直接复用。完整原始记录、逐次代码和模板对比按项目约定保存在仓库外；该汇总不替代原始证据。
