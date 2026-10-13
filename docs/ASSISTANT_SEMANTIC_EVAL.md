# 语义检索实验方向（尚未发布）

语义检索后端及 ONNX 可选依赖尚未接入正式产品入口，本批 PR 不包含其模块、依赖或运行命令。正式公开题集入口只发布 baseline/compact；离线比较器可检查已有实验报告，但不代表发布版本能够产生 semantic 运行。

实验方向为本地 dense/lexical 混合召回与跨编码器重排。初步离线诊断只用于观察证据覆盖和上下文规模，不能代替回答准确率、实际 API 用量或 GPU 性能。模型来源参考 [Sentence Transformers 检索重排说明](https://sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html)。

集成前应完成模型文件校验、缓存失效、中文效果、产品存档适配及原生入口验收，再在冻结公开留出题集上进行完整前后对照。当前模型服务过滤阻断完整对照，不晋升产品默认基线。
