"""Reproduce authored synthetic notes and independently labelled query splits."""

import argparse
import json
from collections import Counter
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

# v2 adds nearby topics to the same corpus. Query labels are authored before evaluation;
# no algorithm outputs are used to manufacture relevance labels or remove difficult cases.
EXTRA_NOTES = [
    ('m25', 'MySQL 认证失败', '连接数据库出现 Access denied 时核对用户名、密码和授权主机。认证失败与连接池借出后未归还是不同问题。'),
    ('m26', 'MySQL 行锁等待', 'MySQL 更新语句卡住时检查事务和行锁等待，定位未提交的长事务。缩短事务并统一更新顺序；扩连接池不能解除行锁。'),
    ('m27', 'Redis 热键', 'Redis 单个热门 key 流量过高会压满一个实例。使用本地缓存与请求合并，评估分片；这与请求不存在数据的缓存穿透不同。'),
    ('m28', 'Redis 缓存击穿', '一个热点 key 失效后并发请求直接访问数据库是缓存击穿。可用互斥锁重建缓存或逻辑过期；大量 key 同时过期才是雪崩。'),
    ('m29', 'Docker 磁盘不足', 'Docker 写日志或拉取镜像报 no space left on device 时检查磁盘容量和 inode，配置日志轮转并清理未用镜像。内存上限不会释放磁盘。'),
    ('m30', 'Docker 应用退出', 'Docker 容器 exit code 1 往往来自应用异常或配置错误，先查看 stderr 与启动参数；退出码 137 和 OOMKilled 才提示内存问题。'),
    ('m31', 'Git 未跟踪文件', 'Git 切换分支提示 untracked files would be overwritten 时先备份未跟踪文件，或用 git stash -u 保存后切换。不要直接删除个人文件。'),
    ('m32', 'Git merge 冲突', 'Git merge 的冲突解决后需要提交合并结果。rebase 冲突则在 git add 后 rebase continue，二者后续命令不同。'),
    ('m33', 'Python CPU 并行', 'Python CPU 密集计算不适合指望普通线程绕过 GIL。使用多进程并测量序列化开销；异步 HTTP 解决等待型 IO 而非 CPU 并行。'),
    ('m34', 'Python 依赖版本冲突', 'ImportError cannot import name 常见于依赖版本不兼容。核对锁文件与包版本，重新安装兼容依赖；模块完全找不到时才优先排查解释器路径。'),
    ('m35', 'RAG 回答忠实性', 'RAG 回答是否得到引用文档支持属于忠实性评估。检索 Recall 衡量找回相关文档，不能直接说明生成的回答是否编造事实。'),
    ('m36', 'RAG 表格解析', 'PDF 表格跨页时保留列标题、行关系和页码，检查 OCR 错字。解析质量影响后续分块，但调整 chunk 大小无法恢复丢失的单元格结构。'),
    ('m37', 'Ollama 推理慢', 'Ollama 能连通但生成速度慢时检查模型是否使用 GPU，观察 tokens 每秒与并发，区分模型冷加载耗时与生成耗时。'),
    ('m38', 'Jetson 存储空间', 'Jetson 下载模型失败或设备写满时检查磁盘剩余空间，移除不再使用的模型。共享内存不足需要量化或缩小上下文，不靠清空磁盘解决。'),
    ('m39', '时序特征泄漏', '预测未来销量时，滚动均值只能使用预测时刻之前的数据，不能把未来观测加入窗口。时间序列按时间切分；随机划分可能泄漏未来。'),
    ('m40', '分类决策阈值', '二分类概率阈值影响 Precision 与 Recall。根据验证集业务代价选择阈值，阈值变化不同于训练时的 class weight；最终测试不能用于挑阈值。'),
    ('m41', 'API 身份认证', 'API 返回 HTTP 401 时检查 Bearer token 是否缺失、过期或签名错误，重新获取有效凭据。无限重试无法修复无效身份。'),
    ('m42', 'API 限流', 'API 返回 HTTP 429 时尊重 Retry-After，降低请求并发并退避。限流不是 HTTP 401 身份认证失败，不应反复更换密钥。'),
    ('m43', 'SQLite 查询索引', 'SQLite SELECT 缓慢时使用 EXPLAIN QUERY PLAN，给 WHERE 与排序字段建索引。查询扫描过多行不同于并发写事务等待。'),
    ('m44', 'PostgreSQL 连接空闲事务', 'PostgreSQL idle in transaction 会长期持有事务和锁。监控空闲事务并设置 idle_in_transaction_session_timeout，及时提交或回滚。'),
    ('m45', '向量检索版本', '向量索引保存 embedding 模型名和版本。更换向量模型后重新生成文档向量与索引；不同模型空间的旧向量不能直接混用。'),
    ('m46', '敏感文档访问', '知识库检索前按用户和租户过滤文档权限，避免把他人的私人笔记送入生成模型。日志脱敏无法替代文档访问控制。'),
    ('m47', '知识库页面缓存', '知识库网页更新后仍显示旧内容时检查浏览器缓存和 CDN 缓存并清理旧资源。页面缓存刷新与事实冲突的证据版本管理不同。'),
    ('m48', 'DNS 缓存失效', '服务域名迁移后客户端仍连接旧 IP 时检查 DNS 缓存与记录 TTL，刷新解析结果。缓存不存在的业务数据与域名解析缓存不同。'),
]

