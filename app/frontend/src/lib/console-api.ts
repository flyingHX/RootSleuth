/**
 * 控制台 API 封装层：统一通过 Atoms Web SDK 访问后端自定义 API。
 * 禁止 fetch / axios / 直连地址 —— 全部走 client.apiCall.invoke。
 */
import { client } from './api';
import type { ContentScanResult, QualityMetrics } from '@/components/console/shared';

export interface InvokeErrorShape {
  detail?: string;
  message?: string;
}

/** 从 SDK 抛出的错误中提取后端 detail 信息。 */
export function errDetail(e: unknown): string {
  const err = e as { data?: InvokeErrorShape; response?: { data?: InvokeErrorShape }; message?: string };
  return (
    err?.data?.detail ||
    err?.response?.data?.detail ||
    err?.data?.message ||
    err?.response?.data?.message ||
    err?.message ||
    '请求失败，请稍后重试'
  );
}

/** 从错误中提取内容安全拦截扫描结果（detail={error:'content_guard_blocked', scan}）。 */
export function extractContentGuardScan(e: unknown): ContentScanResult | null {
  const err = e as { data?: { detail?: unknown }; response?: { data?: { detail?: unknown } } };
  const detail = err?.data?.detail ?? err?.response?.data?.detail;
  if (
    detail &&
    typeof detail === 'object' &&
    (detail as { error?: string }).error === 'content_guard_blocked' &&
    (detail as { scan?: ContentScanResult }).scan
  ) {
    return (detail as { scan: ContentScanResult }).scan;
  }
  return null;
}

async function invoke<T>(url: string, method: 'GET' | 'POST' | 'PUT' = 'GET', data?: unknown): Promise<T> {
  const res = await client.apiCall.invoke({ url, method, data });
  return res.data as T;
}

// ---------------- 类型定义 ----------------

export interface Permissions {
  user: { id: string; email: string | null; name: string | null };
  role: string;
  role_label: string;
  level: number;
  can_diagnose: boolean;
  can_feedback: boolean;
  can_edit_kb: boolean;
  can_approve: boolean;
  can_publish: boolean;
  can_manage_rules: boolean;
  can_manage_config: boolean;
  can_manage_users: boolean;
  approval_mode: 'OFF' | 'SINGLE_REVIEW' | 'MULTI_LEVEL';
}

export interface DashboardData {
  metrics: {
    total_events: number;
    noise_reduction: number;
    unknown_rate: number;
    rag_success_rate: number;
    rag_p99_ms: number | null;
    avg_rag_ms: number | null;
  };
  by_severity: Record<string, number>;
  top_error_types: { name: string; count: number }[];
  top_services: { name: string; count: number }[];
  recent_events: {
    id: number;
    event_id: string;
    severity: string;
    service_name: string;
    error_type: string | null;
    status: string | null;
    created_at: string | null;
  }[];
  health: Record<string, { status: string; detail: string }>;
  kb: { total: number; archived: number; avg_feedback: number };
  /** 知识健康（总览聚合）：红黄绿分级摘要 */
  kb_health: {
    total: number;
    active: number;
    archived: number;
    green: number;
    yellow: number;
    red: number;
    health_rate: number | null;
  };
  todo: { pending_approvals: number; pending_unknowns: number };
  /** 质量态势（总览）：单次诊断质量聚合 + 生成/检索/内容安全统计 */
  quality: {
    sample_count: number;
    trust_index_avg: number | null;
    faithfulness_avg: number | null;
    context_coverage_avg: number | null;
    answer_relevance_avg: number | null;
    hallucination_rate_avg: number | null;
    quality_ok_rate: number | null;
    generation: { success: number; fail: number; success_rate: number | null };
    retrieval: { success_rate: number | null; p99_ms: number | null; avg_ms: number | null };
    content_safety: {
      scans: number;
      blocked: number;
      flagged: number;
      passed: number;
      overrides: number;
      last_rule_version: string | null;
    };
  };
}

