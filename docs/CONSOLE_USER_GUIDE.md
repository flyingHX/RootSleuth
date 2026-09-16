# AIOps 运营控制台使用手册

> 适用对象：业务运维、值班工程师、SRE、审批人与系统管理员。
> 控制台内置「帮助中心」页面（侧边栏 → 使用手册）提供本文摘要版。

## 1. 登录与角色权限

### 登录方式
- 正式登录：点击「使用 Atoms 账号登录」，完成 OIDC 授权后自动回调进入控制台。
- 预览/演示环境：登录页提供四个演示账号快捷登录（走后端 `/api/v1/auth/demo-login`，与正式登录共用同一 JWT 签发链路）：
  - `demo-operator@atoms.dev` → 值班运维
  - `demo-sre@atoms.dev` → SRE
  - `demo-lead@atoms.dev` → 审批人 / SRE Lead
  - `demo-admin@atoms.dev` → 系统管理员

### 角色层级（由低到高）
| 角色 | 标签 | 主要能力 |
|------|------|----------|
| viewer | 只读审计 | 查看全部页面、审计日志、重复案例扫描 |
| operator | 值班运维 | AI 诊断、告警反馈（点赞/点踩） |
| sre | SRE | 编辑知识库、发起变更/合并/模板晋升 |
| approver | 审批人 / SRE Lead | 审批通过/拒绝 |
| kb_admin | 知识库管理员 | 知识案例版本回滚、发布 |
| sys_admin | 系统管理员 | 规则发布/回滚、配置中心管理 |

- 未绑定角色的用户默认 `viewer`；可在「审计与配置 → 配置中心」调整 `default_role` 或 `role_bindings_json`。
- 权限按层级包含：高级角色自动拥有低级角色的全部能力。

## 2. 运营总览（Dashboard）

- **核心指标**：总事件数、降噪率（指纹去重）、未知率、RAG 成功率、RAG P99/平均耗时。
- **待办卡片**：待我审批数量、待晋升未知模板数量。
- **依赖健康**：向量检索、缓存、归档存储与 LLM 服务的状态摘要。
- **分布图**：按严重级别、错误类型 TOP、服务 TOP 与最近告警列表。

## 3. 告警工作台

1. **筛选**：支持严重级别、服务、集群、错误类型、状态、时间范围（1h/24h/7d/全部）与关键字（事件 ID / 模板 / 原始日志）。
2. **详情**：查看原始日志、指纹、拓扑与 RAG 召回案例（评分、根因、处置方案、反馈分）。
3. **AI 诊断**：一键调用 `deepseek-v4-flash` 输出根因/建议/命令；置信度低于阈值（默认 0.75）时标记「低置信」，请人工复核。每次诊断同时输出**质量评估卡**（Trust Index、Faithfulness、Context Coverage、Answer Relevance、幻觉率与未支撑断言），低于质量线 0.85 时同样提示复核。
4. **降级说明**：模型超时（`llm_timeout`）或输出异常（`invalid_json`）时自动降级，详情页展示降级原因，告警查看不受影响。
5. **反馈闭环**：
   - 👍/👎 更新召回案例反馈分（±1，范围 -5 ~ 5）。
   - 填写人工修正（根因/处置方案）会**自动生成知识库变更集**并进入审批流。

### 质量指标口径（置信度 vs Trust Index）

两个数字统计对象不同，同时展示、互不矛盾：

- **置信度（如 86%）**：模型对**根因结论**的主观自评把握度，阈值 `confidence_threshold=0.75`；
- **Trust Index（如 72.1%）**：**客观质量分**，衡量结论断言能在召回案例/告警证据中找到多少依据，`T = 0.4×Faithfulness + 0.35×Context Coverage + 0.25×(1−幻觉率)`，单次质量线 0.85；
- 典型场景：高置信 + 低 Trust Index = 模型自信但断言缺证据 → 必须人工复核；低置信 + 高 Trust Index = 结论谨慎但证据充分 → 可放心采信。

同一口径复用于 **Agent 工作台**：深度诊断（结论质量）、知识治理（起草案例质量与聚合达标率）、值班报告（报告质量，随当前报告与历史报告持久化展示）。

## 4. 知识库治理

### 案例编辑
- 修改字段实时预览**前后对比**，提交后按当前审批模式进入审批流或直接发布。
- 变更理由必填；全部操作写入审计日志。

### 新建案例（ID 自动生成）
- **无需手工填写案例 ID**。提交后由后端自动生成 `KB-YYYYMMDD-NNN` 格式的唯一 ID（当日序号，按已入库案例与在途新建变更集去重顺延）。
- 创建成功的提示与审批单内容中均会展示生成的案例 ID；并发创建同样保证唯一。

### 版本回滚
- `kb_admin` 及以上角色可回滚到任意历史版本；回滚**生成新版本快照**，历史完整保留可追溯。

### 去重合并
- 「扫描相似案例」按同错误类型 + 同服务且模板相似度 ≥80% 聚类，仅展示相似度达标的合并建议（低于 80% 不展示，同集群不再作为兜底条件）。
- 每个簇指定一个主案例（默认建议反馈分最高者），其余案例合并后**归档**；合并提案走审批流。