EXTRA_VALIDATION = [
    ('v13', 'MySQL 可借用的连接都占着，请求一直排队，怎样让资源释放', ['m01'], 'paraphrase', ['m25', 'm26'], '借用连接不释放对应连接池耗尽，认证和行锁不是主因。'),
    ('v14', 'Redis 查一个根本没有的数据，每次又去后面的数据库，怎么挡住', ['m03'], 'paraphrase', ['m27', 'm28'], '不存在的数据反复回源是穿透，热门键流量和过期重建是负例。'),
    ('v15', 'asyncio 里用了同步 HTTP，其他协程全停了，怎样避免等它', ['m09'], 'paraphrase', ['m33'], '等待型网络 IO 阻塞事件循环，非 CPU 并行问题。'),
    ('v16', 'RAG 怎样知道该找的资料有没有被找回来，第一条是否排对了', ['m11'], 'paraphrase', ['m35'], '召回和首条排序评估属于检索质量，不是回答忠实性。'),
    ('v17', 'Ollama 在 Jetson 自己能用，另一台电脑却连不进去，先查什么', ['m13'], 'paraphrase', ['m37', 'm38'], '跨机器连通性对应监听和防火墙，非速度或磁盘问题。'),
    ('v18', '分类样本里少数类别太少，总体命中率很好但漏掉它们怎么评估', ['m16'], 'paraphrase', ['m40'], '类别不均衡应查看各类召回与 macro F1，非单纯调概率阈值。'),
    ('v19', 'SQLite 两个人同时保存，后一个一直拿不到写机会，怎样缩短等待', ['m19'], 'paraphrase', ['m43'], '并发写者等待对应事务和锁，查询索引不是主因。'),
    ('v20', '检索只看词面常漏掉换种说法的笔记，BM25 怎样配合语义向量', ['m21'], 'paraphrase', ['m22', 'm45'], '需要融合关键词与语义排名，非单一 TF-IDF 或索引迁移。'),
    ('v21', 'MySQL Access denied，密码或授权主机应该怎样核对', ['m25'], 'hard_negative', ['m01'], '明确认证失败，不是连接池耗尽。'),
    ('v22', 'Redis 一个热点 key 刚过期就有大量请求回源，互斥重建怎么做', ['m28'], 'hard_negative', ['m03', 'm04', 'm27'], '单键过期击穿，区分穿透、多键雪崩与未过期热键。'),
    ('v23', 'Docker 拉镜像报 no space left on device，要清理什么', ['m29'], 'hard_negative', ['m05', 'm30'], '存储空间与 inode 排查，非 OOM 或应用退出。'),
    ('v24', 'Git 提交已经共享，我想保留历史再产生一笔反向修改', ['m08'], 'hard_negative', ['m07', 'm31'], '共享提交撤销用 revert，非 rebase 冲突或未跟踪文件保护。'),
    ('v25', 'Python ImportError cannot import name，锁文件里的包版本不兼容', ['m34'], 'hard_negative', ['m10'], '包已存在但接口版本不符，区别模块找不到。'),
    ('v26', 'RAG 回答引用了资料却编造结论，应该检查哪种质量', ['m35'], 'hard_negative', ['m11'], '检查回答忠实性；找到相关文档不证明回答正确。'),
    ('v27', 'API 返回 401，Bearer token 已过期还应该一直重试吗', ['m41'], 'hard_negative', ['m18', 'm42'], '凭据过期需要更新身份，区别超时重试与限流。'),
    ('v28', 'SQLite SELECT 扫描太多行，怎么用执行计划给 WHERE 建索引', ['m43'], 'hard_negative', ['m19'], '读查询扫描和索引问题，非并发写等待。'),
    ('v29', 'MySQL 请求超时，如何分别排查连接池耗尽和慢查询索引', ['m01', 'm02'], 'multi_relevance', ['m25'], '两部分分别要求连接释放和执行计划，认证失败不直接回答。'),
    ('v30', 'Redis 不存在的数据穿透和许多 TTL 同时失效，两种故障如何保护数据库', ['m03', 'm04'], 'multi_relevance', ['m28'], '两种明确故障分别穿透与雪崩，非仅单热键击穿。'),
    ('v31', 'Docker 容器既出现 OOMKilled 又无法访问外部服务，要从内存与网络两边查', ['m05', 'm06'], 'multi_relevance', ['m29'], '同时询问内存和网络，磁盘负例仅有相同产品名。'),
    ('v32', 'Git rebase 冲突解决后如何继续，以及共享的错误提交如何撤销', ['m07', 'm08'], 'multi_relevance', ['m32'], '要求 rebase continue 和 revert，不是 merge 后提交。'),
    ('v33', 'RAG 的文档怎么分块，之后怎么用 Recall 和 MRR 检查检索', ['m12', 'm11'], 'multi_relevance', ['m35'], '需要分块和检索指标，回答忠实性是相近评估主题。'),
    ('v34', 'Jetson Ollama 的局域网访问与模型共享内存不足，分别怎样配置', ['m13', 'm14'], 'multi_relevance', ['m38'], '同时要求连通性和推理内存管理，磁盘不是共享内存。'),
    ('v35', 'API 请求超时怎样做幂等退避，同时日志如何避免保存密钥', ['m18', 'm17'], 'multi_relevance', ['m41'], '要求重试与日志隐私两项，非身份失效处理。'),
    ('v36', '个人记忆新旧事实冲突如何保留证据，检索又怎样按租户控制访问', ['m24', 'm46'], 'multi_relevance', ['m47'], '要求事实版本与访问权限，刷新页面缓存不足以回答。'),
]