export interface EventItem {
  id: number;
  event_id: string;
  severity: string;
  service_name: string;
  cluster: string | null;
  error_type: string | null;
  template: string | null;
  status: string | null;
  rag_status: string | null;
  rag_score: number | null;
  confidence: number | null;
  degraded_reason: string | null;
  created_at: string | null;
}

export interface EventDetail extends EventItem {
  raw_log: string | null;
  fingerprint: string | null;
  topology: string | null;
  rag_ms: number | null;
  std_ms: number | null;
  candidates: { case_id: string; error_type: string; service_name: string; score: number; root_cause: string | null; solution: string | null; feedback_score: number | null; embedding_score?: number | null }[] | null;
  ai_root_cause: string | null;
  ai_solution: string | null;
  ai_command: string | null;
  ai_output: { root_cause: string; solution: string; confidence: number; command: string; model?: string; quality?: QualityMetrics | null } | null;
}

export interface DiagnosisResult {
  status: string;
  message: string;
  event_id: number;
  rag: {
    status: string;
    score: number | null;
    ms: number;
    candidates: EventDetail['candidates'];
    embedding?: { applied?: boolean; boost_weight?: number; reranked?: boolean } | null;
    rerank?: { strategy: string; candidate_count: number } | null;
  };
  /** 单次诊断质量指标（Trust Index / Faithfulness 等） */
  quality?: QualityMetrics | null;
  /** 端到端耗时（毫秒） */
  elapsed_ms?: number;
  diagnosis: {
    root_cause: string;
    solution: string;
    confidence: number;
    command: string;
    model: string;
    low_confidence: boolean;
    threshold: number;
  } | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
}

export interface KbCase {
  id: number;
  case_id: string;
  error_type: string;
  service_name: string;
  cluster: string | null;
  alert_template: string | null;
  root_cause: string | null;
  solution: string | null;
  topology_snapshot: string | null;
  status: string | null;
  version: number | null;
  feedback_score: number | null;
  created_at: string | null;
  updated_at: string | null;
  /** 案例关联告警实例日志数量（案例库卡片展示） */
  related_event_count?: number;
}

export interface CaseVersion {
  id: number;
  case_id: string;
  version: number;
  snapshot: Record<string, unknown> | null;
  approval_id: number | null;
  created_by: string | null;
  created_at: string | null;
}

export interface ChangeSet {
  id: number;
  case_id: string;
  change_type: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  diff: Record<string, { before: unknown; after: unknown }> | null;
  reason: string | null;
  status: string;
  version: number | null;
  approval_request_id: number | null;
  created_by: string | null;
  created_at: string | null;
  /** 内容安全扫描结果（创建返回与审批内容附加，发布前预检留痕） */
  content_scan?: ContentScanResult | null;
  /** 仅 /approvals/{id}/content 附加：新建案例的案例库完整字段视图 */
  full_case?: KbCaseFullView | null;
  /** 仅 /approvals/{id}/content 附加：关联日志实例样本（证据） */
  related_events?: ApprovalEventSample[];
}

/** 审批内容中的日志实例样本（证据用途，不替代知识案例正文） */
export interface ApprovalEventSample {
  event_id: string;
  service_name: string;
  severity: string | null;
  status: string | null;
  error_type: string | null;
  template: string | null;
  raw_log: string | null;
  created_at: string | null;
}

/** 新建案例的案例库完整字段视图（未填写字段以 null 呈现并在 missing_fields 标注） */
export interface KbCaseFullView {
  case_id: string;
  error_type: string;
  service_name: string;
  cluster: string | null;
  alert_template: string | null;
  root_cause: string | null;
  solution: string | null;
  topology_snapshot: string | null;
  status: string | null;
  version: number | null;
  feedback_score: number | null;
  missing_fields?: string[];
}

