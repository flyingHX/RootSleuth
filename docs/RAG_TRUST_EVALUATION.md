# RAG 告警知识库可信度评估报告

> 评估日期：2026-09-15
> 评估方法：代码级证据审计（静态核查 + 已执行回归记录）对照业界五层可信度指标基准
> 评估范围：`aiops-rag-system/`（RAG 流水线）+ `app/backend`（控制台/Agent/治理）

---

## 一、总体结论

**四大功能目标：全部达成（MVP 级真实闭环）**

| 功能目标 | 结论 | 核心证据 |
|---------|------|---------|
| 构建知识库 | ✅ 达成 | kb-sync 幂等 upsert/delete/verify、kb_version 版本守卫（409 拒绝旧版本覆盖）、补偿闭环 E2E 37/37、Milvus Schema 交集兼容 |
| 治理知识库 | ✅ 基本达成 | 审批流（sre 起草 + kb_admin 审批）、回滚快照、归档、合并提案、lifecycle-patrol（90 天 + 负反馈自动老化）、黑名单 |
| 深度诊断告警 | ✅ 达成 | ReAct Agent 五大工具、四层重排漏斗（L1→L2→L3∥L4→Fusion）、tool_trace 必传证据链、六信号置信度、全链路降级 |
| 生成报告 | ✅ 达成 | oncall_reports 持久化（report_json/chatops_text）、AI ChatOps 报告生成、报告-会话双向关联、确定性降级 |

**可信度指标体系：多数指标处于「有机制、未度量」或「未建设」状态，尚不能宣称达标。**

分层达标情况一句话概括：
- 第三层（知识库健康度）**接近达标**：新鲜度治理已落地。
- 第一、二层（检索/生成质量）**机制到位但零量化**：有四层重排与幻觉校验，但没有任何 Precision/Recall/Faithfulness 的评测管道。
- 第四层（安全性）**存在硬伤**：控制台写路径权限完备，但 RAG 检索层无鉴权、无租户隔离，投毒检测完全缺失。
- 第五层（Trust Index）**无法计算**：F/C/P 三个输入均无度量来源。

---

## 二、五层指标逐层对照

### 第一层：检索质量

| 指标 | 业界目标 | 现状评级 | 证据与差距 |
|------|---------|---------|-----------|
| Precision@1 | > 0.7 | ⚪ 未度量 | 无评测脚本（全仓无 eval/golden 资产）。正面机制：L1 规则硬过滤剔除过期/黑名单/差评案例，L2 七特征融合，L3 Cross-Encoder 精排，L4 Listwise + 幻觉校验，架构上具备冲高 P@1 的条件 |
| Precision@5 | > 0.5 | ⚪ 未度量 | 同上；漏斗 20→15→10→5→3 压缩路径完整 |
| Recall@10/20 | > 0.8/0.9 | ⚪ 未度量 | 双路召回 Top-20 固定窗口；无标注集验证窗口是否够用 |
| Hit Rate | 90%+ | ⚪ 未度量 | hit_count/recall_count 已回写 Milvus（update_recall_stats），**原始数据已具备**，但无聚合报表 |
| MRR | > 0.6 良 / > 0.8 优 | ⚪ 未度量 | 排序质量取决于 L3 是否加载（无 GPU 环境降级为 L2+L4），实测分布未知 |

**差距根因**：缺少 Golden Set（黄金测试集）与离线评测脚本。检索链路的"原材料"（召回统计、四层分数、降级事件）都已埋点，缺的是带标注的查询-标准案例对照数据与计算器。

### 第二层：生成质量

| 指标 | 业界目标 | 现状评级 | 证据与差距 |
|------|---------|---------|-----------|
| Faithfulness | > 0.85（告警线）/ > 0.95（高要求） | ⚪ 未度量 | 无忠实度评测。结构性保障存在：诊断结论必传 tool_trace（`_validate_diagnose_result` 强校验），evidence_chain 经 `_filter_evidence_by_trace` 与工具观察做 token 重叠过滤（EVIDENCE_TOKEN_OVERLAP），不重叠证据被剔除计数 |
| Answer Relevance | > 0.8 | ⚪ 未度量 | Prompt 强约束输出 root_cause/solution/command（中文、分步骤）；无相关性打分管道 |
| Hallucination Rate | < 5% | 🟡 部分防护 | L4 幻觉校验：过滤未知 case_id、按原顺序补全遗漏；诊断侧证据链只能引用工具观察。但**无幻觉率量化监控**，无 5% 告警线 |
| Citation Accuracy | > 95% | 🟡 结构达标未量化 | 每条结论可追溯：tool_trace 落库（20000 字符上限）、superseded 会话保留旧结论、审计留痕。但引用与知识条目的自动比对器未建设 |