# Every v2 final-test wording is new. These rows are fixed before running benchmarks.
FINAL_TEST_V2 = [
    ('t01', 'MySQL 更新记录等锁，应该怎样定位没有提交的长事务', ['m26'], 'direct', ['m01', 'm02'], '写操作行锁等待，连接池和慢读索引是相近负例。'),
    ('t02', 'Redis 未过期的热门键让单个实例负载太高，怎样合并请求', ['m27'], 'direct', ['m28', 'm04'], '未过期热键流量过高，非缓存失效。'),
    ('t03', 'Docker exit code 1，要先看应用日志还是 OOMKilled', ['m30'], 'direct', ['m05'], '码 1 的应用异常排查，码 137 的内存故障是负例。'),
    ('t04', 'PDF 跨页表格 OCR 后列对应错乱，怎样保留表头与页码', ['m36'], 'direct', ['m12'], '要求恢复表格结构，不能仅调整分块长度。'),
    ('t05', 'Ollama 连接成功但 tokens 每秒很低，怎样检查 GPU 和冷加载', ['m37'], 'direct', ['m13'], '已连通，重点生成性能而非监听地址。'),
    ('t06', 'embedding 模型换版本以后，旧文档向量索引能直接继续用吗', ['m45'], 'direct', ['m21', 'm22'], '要求向量空间迁移，非排名融合或字符检索。'),
    ('t07', 'SQL 读数据很慢，怎样看执行计划避免翻遍整张表', ['m02'], 'paraphrase', ['m26'], '读取慢与索引，不是写事务行锁。'),
    ('t08', 'Redis 许多键约好同一时刻失效，后端瞬间被请求打满怎么办', ['m04'], 'paraphrase', ['m28', 'm03'], '多键同时失效对应雪崩。'),
    ('t09', '容器里访问 localhost 找不到宿主的服务，名称解析和映射该怎么查', ['m06'], 'paraphrase', ['m13', 'm48'], '容器网络地址范围而非外部模型监听或迁移旧域名。'),
    ('t10', 'Python 明明装了包却说模块不存在，如何确认是不是用了另一个解释器', ['m10'], 'paraphrase', ['m34'], '模块搜索路径/解释器，非已有包版本接口差异。'),
    ('t11', '检索资料切得太碎看不懂，如何把章节上下文和来源一起保留', ['m12'], 'paraphrase', ['m36'], '分块上下文缺失；未询问表格恢复。'),
    ('t12', '模型预处理先看了所有数据，测试成绩会不会被提前的信息影响', ['m15'], 'paraphrase', ['m39'], '一般特征拟合污染；未指明未来时序窗口。'),
    ('t13', '以前记住的结论与今天的证据不同，如何保留出处而不直接抹掉旧记录', ['m24'], 'paraphrase', ['m47'], '事实冲突与溯源，而非页面刷新。'),
    ('t14', '服务平均响应快但少数请求特别慢，怎么报告尾部耗时与启动成本', ['m23'], 'paraphrase', ['m37'], '通用尾延迟和冷启动报告，非只针对 Ollama GPU。'),
    ('t15', 'Git checkout 提示未跟踪文件会被覆盖，如何先把个人文件安全收起来', ['m31'], 'hard_negative', ['m07', 'm08'], '切分支前保护未跟踪文件，不是解合并冲突或撤销提交。'),
    ('t16', 'Python 算法全在做 CPU 运算，用 asyncio HTTP 客户端能解决 GIL 并行吗', ['m33'], 'hard_negative', ['m09'], 'CPU 并行需要多进程，不是等待网络。'),
    ('t17', 'Jetson 模型下载到一半磁盘写满，应清理模型还是缩小上下文', ['m38'], 'hard_negative', ['m14'], '模型文件占存储，非推理共享内存不足。'),
    ('t18', '销量预测的滚动窗口把明天观测加进来了，时间划分该怎么修正', ['m39'], 'hard_negative', ['m15'], '未来时间窗口泄漏有专门时间切分约束，通用预处理不足以完整回答。'),
    ('t19', '分类概率切分点怎样按误报漏报代价选择，能用最终测试挑吗', ['m40'], 'hard_negative', ['m16'], '要求阈值选择与测试隔离，训练类别权重并非同一操作。'),
    ('t20', 'HTTP 429 有 Retry-After，要降低并发还是重新获取认证凭据', ['m42'], 'hard_negative', ['m41'], '限流响应与身份认证不同。'),
    ('t21', '知识库私有笔记不能让别的租户检索，只有日志脱敏够吗', ['m46'], 'hard_negative', ['m17'], '需要检索前权限过滤，日志规则无法替代。'),
    ('t22', '知识库网页改好了浏览器还显示旧版，怎样刷新 CDN 和本地缓存', ['m47'], 'hard_negative', ['m24'], '页面缓存刷新，与事实冲突存证不同。'),
    ('t23', 'Redis 一个热键刚失效和多个键一起失效，各自用锁重建还是 TTL 抖动', ['m28', 'm04'], 'multi_relevance', ['m27'], '单键击穿与多键雪崩两种失效方案。'),
    ('t24', 'Python 模块找不到和包版本不兼容各该怎样排查解释器与锁文件', ['m10', 'm34'], 'multi_relevance', ['m33'], '两种导入失败原因，非 CPU 并行。'),
    ('t25', 'RAG 既要衡量相关资料能否找回，也要检查回答有没有脱离引用', ['m11', 'm35'], 'multi_relevance', ['m12'], '检索质量与回答忠实性两类评估。'),
    ('t26', 'Jetson 存模型的磁盘不够和运行模型的共享内存不够，应分别怎样处理', ['m38', 'm14'], 'multi_relevance', ['m13'], '存储清理与推理量化/上下文两项，未询问连通性。'),
    ('t27', 'API 无效身份返回 401 和请求太多返回 429，如何分别处理', ['m41', 'm42'], 'multi_relevance', ['m18'], '更新身份与尊重限流，单纯超时重试不足以回答。'),
    ('t28', 'SQLite 写入等其他事务结束和 SELECT 慢，怎样分别缩短事务与建索引', ['m19', 'm43'], 'multi_relevance', ['m20'], '同一产品的写锁等待与读索引优化，不是 PostgreSQL 死锁。'),
    ('t29', '检索怎样融合 BM25 与向量，以及换 embedding 模型后索引如何迁移', ['m21', 'm45'], 'multi_relevance', ['m22'], '融合检索与向量版本迁移分别需要两条经验。'),
    ('t30', '应用日志避免暴露用户隐私，私人文档检索怎样按租户权限过滤', ['m17', 'm46'], 'multi_relevance', ['m47'], '日志脱敏和检索授权是两项独立约束。'),
]