## 5. 审批中心

### 三个视图
- **待我审批**：当前步骤角色匹配且申请人不是自己的待审单。
- **我发起的**：本人提交的全部审批单（可撤回待审单）。
- **我已处理**：本人审批过的历史单。

### 审批模式（配置中心 `approval_mode`）
| 模式 | 说明 |
|------|------|
| OFF | 免审直发：变更提交后立即发布 |
| SINGLE_REVIEW | 单级审批：审批人一步 |
| MULTI_LEVEL | 多级审批：审批人 → 知识库管理员两步 |

### 审批内容与前后对比
- 每张审批单点击「查看审批内容与前后对比」即可展开：
  - **知识库变更（kb_edit）**：字段级修改前/修改后对比表（已过滤版本号等元数据与无变化字段）。
  - **知识合并（merge）**：主案例与被归档的冗余案例清单。
  - **模板晋升（rule_promote）**：未知模板原文、样本量、最近出现服务与晋升目标分类。

### 操作规则
- **禁止自审**：申请人不能审批自己的申请，系统强制校验。
- **通过**：终审通过后系统自动执行发布（知识库写新版本并失效语义缓存）/ 合并 / 规则晋升，并写审计。
- **拒绝**：变更集状态置为 rejected，内容不发布。
- **撤回**：仅申请人本人且状态为待审时可撤回。
- 时间线展示每一步的角色、审批人、动作、意见与时间。

## 6. 规则管理

- **YAML 校验**：规则 id 唯一、score ∈ (0,1]、keywords/pattern 至少其一、severity 合法。
- **发布**（sys_admin）：解析成功后写入新版本，旧激活版本自动标记「已替代」，热加载立即生效。
- **版本回滚**（sys_admin）：目标版本重新激活并热加载。
- **未知模板队列**：展示模板、样本量、最近出现服务与状态；晋升需填写目标错误类型，终审通过后按 0.6 分值、warning 级别追加规则并发布新版本；无需沉淀的模板可废弃。

## 7. 审计与配置

### 审计日志
- 记录全部关键操作：操作人、动作、对象类型/ID、前后状态、时间。
- 支持按动作、操作人、对象类型筛选。典型动作：`change_set_create`、`kb_publish`、`kb_rollback`、`kb_merge`、`approval_approve/reject/withdraw`、`rule_publish/reload/rollback`、`unknown_promote(_request)/discard`、`feedback`、`config_update`、`semantic_cache_invalidate`。

### 配置中心（sys_admin）
| 键 | 说明 |
|----|------|
| approval_mode | 审批模式 OFF / SINGLE_REVIEW / MULTI_LEVEL |
| confidence_threshold | 诊断置信度阈值（0~1） |
| rerank_weight_json | 重排权重（cosine/topology/time_decay/feedback） |
| diagnose_temperature | 诊断 Agent 独立采样温度（默认 0：同一事件重复深度诊断输出稳定；治理/值班 Agent 仍使用 llm_temperature=0.2） |
| diagnose_time_budget_seconds | 诊断墙钟时间预算（30~600 秒，默认 90）：多轮推理+强制收尾的总时长上限，超时自动降级/返回 502，防止 LLM 变慢时请求无限拉长 |
| llm_provider / llm_base_url / llm_api_key / llm_model / llm_temperature / llm_timeout_seconds | LLM 全局默认接入与模型参数（三个 Agent 独立配置留空时逐项继承；API Key 加密存储、脱敏展示） |
| diagnose_llm_provider / diagnose_llm_base_url / diagnose_llm_api_key | 诊断 Agent 独立 LLM 接入：接入方式（atoms_hub / openai_compatible）+ OpenAI 兼容 Base URL + API Key（留空逐项继承全局；API Key 加密存储、脱敏展示） |
| diagnose_llm_model / diagnose_llm_timeout_seconds | 诊断 Agent 独立模型与单次调用超时（10~300 秒；留空继承全局） |
| kb_governance_llm_provider / kb_governance_llm_base_url / kb_governance_llm_api_key / kb_governance_llm_model / kb_governance_temperature / kb_governance_llm_timeout_seconds | 知识治理 Agent 独立接入与模型参数（留空逐项继承全局，可只覆盖其中几项） |
| oncall_llm_provider / oncall_llm_base_url / oncall_llm_api_key / oncall_llm_model / oncall_temperature / oncall_llm_timeout_seconds | 值班 Agent 独立接入与模型参数（留空逐项继承全局，可只覆盖其中几项） |
| feature_flags_json | 功能开关（auto_diagnose/dedup_scan） |
| default_role | 未绑定用户默认角色 |
| role_bindings_json | 邮箱 → 角色绑定映射 |

- 修改即时生效并写 `config_update` 审计。三个 Agent 分组均提供「测试连通性」按钮（`POST /api/v1/console/configs/llm-test?agent=diagnose|kb_governance|oncall`），返回按继承规则解析后的实际模型/超时/接入方式与 `access_source`（agent=独立配置生效 / global=继承全局），API Key 永不回显。

## 8. 帮助中心

侧边栏「使用手册」内置本文摘要、常见问题 FAQ 与运维部署要点速览；完整文档见仓库根目录 `docs/`。
