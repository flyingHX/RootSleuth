"""Golden Set 离线评测框架（P0-3 检索质量 / P1-1 生成质量 / Trust Index）。

目录职责：
- golden_set_v1.jsonl：50 条黄金查询（query -> relevant_case_ids 标注）；
- golden_corpus.jsonl：黄金知识语料（14 个案例文档，与查询标注配套）；
- metrics.py：Precision@K / Recall@K / MRR / Hit Rate / Context Recall / Trust Index 纯函数；
- retrieval_eval.py：检索评测运行器（lexical 基线模式 + Milvus pipeline 模式）+ 质量门禁；
- generation_eval.py：Faithfulness / Citation Accuracy / Answer Relevance / 幻觉率比对器
  + Trust Index T = 0.4F + 0.35C + 0.25(1-P)。

使用（aiops-rag-system 根目录）：
    python -m evaluation.run_retrieval_eval --mode lexical --fail-on-gate
    python -m evaluation.run_generation_eval --fail-on-gate
CI：tests/evaluation/test_golden_eval.py 以黄金集跑门禁断言（无中间件可运行）。
"""