def write(directory, name, records):
    (directory / name).write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build_dataset():
    memories = [{"id": uid, "text": title + "：" + body,
                            "source": "synthetic://notes/" + uid}
                for uid, title, body in NOTES + EXTRA_NOTES]
    validation = [{"id": uid, "query": query, "relevant_ids": relevant, 'category': 'direct',
                   'hard_negative_ids': [], 'annotation': '继承 v1 的词面基线查询。'}
                  for uid, query, relevant in VALIDATION]
    def labelled(rows):
        return [{'id': uid, 'query': query, 'relevant_ids': relevant, 'category': category,
                 'hard_negative_ids': negative, 'annotation': reason}
                for uid, query, relevant, category, negative, reason in rows]
    validation += labelled(EXTRA_VALIDATION)
    test = labelled(FINAL_TEST_V2)
    metadata = {'version': '2.0', 'source': 'project-authored synthetic notes and relevance labels',
                'created_on': '2026-10-14', 'memory_count': len(memories),
                'splits': {name: {'query_count': len(rows), 'categories': dict(Counter(q['category'] for q in rows))}
                           for name, rows in [('validation', validation), ('test', test)]},
                'label_policy': '直接回答问题为相关；相同产品但不同故障/操作为相近负例；多问题标注所有直接答案。',
                'limitations': ['模拟资料，未经过外部业务专家审核', '有答案、二元相关性；未覆盖无答案与相关性等级',
                                '最终查询在评估前固定；开发后续调参须另建独立测试集']}
    return {'memories.json': memories, 'validation_queries.json': validation,
            'test_queries.json': test, 'dataset.json': metadata}


def main():
    parser = argparse.ArgumentParser(description='确定性重建 v2 模拟检索评估集')
    parser.add_argument('--output', type=Path, default=ROOT / 'data')
    directory = parser.parse_args().output
    directory.mkdir(parents=True, exist_ok=True)
    for name, records in build_dataset().items():
        write(directory, name, records)


if __name__ == '__main__':
    main()