### 第三层：知识库健康度

| 指标 | 业界目标 | 现状评级 | 证据与差距 |
|------|---------|---------|-----------|
| 来源新鲜度（>90 天未更新归零） | 无 L4/L5 超 90 天 | 🟢 达标 | `lifecycle-patrol` 已实现：`kb_expire_days` 配置（默认 90 天），超期且 `feedback_score < 0` 自动归档，支持 dry_run 预览；L1 重排层 `l1_max_age_days`（默认 365 天）兜底过滤过期案例。健康报表（P2-1/P2-2）新增 `kb_expire_unconditional_days`（默认 180 天）独立无条件老化阈值：超期案例红级分级，0/留空回退 `2×kb_expire_days` 兼容旧口径，正反馈案例不再无限期存活 |
| 内容一致性（红/黄/绿三级） | 绿=可用，红>5% 失败必修 | 🔴 未建设 | 无红黄绿评估任务/报表。间接替代：补偿闭环 verify（同步后回读验证）+ verified 标志位 |
| 分块质量（Context Recall > 0.8） | > 0.8 | ⚪ 不适用/未度量 | 告警知识为案例级短文本（现象/根因/方案三元组），非长文档分块场景，该指标本身弱适用；但无度量确认 |

### 第四层：安全性与鲁棒性 ⚠️ 本层是最大短板

| 指标 | 业界目标 | 现状评级 | 证据与差距 |
|------|---------|---------|-----------|
| 投毒检测 FNR | < 2%，越低越好 | 🔴 未建设 | 全仓无投毒/恶意文档检测逻辑（poison/toxic/malicious 零命中）。**间接防线**：写路径有审批流（kb_edit 需 sre 起草 + kb_admin 审批）、L1 黑名单与差评过滤、kb_version 版本守卫防回滚覆盖——防的是"流程内投毒"，对"内容本身投毒"（如方案中藏危险命令、语义投毒）无任何检测 |
| 投毒检测 FPR | < 0.7% | 🔴 未建设 | 同上 |
| 权限安全性（越权召回率 = 0%） | 必须 0% | 🔴 不达标（硬伤） | **控制台写路径权限完备**：5 级 RBAC（viewer/operator/sre/kb_admin/sys_admin）、require_role 校验、can_edit_kb 阈值、kb_admin 才能删除/合并。**但 RAG 检索层裸奔**：`/api/v1/kb-search`、`/api/v1/kb-sync`、`/diagnostic` 等路由无任何鉴权依赖（无 Depends/verify_token/Authorization）；Milvus 检索表达式仅按 fingerprint/case_id/月分区过滤，**无 tenant/cluster/role 过滤条件**。任何能触达 RAG 端口的调用方可检索全部知识，越权召回率无法保证 0% |
| 数据完整性 | — | 🟢 达标 | kb_version 版本守卫：旧版本覆盖 409 终态拒绝、同内容幂等短路、补偿 409 清账不进死信；E2E 场景 I（I1-I8）全过 |

### 第五层：综合信任指数

| 指标 | 参考基准 | 现状评级 | 说明 |
|------|---------|---------|------|
| Trust Index（T = 0.4F + 0.35C + 0.25(1-P)） | 无统一值，看趋势 | 🔴 无法计算 | F（忠实度）、C（引用准确率）、P（幻觉率）均无度量管道。参考区间 0.73-0.81 仅在 TruthfulQA 类基准上有区分度，本项目无对应测试集 |

**已具备的可观测基础**（可复用于质量指标扩展）：Prometheus 指标 rag_latency/rag_stage_latency（分阶段 P99）、rerank_layer_latency、rerank_degraded_total（分层降级事件）、rerank_final_total（漏斗出口分布）、rag_embed_fallback_total（Embedding 降级）、dedup_reduction_rate。这些是**性能/可用性**指标，质量维度（F/C/P）需新增。

