# 文档检查评估材料

- document_field_completeness：18 条开发验证、18 条最终测试。
- document_source_references：22 条开发验证、22 条最终测试。

每行包含 id、category、payload、expected。payload 是生成组件可接收的输入；expected 始终保留在可信评估进程。标注是按公开契约构造并静态保存的，不在运行时从基线或被测实现取得。

两组样例使用不同记录、来源、文本块 ID 与文本，preflight 拒绝 ID 或完整 payload 跨 split 重叠。类别和构造模式仍有重合，这是小型固定合成评估集，不能当作独立真实业务泛化实验。扩展真实资料时应由业务方核对标注，并继续隔离最终测试。

检查范围及所有状态定义见 docs/capability-factory.md。引用检查只验证位置与原文精确一致，不能判断语义支持或客观真实性。