/** 知识健康风险案例（P2-1 健康报表风险清单项） */
export interface KbHealthRiskCase {
  case_id: string;
  error_type: string;
  service_name: string;
  status: string;
  version: number | null;
  feedback_score: number | null;
  age_days: number | null;
  updated_at: string | null;
  health: 'green' | 'yellow' | 'red';
  reasons: string[];
  sync_verified: boolean;
  sync_dead: number;
  sync_pending: number;
  content_risk: string | null;
  rule_version: string | null;
}

/** 知识库红黄绿健康报表（P2-1）：分级统计 + 同步闭环 + 内容安全 + 老化聚合 */
export interface KbHealthReport {
  generated_at: string;
  expire_days: number;
  unconditional_expire_days: number;
  summary: {
    total: number;
    active: number;
    archived: number;
    green: number;
    yellow: number;
    red: number;
    health_rate: number | null;
  };
  sync: {
    verified_cases: number;
    unverified_cases: number;
    pending_tasks: number;
    dead_tasks: number;
  };
  content_safety: {
    scans: number;
    blocked: number;
    flagged: number;
    passed: number;
    overrides: number;
    last_rule_version: string | null;
  };
  aging: {
    expire_days: number;
    stale_candidates: number;
    avg_age_days: number | null;
    oldest: { case_id: string; age_days: number | null } | null;
  };
  risk_cases: KbHealthRiskCase[];
}

/** 生命周期巡检（老化 / 负反馈归档淘汰）返回体：dry_run=true 仅返回候选预览 */
export interface LifecyclePatrolResult {
  dry_run: boolean;
  expire_days: number;
  candidates: { case_id: string; feedback_score: number | null; updated_at: string | null }[];
  archived: string[];
}

/** 到期补偿任务重试返回体 */
export interface SyncRetryResult {
  executed: number;
  succeeded: number;
  dead_lettered: number;
  pending_remaining: number;
  dry_run: boolean;
}

export interface KbCaseDetail {
  case: KbCase;
  versions: CaseVersion[];
  change_sets: ChangeSet[];
  related_events?: EventSample[];
}

/** 案例关联告警实例日志样本（证据用途）。 */
export interface EventSample {
  event_id: string;
  service_name: string;
  severity: string;
  status: string | null;
  error_type: string | null;
  template: string | null;
  raw_log: string | null;
  created_at: string | null;
}

export interface ApprovalStep {
  id: number;
  step_no: number;
  approver_role: string;
  approver_role_label: string;
  approver: string | null;
  action: string;
  comment: string | null;
  acted_at: string | null;
}

export interface ApprovalRequest {
  id: number;
  applicant: string;
  applicant_role: string;
  applicant_role_label: string;
  biz_type: string;
  biz_id: string;
  title: string;
  reason: string | null;
  risk_level: string | null;
  status: string;
  current_step: number;
  total_steps: number;
  published_at: string | null;
  steps: ApprovalStep[];
  created_at: string | null;
}

/** 审批关联业务内容：kb_edit 返回 ChangeSet（含 diff，create 另附完整字段视图与日志样本）、merge 返回提案（含合并双方完整案例）、rule_promote 返回模板（含规则条目预览与日志样本）。 */
export type ApprovalBizContent = ChangeSet | MergeProposal | UnknownTemplate;

export interface ApprovalContentData {
  biz_type: string;
  biz_id: string;
  title: string;
  content: ApprovalBizContent | null;
}

export interface RuleVersion {
  id: number;
  version: number;
  content: string;
  status: string;
  change_note: string | null;
  created_by: string | null;
  created_at: string | null;
}

export interface RulesData {
  active: RuleVersion | null;
  versions: RuleVersion[];
  rule_count: number;
}

export interface UnknownTemplate {
  id: number;
  template: string;
  suggested_error_type: string | null;
  sample_count: number | null;
  last_seen_service: string | null;
  status: string;
  created_at: string | null;
  /** 仅 /approvals/{id}/content 附加：晋升后写入的规则条目预览 */
  proposed_rule_entry?: { id: string; error_type: string; keywords: string[]; score: number; severity: string };
  /** 仅 /approvals/{id}/content 附加：关联日志实例样本（证据） */
  related_events?: ApprovalEventSample[];
}