---

## 三、结论判定

### 达成判定：功能目标 ✅ / 指标目标 ❌（当前不可宣称达标）

1. **构建、治理、诊断、报告四大功能闭环均为真实端到端实现**（非演示桩），有 78 项 RAG 回归 + 37/37 补偿 E2E + 真实 PostgreSQL 迁移背书。作为 AIOps 平台 MVP，工程完成度扎实。
2. **按用户给出的可信度基准衡量，项目处于「机制先行、度量缺位」阶段**：
   - 🟢 已达标：新鲜度治理（90 天自动老化）、写路径权限隔离、引用链可追溯、数据完整性（版本守卫）
   - 🟡 结构到位未量化：检索质量（四层重排无评测）、生成质量（幻觉校验无监控）、反馈数据（已回写无报表）
   - 🔴 缺失：RAG 检索层鉴权与租户隔离（**安全性硬伤**）、投毒检测、Golden Set 评测设施、红黄绿一致性、Trust Index

### 与业界基准的差距根因（按优先级）

- **P0-1 越权召回（违反 0% 硬性指标）**：RAG 服务面向内网但零鉴权；Milvus 检索无租户/角色表达式。修复路径：RAG 路由加 API Key/JWT 依赖 + 检索请求携带 tenant_id/cluster 并注入 expr 过滤（Milvus 分区或标量过滤均可）。
- **P0-2 Golden Set 与评测管道缺失（P@K/R@K/MRR/Faithfulness/幻觉率全部无法出数）**：建议从种子数据（12 案例 + 25 事件）起步构建 50-100 条标注查询对，脚本化计算 Precision@1/5、Recall@10/20、MRR、Hit Rate，跑出真实分布后替换业界参考值。
- **P1-1 投毒检测**：最小可行方案为入库前规则扫描（危险命令模式：rm -rf、curl|sh、chmod 777、外发数据域名等）+ LLM 审查员二次把关，审批流已具备人审卡点，缺自动预检。
- **P1-2 质量监控告警线**：复用现有 Prometheus 体系新增 faithfulness/hallucination/citation 三类指标，按业界建议以 0.85 设生产告警线。
- **P2-1 红黄绿一致性**：基于补偿 verify 结果 + 反馈分聚合出案例级/库级健康分，前端已有 OpsPage 可挂报表。
- **P2-2 老化口径收紧**：lifecycle-patrol 的 `feedback_score < 0` 条件放宽了 90 天口径，核心处置文档建议增加"无条件按 updated_at 老化"的分级配置。

### 建议达标路径（预计工作量）

| 阶段 | 内容 | 产出指标 |
|------|------|---------|
| 第一阶段 | RAG 鉴权 + 租户过滤 + Golden Set v1（50 条）评测脚本 | 越权召回 0%、P@1/R@10/MRR/Hit Rate 首批实测值 |
| 第二阶段 | 生成质量评测（faithfulness/citation 比对器）+ Prometheus 告警线 | F/C/P 可计算、0.85 告警线生效 |
| 第三阶段 | 投毒预检 + 红黄绿报表 + Trust Index 看板 | T 指数可计算、趋势可观测 |

---

## 四、评估依据索引

- 检索链路：`aiops-rag-system/src/rag_pipeline/{retriever,rerank_pipeline,reranker_l1..l4}.py`
- 证据链校验：`app/backend/services/console_agent.py`（`_validate_diagnose_result`、`_filter_evidence_by_trace`、tool_trace 落库）
- 治理与权限：`app/backend/services/console_kb.py`（require_role/kb_admin 卡点/lifecycle_patrol）、`console_common.py`（5 级 RBAC）
- 版本守卫与补偿：`app/backend/services/rag_sync.py`、`models/rag_sync_tasks.py`、`scripts/verify_rag_compensation_e2e.py`
- RAG 层无鉴权证据：`aiops-rag-system/src/api/*.py`（无 Depends/鉴权依赖）、`milvus_client.py`（expr 无租户过滤）
- 指标现状：`aiops-rag-system/src/utils/metrics.py`
- 回归记录：`.atoms/PROGRESS.md`（pytest 78/0/0、E2E 37/37、迁移 head c9d5e2f7a8b1）
