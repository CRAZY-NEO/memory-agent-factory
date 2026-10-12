"""Reproduce authored synthetic notes and independently labelled query splits."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = [
    ("m01", "MySQL 连接池耗尽排查", "数据库请求超时，排查发现 MySQL 连接没有归还。使用上下文管理器关闭连接，监控连接池活跃数与等待队列。"),
    ("m02", "慢 SQL 与索引", "MySQL 查询缓慢时使用 EXPLAIN 检查执行计划，为过滤字段建立联合索引，避免 SELECT 星号和深分页。"),
    ("m03", "Redis 缓存穿透", "Redis 缓存穿透是不存在的数据不断访问数据库。可缓存空值或增加布隆过滤器，并给空值设置短 TTL。"),
    ("m04", "缓存雪崩", "大量 Redis 缓存同时过期造成雪崩。为 TTL 加随机抖动，热点数据预热，数据库访问增加限流保护。"),
    ("m05", "Docker 内存不足", "Docker 容器退出码 137 通常需要检查 OOMKilled。限制 worker 并发，设置内存上限，观察容器内存峰值。"),
    ("m06", "Docker 网络解析", "容器无法访问服务时检查 Docker 网络、DNS 解析和端口映射。localhost 在容器内指向容器自身。"),
    ("m07", "Git 冲突处理", "Git rebase 出现合并冲突时逐文件解决标记，运行测试后 git add，再 rebase continue。共享分支避免随意强制推送。"),
    ("m08", "Git 撤销提交", "已推送的错误提交用 git revert 生成反向提交。reset 会改写历史，适合尚未共享的本地提交。"),
    ("m09", "Python 异步阻塞", "asyncio 事件循环被同步网络请求阻塞。使用异步 HTTP 客户端，CPU 密集工作放进进程池，避免 async 函数中 time.sleep。"),
    ("m10", "Python 模块路径", "ModuleNotFoundError 排查虚拟环境和 Python 解释器路径。使用 python -m 运行模块，通过 pip install -e 安装项目。"),
    ("m11", "RAG 检索评估", "RAG 需要标注查询与相关文档 ID，计算 Recall@5 和 MRR，分析漏召回与排序错误。评估查询与调参查询分开。"),
    ("m12", "RAG 分块决策", "知识库按标题和语义边界分块，保留来源、页码与上下文。过短分块丢失语义，过长分块增加噪声和 token 开销。"),
    ("m13", "Ollama 局域网", "Jetson Ollama 默认监听本机 11434。局域网访问检查监听地址与防火墙，用 HTTP chat 接口发送模型和消息。"),
    ("m14", "Jetson 推理内存", "Jetson 推理模型超出共享内存时减少上下文长度，选择量化模型，测量内存与吞吐，避免同时加载多个大模型。"),
    ("m15", "数据泄漏", "机器学习先划分训练、验证、测试集。标准化与特征选择仅在训练集拟合，最终测试集不参与调参。"),
    ("m16", "类别不平衡", "文本分类类别不平衡时查看各类召回和 macro F1，设置 class weight，使用分层划分，不能只看 accuracy。"),
    ("m17", "日志隐私", "应用日志不记录密钥和完整用户隐私，错误信息脱敏，使用请求 ID 关联调用链，设置日志保留时间。"),
    ("m18", "API 重试", "远程 API 超时使用带抖动的指数退避并限制重试次数。写操作配合幂等键，避免重复扣款或重复提交。"),
    ("m19", "SQLite 并发", "SQLite 写入是单写者模式。事务保持短小，设置 busy timeout，适当启用 WAL，避免长事务阻塞其他连接。"),
    ("m20", "PostgreSQL 死锁", "PostgreSQL 死锁来自事务锁顺序不一致。统一更新顺序，缩短事务，并在安全边界重试整个事务。"),
    ("m21", "检索混合方案", "技术文档检索可融合 BM25 关键词检索和向量语义检索，使用 RRF 排序融合。独立验证集选择参数，分析同义改写失败。"),
    ("m22", "TF-IDF 中文方案", "中文 TF-IDF 可采用字符 ngram，避免依赖分词器。在语料上拟合 IDF，再对查询计算余弦相似度，按分数返回记忆 ID。"),
    ("m23", "服务性能指标", "服务延迟报告区分 P50 P95 P99，注明数据规模、并发和冷启动时间。使用压测分析尾延迟与资源瓶颈。"),
    ("m24", "文档更新溯源", "个人记忆保存原始来源、时间和版本。新事实与旧事实冲突时标记待核实，保留修改历史，避免直接覆盖证据。"),
]
VALIDATION = [
    ("v01", "MySQL 连接池等待导致请求超时怎么排查", ["m01"]),
    ("v02", "Redis 不存在的 key 一直查数据库", ["m03"]),
    ("v03", "Docker 容器退出 137 内存不足", ["m05"]),
    ("v04", "Git rebase 冲突如何处理", ["m07"]),
    ("v05", "asyncio 同步请求阻塞事件循环", ["m09"]),
    ("v06", "RAG 检索效果用哪些指标评估", ["m11"]),
    ("v07", "Jetson Ollama 如何在局域网访问", ["m13"]),
    ("v08", "文本分类类别不平衡不能只看 accuracy", ["m16"]),
    ("v09", "SQLite 并发写事务被阻塞", ["m19"]),
    ("v10", "BM25 和向量检索怎么融合", ["m21"]),
    ("v11", "中文 TF-IDF 字符 ngram 余弦相似度", ["m22"]),
    ("v12", "API 超时指数退避与幂等重试", ["m18"]),
]
TEST = [
    ("t01", "SQL 慢查询 EXPLAIN 联合索引", ["m02"]),
    ("t02", "Redis TTL 同时过期引发雪崩", ["m04"]),
    ("t03", "Docker 内 localhost 不能访问外部服务", ["m06"]),
    ("t04", "已经推送的 Git 提交怎样撤销", ["m08"]),
    ("t05", "Python 虚拟环境出现 ModuleNotFoundError", ["m10"]),
    ("t06", "知识库文档分块过短丢失上下文", ["m12"]),
    ("t07", "训练测试数据泄漏和特征拟合", ["m15"]),
    ("t08", "记忆来源版本与新旧事实冲突", ["m24"]),
    ("t09", "如何报告服务延迟 P95 和冷启动", ["m23"]),
    ("t10", "数据库并发事务锁冲突", ["m19", "m20"]),
]


def write(name, records):
    (ROOT / "data" / name).write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    write("memories.json", [{"id": uid, "text": title + "：" + body,
                            "source": "synthetic://notes/" + uid}
                           for uid, title, body in NOTES])
    for name, rows in [("validation_queries.json", VALIDATION), ("test_queries.json", TEST)]:
        write(name, [{"id": uid, "query": query, "relevant_ids": relevant}
                     for uid, query, relevant in rows])