export interface AuditLog {
  id: number;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string | null;
}

export interface ConfigItem {
  key: string;
  value: string;
  description: string;
  is_default: boolean;
  /** 密钥类配置（API Key）：后端返回脱敏值，永不明文回显 */
  is_secret?: boolean;
}

/** LLM/Embedding 配置连通性自检结果 */
export interface LlmTestResult {
  chat: {
    ok: boolean;
    /** 连通性测试使用的 Agent 作用域（全局测试为 undefined） */
    agent?: string;
    model?: string;
    /** 按 Agent 继承规则解析后的实际超时（秒） */
    resolved_timeout_seconds?: number;
    /** 按 Agent 继承规则解析后的实际模型 */
    resolved_model?: string;
    /** 按 Agent 继承规则解析后的实际接入方式 */
    resolved_provider?: string;
    /** 按 Agent 继承规则解析后的实际 Base URL */
    resolved_base_url?: string;
    /** 接入来源：agent = Agent 独立配置生效 / global = 继承全局 */
    access_source?: 'agent' | 'global';
    latency_ms?: number;
    sample?: string;
    error?: string;
  };
  embedding: {
    enabled: boolean;
    ok?: boolean;
    model?: string;
    dims?: number | null;
    latency_ms?: number;
    error?: string;
    note?: string;
  };
}

export interface MergeGroup {
  error_type: string;
  service_name: string;
  case_ids: string[];
  cases: KbCase[];
  suggested_master: string;
  /** 组内两两模板相似度（case_id → case_id → [0,1]），仅返回 ≥80% 达标组 */
  similarities?: Record<string, Record<string, number>>;
  /** 组内最低两两相似度（0-1），用于展示达标情况 */
  min_pair_similarity?: number;
}

export interface MergeProposal {
  id: number;
  master_case_id: string;
  merged_case_ids: string[];
  merge_strategy: Record<string, unknown> | null;
  reason: string | null;
  status: string;
  approval_request_id: number | null;
  created_by: string | null;
  created_at: string | null;
  /** 仅 /approvals/{id}/content 附加：主案例完整内容 */
  master_case?: KbCase | null;
  /** 仅 /approvals/{id}/content 附加：被合并案例完整内容（不存在时以 missing 标注） */
  merged_cases?: (KbCase | { case_id: string; missing: boolean })[];
}

// ---------------- Agent（诊断 / 知识治理 / 值班） ----------------

export interface AgentTraceStep {
  iteration?: number;
  step?: string;
  thought?: string | null;
  tool?: string;
  args?: Record<string, unknown>;
  observation?: unknown;
  result?: unknown;
  clusters?: number;
  drafts?: number;
  submitted?: number;
  skipped?: number;
  status?: string;
  window?: string;
  events?: number;
  systems?: number;
  unmapped?: string[];
  error?: string;
  raw?: string;
}

export interface AgentConclusion {
  root_cause: string;
  solution: string;
  confidence: number;
  evidence_chain: string[];
  command: string;
  low_confidence?: boolean;
  threshold?: number;
}

export interface AgentDiagnoseResult {
  status: string;
  session_id: number;
  event_id: number;
  message: string;
  agent: {
    model: string;
    iterations: number;
    duration_ms: number;
    tool_trace: AgentTraceStep[];
    conclusion: AgentConclusion;
    /** 单次深度诊断质量指标 */
    quality?: QualityMetrics | null;
    /** 本次会话 RAG 知识库召回统计 */
    rag?: { case_count?: number; kb_search_used?: boolean } | null;
    /** 确定性信息（波动治理）：采样温度、输入/上下文指纹与固定评估上下文构成 */
    stability?: {
      temperature?: number;
      input_fingerprint?: string;
      context_fingerprint?: string;
      eval_context?: { local_count?: number; rag_count?: number; merged_count?: number };
    } | null;
    /** 本次会话 Token 用量汇总 */
    usage?: Record<string, unknown> | null;
  } | null;
  fallback?: unknown;
}

export interface AgentCluster {
  template: string;
  count: number;
  services: string[];
  clusters: string[];
  severity_dist: Record<string, number>;
  max_severity: string;
  known_error_types: string[];
  last_seen: string | null;
  sample_raw_log: string | null;
}

export interface AgentDraftOutcome {
  case_id?: string;
  approval_request_id?: number;
  auto_published?: boolean;
  alert_template?: string;
  reason?: string;
  /** 该条草稿的生成质量评估（Trust Index 口径，与诊断一致） */
  quality?: QualityMetrics | null;
}

export interface AgentKbGovernanceResult {
  status: string;
  session_id: number;
  message: string;
  governance: {
    time_window?: string;
    analysis: string;
    clusters: AgentCluster[];
    drafts_submitted: AgentDraftOutcome[];
    drafts_skipped: AgentDraftOutcome[];
    merge_result: Record<string, unknown>;
    /** 本次起草案例的质量聚合（样本均值 + 达标率；无样本为 null） */
    quality?: QualityMetrics | null;
    model: string;
    duration_ms: number;
  };
}

export interface AgentAffectedSystem {
  system: string;
  event_count: number;
  max_severity: string;
  owners: string[];
  clusters: string[];
  services: { service: string; count: number; max_severity: string }[];
}

export interface AgentOncallReportResult {
  status: string;
  session_id: number;
  message: string;
  report: {
    id: number;
    time_window: string;
    event_count: number;
    by_severity: Record<string, number>;
    affected_systems: AgentAffectedSystem[];
    unmapped_services: Record<string, number>;
    impact_summary: string;
    priority: string;
    actions: string[];
    owners_to_notify: string[];
    chatops_text: string;
    /** 报告生成质量评估（Trust Index 口径；无告警窗口为 null） */
    quality?: QualityMetrics | null;
  };
}

export interface AgentSession {
  id: number;
  session_type: string;
  event_id: number | null;
  status: string;
  model: string;
  iterations: number | null;
  duration_ms: number | null;
  tool_trace: AgentTraceStep[] | null;
  result: unknown;
  error_message: string | null;
  actor: string | null;
  summary: string | null;
  created_at: string | null;
}

export interface CmdbAsset {
  id: number;
  hostname: string;
  ip: string;
  system_name: string;
  service_name: string;
  cluster: string | null;
  environment: string | null;
  owner: string | null;
  owner_email: string | null;
  dependencies: string[];
  log_path: string | null;
  status: string | null;
  description: string | null;
}

export interface OncallReportRecord {
  id: number;
  time_window: string;
  event_count: number;
  critical_count: number;
  warning_count: number;
  info_count: number;
  affected_systems: AgentAffectedSystem[];
  report: { impact_summary: string; priority: string; actions: string[]; owners_to_notify: string[]; chatops_text: string; quality?: QualityMetrics | null } | null;
  chatops_text: string | null;
  session_id: number | null;
  actor: string | null;
  created_at: string | null;
}

// ---------------- 用户管理（仅系统管理员） ----------------

export interface UserAdminItem {
  id: string;
  email: string;
  name: string | null;
  role: string;
  role_label: string;
  status: string;
  created_at: string | null;
  last_login: string | null;
}

// ---------------- API 函数 ----------------

export const consoleApi = {
  getPermissions: () => invoke<Permissions>('/api/v1/console/permissions'),
  getDashboard: () => invoke<DashboardData>('/api/v1/console/dashboard'),
  getKbHealth: () => invoke<KbHealthReport>('/api/v1/console/kb/health'),

  listEvents: (params: Record<string, string | number>) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString();
    return invoke<Paged<EventItem>>(`/api/v1/console/events?${qs}`);
  },
  getEvent: (id: number) => invoke<EventDetail>(`/api/v1/console/events/${id}`),
  diagnose: (id: number) =>
    invoke<DiagnosisResult>(`/api/v1/console/events/${id}/diagnose`, 'POST', {}),
  feedback: (id: number, body: { rating: string; correction?: Record<string, string>; comment?: string }) =>
    invoke<{ case_id: string; feedback_score: number; rating: string; correction_change_set: unknown }>(
      `/api/v1/console/events/${id}/feedback`, 'POST', body,
    ),

  listKbCases: (params: Record<string, string | number>) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString();
    return invoke<Paged<KbCase>>(`/api/v1/console/kb/cases?${qs}`);
  },
  getKbCase: (caseId: string) => invoke<KbCaseDetail>(`/api/v1/console/kb/cases/${encodeURIComponent(caseId)}`),
  createChangeSet: (body: {
    case_id: string;
    change_type: string;
    fields: Record<string, string>;
    reason: string;
    /** 高危内容误报放行：需同时填写 override_reason（写入审计） */
    allow_override?: boolean;
    override_reason?: string;
  }) =>
    invoke<{
      change_set: ChangeSet;
      approval_mode: string;
      auto_published: boolean;
      approval_request_id?: number;
      content_scan?: ContentScanResult | null;
    }>(
      '/api/v1/console/kb/change-sets', 'POST', body,
    ),
  rollbackCase: (caseId: string, version: number) =>
    invoke<KbCase>(`/api/v1/console/kb/cases/${encodeURIComponent(caseId)}/rollback`, 'POST', { version }),
  /** 归档知识案例：业务库归档 + 审计 + RAG 检索侧索引删除（kb_admin 及以上） */
  archiveCase: (caseId: string) =>
    invoke<KbCase>(`/api/v1/console/kb/cases/${encodeURIComponent(caseId)}/archive`, 'POST', {}),
  /** 生命周期巡检：dryRun=true 仅预览归档候选，false 执行批量归档（kb_admin 及以上） */
  lifecyclePatrol: (dryRun: boolean) =>
    invoke<LifecyclePatrolResult>('/api/v1/console/kb/lifecycle-patrol', 'POST', { dry_run: dryRun }),
  /** 手动触发到期补偿任务重试（kb_admin 及以上） */
  retryDueTasks: (limit = 20) =>
    invoke<SyncRetryResult>(`/api/v1/console/rag-sync/retry-due?limit=${limit}`, 'POST', {}),
  /** 批量重放死信任务：重置计数后重新排队（kb_admin 及以上） */
  replayDeadTasks: () =>
    invoke<{ requeued: number; task_ids: number[] }>('/api/v1/console/rag-sync/replay-dead', 'POST', {}),
  previewKbRelatedEvents: (template?: string, service?: string) => {
    const qs = new URLSearchParams();
    if (template?.trim()) qs.set('template', template.trim());
    if (service?.trim()) qs.set('service', service.trim());
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return invoke<{ items: EventSample[] }>(`/api/v1/console/kb/related-events${suffix}`);
  },
  scanDuplicates: () => invoke<{ groups: MergeGroup[] }>('/api/v1/console/kb/duplicates'),
  listMergeProposals: () => invoke<Paged<MergeProposal>>('/api/v1/console/kb/merge-proposals'),
  createMergeProposal: (body: { master_case_id: string; merged_case_ids: string[]; reason: string }) =>
    invoke<{ proposal: MergeProposal; auto_merged: boolean; approval_request_id?: number }>(
      '/api/v1/console/kb/merge-proposals', 'POST', body,
    ),
  listChangeSets: (status?: string) =>
    invoke<Paged<ChangeSet>>(`/api/v1/console/change-sets${status ? `?status=${status}` : ''}`),

  listApprovals: (box: string) =>
    invoke<{ items: ApprovalRequest[]; role: string }>(`/api/v1/console/approvals?box=${box}`),
  decideApproval: (id: number, action: string, comment: string) =>
    invoke<{ status: string }>(`/api/v1/console/approvals/${id}/decide`, 'POST', { action, comment }),
  getApprovalContent: (id: number) =>
    invoke<ApprovalContentData>(`/api/v1/console/approvals/${id}/content`),

  getRules: () => invoke<RulesData>('/api/v1/console/rules'),
  validateRules: (content: string) =>
    invoke<{ valid: boolean; rule_count: number; rule_ids: string[] }>('/api/v1/console/rules/validate', 'POST', { content }),
  publishRules: (content: string, change_note: string) =>
    invoke<RuleVersion>('/api/v1/console/rules/publish', 'POST', { content, change_note }),
  rollbackRules: (versionId: number) =>
    invoke<RuleVersion>(`/api/v1/console/rules/${versionId}/rollback`, 'POST', {}),

  listUnknownTemplates: (status?: string) =>
    invoke<{ items: UnknownTemplate[] }>(`/api/v1/console/unknown-templates${status ? `?status=${status}` : ''}`),
  promoteTemplate: (id: number, errorType: string) =>
    invoke<{ template: UnknownTemplate; approval_request_id: number }>(
      `/api/v1/console/unknown-templates/${id}/promote`, 'POST', { error_type: errorType },
    ),
  discardTemplate: (id: number) =>
    invoke<UnknownTemplate>(`/api/v1/console/unknown-templates/${id}/discard`, 'POST', {}),

  listAuditLogs: (params: Record<string, string | number>) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString();
    return invoke<Paged<AuditLog>>(`/api/v1/console/audit-logs?${qs}`);
  },

  listConfigs: () => invoke<{ items: ConfigItem[] }>('/api/v1/console/configs'),
  updateConfig: (key: string, value: string) =>
    invoke<{ key: string; value: string }>('/api/v1/console/configs', 'PUT', { key, value }),
  testLlmConfig: (agent?: string) =>
    invoke<LlmTestResult>(
      agent
        ? `/api/v1/console/configs/llm-test?agent=${encodeURIComponent(agent)}`
        : '/api/v1/console/configs/llm-test',
      'POST',
      {},
    ),

  // Agent：诊断 / 知识治理 / 值班
  agentDiagnose: (eventId: number) =>
    invoke<AgentDiagnoseResult>('/api/v1/console/agent/diagnose', 'POST', { event_id: eventId }),
  agentKbGovernance: (timeWindow: string) =>
    invoke<AgentKbGovernanceResult>('/api/v1/console/agent/kb-governance', 'POST', { time_window: timeWindow }),
  agentOncallReport: (timeWindow: string) =>
    invoke<AgentOncallReportResult>('/api/v1/console/agent/oncall-report', 'POST', { time_window: timeWindow }),
  listAgentSessions: (params?: { session_type?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.session_type) qs.set('session_type', params.session_type);
    if (params?.limit) qs.set('limit', String(params.limit));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return invoke<{ items: AgentSession[] }>(`/api/v1/console/agent/sessions${suffix}`);
  },
  listCmdbAssets: (q?: string) =>
    invoke<{ items: CmdbAsset[] }>(`/api/v1/console/agent/cmdb${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  listOncallReports: () => invoke<{ items: OncallReportRecord[] }>('/api/v1/console/agent/oncall-reports'),

  // 用户管理（仅系统管理员）
  listUsers: (params?: { q?: string; status?: string; skip?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.q) qs.set('q', params.q);
    if (params?.status) qs.set('status', params.status);
    if (params?.skip) qs.set('skip', String(params.skip));
    if (params?.limit) qs.set('limit', String(params.limit));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return invoke<Paged<UserAdminItem>>(`/api/v1/users${suffix}`);
  },
  createUser: (body: { email: string; name?: string; role: string; status?: string }) =>
    invoke<UserAdminItem>('/api/v1/users', 'POST', body),
  updateUser: (userId: string, body: { name?: string; role?: string; status?: string }) =>
    invoke<UserAdminItem>(`/api/v1/users/${encodeURIComponent(userId)}`, 'PUT', body),
};
