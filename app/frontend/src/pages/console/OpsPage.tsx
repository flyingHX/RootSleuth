/** C10 审计日志 + 配置中心：审计链路检索、before/after 快照展示、配置项编辑（含 LLM/Embedding 分组管理与密钥脱敏）。 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, KeyRound, Save } from 'lucide-react';
import { toast } from 'sonner';
import {
  consoleApi,
  errDetail,
  type AuditLog,
  type ConfigItem,
  type KbHealthReport,
  type LlmTestResult,
} from '@/lib/console-api';
import { EmptyBlock, JsonPre, LoadingBlock, StateGate, fmtPercent, fmtTime } from '@/components/console/shared';
import { usePermissions } from '@/components/console/ConsoleLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';

const ACTION_LABELS: Record<string, string> = {
  feedback: '人工反馈',
  diagnosis_run: 'AI 诊断',
  kb_edit_submit: '知识库变更提交',
  kb_publish: '知识库发布',
  kb_rollback: '知识库回滚',
  merge_proposal_create: '合并提案创建',
  merge_apply: '合并执行',
  approval_approve: '审批通过',
  approval_reject: '审批拒绝',
  approval_withdraw: '审批撤回',
  rule_publish: '规则发布',
  rule_rollback: '规则回滚',
  unknown_promote_request: '未知模板晋升',
  unknown_discard: '未知模板废弃',
  config_update: '配置更新',
  llm_config_test: 'LLM 配置连通性测试',
};

const TARGET_LABELS: Record<string, string> = {
  event: '告警事件',
  kb_case: '知识案例',
  kb_change_set: '知识变更集',
  kb_merge_proposal: '合并提案',
  approval_request: '审批单',
  rule_version: '规则版本',
  unknown_template: '未知模板',
  console_config: '控制台配置',
};

function AuditTab() {
  const [action, setAction] = useState('');
  const [actor, setActor] = useState('');
  const [targetType, setTargetType] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const query = useQuery({
    queryKey: ['audit-logs', action, actor, targetType],
    queryFn: () =>
      consoleApi.listAuditLogs({
        limit: 100,
        ...(action.trim() ? { action: action.trim() } : {}),
        ...(actor.trim() ? { actor: actor.trim() } : {}),
        ...(targetType.trim() ? { target_type: targetType.trim() } : {}),
      }),
  });

  const items = query.data?.items ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <Input className="h-9 w-44 text-xs" placeholder="操作（如 kb_publish）" value={action} onChange={(e) => setAction(e.target.value)} />
        <Input className="h-9 w-44 text-xs" placeholder="操作人（邮箱）" value={actor} onChange={(e) => setActor(e.target.value)} />
        <Input className="h-9 w-44 text-xs" placeholder="对象类型（如 kb_case）" value={targetType} onChange={(e) => setTargetType(e.target.value)} />
        <span className="text-xs text-muted-foreground">共 {query.data?.total ?? 0} 条审计记录</span>
      </div>

      <StateGate
        loading={query.isLoading}
        error={query.isError ? errDetail(query.error) : null}
        onRetry={() => query.refetch()}
        isEmpty={items.length === 0}
        empty="没有符合条件的审计记录"
        emptyHint="尝试清空筛选条件"
      >
        <Card>
          <CardContent className="p-0">
            <ul className="divide-y">
              {items.map((log: AuditLog) => {
                const open = expandedId === log.id;
                return (
                  <li key={log.id} className="px-4 py-3">
                    <button
                      className="flex w-full flex-wrap items-center gap-2 text-left text-xs"
                      onClick={() => setExpandedId(open ? null : log.id)}
                    >
                      <Badge variant="secondary">{ACTION_LABELS[log.action] ?? log.action}</Badge>
                      <span className="font-medium">{log.actor}</span>
                      <span className="text-muted-foreground">
                        {TARGET_LABELS[log.target_type] ?? log.target_type} · {log.target_id}
                      </span>
                      <span className="ml-auto text-muted-foreground">{fmtTime(log.created_at)}</span>
                    </button>
                    {open && (
                      <div className="mt-2.5 grid gap-2.5 lg:grid-cols-2">
                        <div>
                          <p className="mb-1 text-xs font-medium text-muted-foreground">变更前快照</p>
                          {log.before ? <JsonPre data={log.before} /> : <p className="text-xs text-muted-foreground">（无）</p>}
                        </div>
                        <div>
                          <p className="mb-1 text-xs font-medium text-muted-foreground">变更后快照</p>
                          {log.after ? <JsonPre data={log.after} /> : <p className="text-xs text-muted-foreground">（无）</p>}
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>
      </StateGate>
    </div>
  );
}

// ---------------- 知识健康报表（P2-1：红黄绿分级 + 同步闭环 + 内容安全 + 老化） ----------------

const HEALTH_TONE: Record<'green' | 'yellow' | 'red', { label: string; badge: string }> = {
  green: { label: '健康', badge: 'border-teal-500/30 bg-teal-500/15 text-teal-700 dark:text-teal-400' },
  yellow: { label: '关注', badge: 'border-amber-500/30 bg-amber-500/15 text-amber-700 dark:text-amber-400' },
  red: { label: '风险', badge: 'border-red-500/30 bg-red-500/15 text-red-700 dark:text-red-400' },
};

function HealthBadge({ level }: { level: 'green' | 'yellow' | 'red' }) {
  const tone = HEALTH_TONE[level] ?? HEALTH_TONE.green;
  return (
    <Badge variant="outline" className={cn('text-[10px]', tone.badge)}>
      {tone.label}
    </Badge>
  );
}

function StatItem({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="text-center">
      <p className={cn('text-xl font-semibold', tone)}>{value}</p>
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

/** 诊断质量态势（Ops 治理视图）：复用 Dashboard 聚合（同查询缓存），展示 Trust Index 与生成/检索成功率。 */
function QualityOverviewCard() {
  const { data } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => consoleApi.getDashboard(),
    staleTime: 30_000,
  });
  const q = data?.quality;
  const fmt = (v: number | null | undefined, f: (n: number) => string) => (typeof v === 'number' ? f(v) : '—');
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">诊断质量态势</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatItem label="Trust Index" value={fmt(q?.trust_index_avg, (n) => n.toFixed(3))} />
        <StatItem label="质量达标率" value={fmt(q?.quality_ok_rate, fmtPercent)} />
        <StatItem label="检索成功率" value={fmt(q?.retrieval?.success_rate, fmtPercent)} />
        <StatItem label="生成成功率" value={fmt(q?.generation?.success_rate, fmtPercent)} />
      </CardContent>
    </Card>
  );
}

function KbHealthTab() {
  const query = useQuery({
    queryKey: ['kb-health'],
    queryFn: () => consoleApi.getKbHealth(),
    refetchInterval: 60_000,
  });

  if (query.isLoading) return <LoadingBlock rows={6} />;
  if (query.isError || !query.data) {
    return <EmptyBlock title="健康报表加载失败" hint={errDetail(query.error)} />;
  }

  const report: KbHealthReport = query.data;
  const { summary, sync, content_safety, aging } = report;

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        红黄绿健康度口径：绿=verify 确认且无风险；黄=同步未确认 / 超老化阈值 / 负反馈 / PII 标记放行；
        红=同步死信 / 高风险内容（含误报放行留痕）/ 反馈分 ≤ -2 / 超无条件老化阈值。
      </p>
      <QualityOverviewCard />
      <div className="grid gap-4 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">红黄绿分级</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <div className="grid grid-cols-3 gap-2">
              <StatItem label="健康" value={summary.green} tone="text-teal-600 dark:text-teal-400" />
              <StatItem label="关注" value={summary.yellow} tone="text-amber-600 dark:text-amber-400" />
              <StatItem label="风险" value={summary.red} tone="text-red-600 dark:text-red-400" />
            </div>
            <div className="flex h-2 overflow-hidden rounded-full bg-muted">
              {summary.total > 0 && (
                <>
                  <span className="bg-teal-500" style={{ width: `${(summary.green / summary.total) * 100}%` }} />
                  <span className="bg-amber-500" style={{ width: `${(summary.yellow / summary.total) * 100}%` }} />
                  <span className="bg-red-500" style={{ width: `${(summary.red / summary.total) * 100}%` }} />
                </>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              健康率 {summary.health_rate !== null ? fmtPercent(summary.health_rate) : '—'} · 案例 {summary.total} 个
              （active {summary.active} / archived {summary.archived}）
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">同步闭环</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <div className="grid grid-cols-2 gap-2">
              <StatItem label="已确认" value={sync.verified_cases} tone="text-teal-600 dark:text-teal-400" />
              <StatItem label="未确认" value={sync.unverified_cases} tone="text-amber-600 dark:text-amber-400" />
              <StatItem label="待重试任务" value={sync.pending_tasks} />
              <StatItem
                label="死信任务"
                value={sync.dead_tasks}
                tone={sync.dead_tasks > 0 ? 'text-red-600 dark:text-red-400' : undefined}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              发布后 upsert+verify 闭环状态；死信表示索引可能缺失对应知识，需人工重放。
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">内容安全</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <div className="grid grid-cols-2 gap-2">
              <StatItem label="扫描次数" value={content_safety.scans} />
              <StatItem
                label="拦截"
                value={content_safety.blocked}
                tone={content_safety.blocked > 0 ? 'text-red-600 dark:text-red-400' : undefined}
              />
              <StatItem label="标记放行" value={content_safety.flagged} />
              <StatItem label="误报申诉" value={content_safety.overrides} />
            </div>
            <p className="text-xs text-muted-foreground">
              规则版本 {content_safety.last_rule_version ?? '—'}；高风险命中默认拦截，人工申诉后留痕放行。
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">老化治理</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <div className="grid grid-cols-2 gap-2">
              <StatItem label="待老化案例" value={aging.stale_candidates} />
              <StatItem label="平均年龄" value={aging.avg_age_days !== null ? `${aging.avg_age_days} 天` : '—'} />
            </div>
            <p className="text-xs text-muted-foreground">
              阈值 {report.expire_days} 天（无条件 {report.unconditional_expire_days} 天）
              {aging.oldest?.age_days !== null && aging.oldest
                ? `；最老案例 ${aging.oldest.case_id}（${aging.oldest.age_days} 天）`
                : ''}
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">风险案例清单（红 / 黄）</CardTitle>
        </CardHeader>
        <CardContent>
          {report.risk_cases.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              全部知识案例健康，无红 / 黄分级案例。
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[880px] text-left text-xs">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="py-2 pr-3 font-medium">健康度</th>
                    <th className="py-2 pr-3 font-medium">案例</th>
                    <th className="py-2 pr-3 font-medium">服务 / 错误类型</th>
                    <th className="py-2 pr-3 font-medium">状态 / 版本</th>
                    <th className="py-2 pr-3 font-medium">反馈分</th>
                    <th className="py-2 pr-3 font-medium">年龄</th>
                    <th className="py-2 pr-3 font-medium">同步</th>
                    <th className="py-2 pr-3 font-medium">内容风险</th>
                    <th className="py-2 font-medium">命中原因</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {report.risk_cases.map((c) => (
                    <tr key={c.case_id} className="align-top">
                      <td className="py-2.5 pr-3">
                        <HealthBadge level={c.health} />
                      </td>
                      <td className="py-2.5 pr-3 font-mono">{c.case_id}</td>
                      <td className="py-2.5 pr-3">
                        {c.service_name}
                        <span className="block text-muted-foreground">{c.error_type}</span>
                      </td>
                      <td className="py-2.5 pr-3">
                        {c.status} <span className="text-muted-foreground">v{c.version ?? 0}</span>
                      </td>
                      <td className="py-2.5 pr-3">{c.feedback_score ?? '—'}</td>
                      <td className="py-2.5 pr-3">{c.age_days !== null ? `${c.age_days} 天` : '—'}</td>
                      <td className="py-2.5 pr-3">
                        {c.sync_dead > 0 ? (
                          <span className="text-red-600 dark:text-red-400">死信 {c.sync_dead}</span>
                        ) : c.sync_pending > 0 ? (
                          <span className="text-amber-600 dark:text-amber-400">待重试 {c.sync_pending}</span>
                        ) : c.sync_verified ? (
                          <span className="text-teal-600 dark:text-teal-400">已确认</span>
                        ) : (
                          <span className="text-amber-600 dark:text-amber-400">未确认</span>
                        )}
                      </td>
                      <td className="py-2.5 pr-3">
                        {c.content_risk && c.content_risk !== 'none' ? (
                          <Badge variant="outline" className="text-[10px]">
                            {c.content_risk}
                            {c.rule_version ? ` · ${c.rule_version}` : ''}
                          </Badge>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="py-2.5">
                        <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
                          {c.reasons.map((r) => (
                            <li key={r}>{r}</li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------- 配置中心（分组：LLM / Embedding / 策略 / 系统） ----------------

/** 密钥类配置：后端仅返回脱敏值，保存空串表示清除 */
const SECRET_KEYS = new Set(['llm_api_key', 'embedding_api_key']);

const SELECT_OPTIONS: Record<string, { value: string; label: string }[]> = {
  llm_provider: [
    { value: 'atoms_hub', label: '平台内置 AIHub' },
    { value: 'openai_compatible', label: '自建 OpenAI 兼容接口' },
  ],
  approval_mode: [
    { value: 'OFF', label: '免审直发（OFF）' },
    { value: 'SINGLE_REVIEW', label: '单级审批' },
    { value: 'MULTI_LEVEL', label: '多级审批' },
  ],
  default_role: [
    { value: 'viewer', label: 'viewer（只读）' },
    { value: 'operator', label: 'operator（运营）' },
    { value: 'sre', label: 'sre（值班）' },
    { value: 'approver', label: 'approver（审批）' },
    { value: 'kb_admin', label: 'kb_admin（知识库管理员）' },
  ],
};

const LABELS: Record<string, string> = {
  llm_provider: 'LLM 接入方式',
  llm_base_url: 'LLM Base URL',
  llm_api_key: 'LLM API Key',
  llm_model: 'Chat 模型',
  llm_temperature: '采样温度',
  llm_timeout_seconds: '超时时间（秒）',
  embedding_base_url: 'Embedding Base URL',
  embedding_api_key: 'Embedding API Key',
  embedding_model: 'Embedding 模型',
  approval_mode: '审批模式',
  confidence_threshold: '置信度阈值',
  rerank_weight_json: '重排权重 JSON',
  feature_flags_json: '功能开关 JSON',
  default_role: '默认角色',
  role_bindings_json: '角色绑定 JSON',
};

const CONFIG_GROUPS: { title: string; hint: string; keys: string[]; testable?: boolean }[] = [
  {
    title: 'LLM 模型接入',
    hint: '诊断与三类 Agent（自研 ReAct）共用的 Chat 模型；切换为自建接口需填写 Base URL 与 API Key，保存后立即生效，无需重启。',
    keys: ['llm_provider', 'llm_base_url', 'llm_api_key', 'llm_model', 'llm_temperature', 'llm_timeout_seconds'],
    testable: true,
  },
  {
    title: 'Embedding 语义检索',
    hint: '填写 Embedding 模型名后，诊断 RAG 将启用语义向量加分重排；Base URL / API Key 缺省回退 LLM 配置，调用失败自动降级。',
    keys: ['embedding_base_url', 'embedding_api_key', 'embedding_model'],
  },
  {
    title: '诊断与审批策略',
    hint: '置信度低于阈值的诊断会标记低置信；重排权重 JSON 控制 RAG 案例召回排序（cosine/topology/time_decay/feedback）。',
    keys: ['approval_mode', 'confidence_threshold', 'rerank_weight_json'],
  },
  {
    title: '系统配置',
    hint: '角色绑定 JSON 为 email → role 映射；功能开关控制自动诊断与去重扫描。',
    keys: ['feature_flags_json', 'default_role', 'role_bindings_json'],
  },
];

function TestResultBox({ result }: { result: LlmTestResult }) {
  return (
    <div className="grid gap-2.5 rounded-md border bg-muted/40 p-3 text-xs lg:grid-cols-2">
      <div>
        <p className="flex flex-wrap items-center gap-1.5 font-medium">
          <Badge variant={result.chat.ok ? 'default' : 'destructive'} className="text-[10px]">
            {result.chat.ok ? 'Chat 连通正常' : 'Chat 连通失败'}
          </Badge>
          {result.chat.model && <span className="font-mono text-muted-foreground">{result.chat.model}</span>}
          {typeof result.chat.latency_ms === 'number' && <span className="text-muted-foreground">{result.chat.latency_ms}ms</span>}
        </p>
        {result.chat.ok ? (
          result.chat.sample && <p className="mt-1 text-muted-foreground">回复样例：{result.chat.sample}</p>
        ) : (
          <p className="mt-1 break-all text-destructive">{result.chat.error}</p>
        )}
      </div>
      <div>
        <p className="flex flex-wrap items-center gap-1.5 font-medium">
          <Badge
            variant={!result.embedding.enabled ? 'outline' : result.embedding.ok ? 'default' : 'destructive'}
            className="text-[10px]"
          >
            {!result.embedding.enabled ? 'Embedding 未启用' : result.embedding.ok ? 'Embedding 连通正常' : 'Embedding 连通失败'}
          </Badge>
          {result.embedding.model && <span className="font-mono text-muted-foreground">{result.embedding.model}</span>}
          {typeof result.embedding.dims === 'number' && <span className="text-muted-foreground">{result.embedding.dims} 维</span>}
          {result.embedding.enabled && typeof result.embedding.latency_ms === 'number' && (
            <span className="text-muted-foreground">{result.embedding.latency_ms}ms</span>
          )}
        </p>
        <p className="mt-1 break-all text-muted-foreground">
          {result.embedding.ok ? '语义加分重排已启用' : result.embedding.error || result.embedding.note}
        </p>
      </div>
    </div>
  );
}

function ConfigTab() {
  const perms = usePermissions();
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<LlmTestResult | null>(null);
  const canManage = !!perms?.can_manage_config;
  // 权限门控：非系统管理员不发起 /configs 请求，避免无意义的 403 报错
  const query = useQuery({
    queryKey: ['configs'],
    queryFn: () => consoleApi.listConfigs(),
    enabled: canManage,
  });

  const updateMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) => consoleApi.updateConfig(key, value),
    onSuccess: (res) => {
      toast.success(`配置 ${res.key} 已保存并写入审计`);
      setDrafts((s) => {
        const next = { ...s };
        delete next[res.key];
        return next;
      });
      queryClient.invalidateQueries({ queryKey: ['configs'] });
      queryClient.invalidateQueries({ queryKey: ['permissions'] });
    },
    onError: (e) => toast.error(`保存失败：${errDetail(e)}`),
  });

  const testMutation = useMutation({
    mutationFn: () => consoleApi.testLlmConfig(),
    onSuccess: (res: LlmTestResult) => {
      setTestResult(res);
      if (res.chat.ok) toast.success(`Chat 连通正常（${res.chat.model ?? '-'}）`);
      else toast.error(`Chat 连通失败：${res.chat.error ?? '未知错误'}`);
    },
    onError: (e) => toast.error(`测试失败：${errDetail(e)}`),
  });

  if (!canManage) {
    return (
      <EmptyBlock
        title="配置中心仅对系统管理员开放"
        hint={`当前角色「${perms?.role_label ?? '未知'}」无配置管理权限；请使用系统管理员账号（如演示账号 demo-admin@atoms.dev）登录后操作。`}
      />
    );
  }
  if (query.isLoading) return <LoadingBlock rows={5} />;
  if (query.isError) {
    return (
      <EmptyBlock
        title="配置加载失败"
        hint={errDetail(query.error)}
      />
    );
  }

  const items = query.data?.items ?? [];
  const byKey = new Map(items.map((cfg: ConfigItem) => [cfg.key, cfg]));

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        模型接入、诊断策略与审批模式等全局配置；修改后立即生效并写入审计日志。
        API Key 加密存储、脱敏展示（永不明文回显），输入新值替换、留空保存即清除。
        {!canManage && ` 当前角色（${perms?.role_label}）为只读。`}
      </p>
      {CONFIG_GROUPS.map((group) => (
        <Card key={group.title}>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              {group.title}
              {group.testable && (
                <Button
                  size="sm"
                  variant="outline"
                  className="ml-auto h-7 px-2.5 text-xs"
                  disabled={!canManage || testMutation.isPending}
                  onClick={() => testMutation.mutate()}
                >
                  <Activity className="mr-1 h-3.5 w-3.5" />
                  {testMutation.isPending ? '测试中…' : '测试连通性'}
                </Button>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">{group.hint}</p>
            {group.testable && testResult && <TestResultBox result={testResult} />}
            <div className="divide-y rounded-md border">
              {group.keys.map((key) => {
                const cfg = byKey.get(key);
                if (!cfg) return null;
                const isSecret = SECRET_KEYS.has(key);
                const draft = drafts[key] ?? (isSecret ? '' : cfg.value);
                const changed = isSecret ? draft.trim().length > 0 : draft !== cfg.value;
                const options = SELECT_OPTIONS[key];
                return (
                  <div key={key} className="flex flex-col gap-1.5 px-3 py-2.5 sm:flex-row sm:items-center sm:gap-3">
                    <div className="sm:w-60 sm:shrink-0">
                      <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium">
                        {isSecret && <KeyRound className="h-3 w-3 text-muted-foreground" />}
                        {LABELS[key] ?? key}
                        {cfg.is_default && <Badge variant="outline" className="text-[10px]">默认值</Badge>}
                      </p>
                      <p className="font-mono text-[10px] text-muted-foreground">{key}</p>
                      <p className="text-[11px] leading-snug text-muted-foreground">{cfg.description}</p>
                    </div>
                    <div className="flex flex-1 items-center gap-2">
                      {options ? (
                        <Select
                          value={draft}
                          disabled={!canManage || updateMutation.isPending}
                          onValueChange={(v) => setDrafts((s) => ({ ...s, [key]: v }))}
                        >
                          <SelectTrigger className={cn('h-9 flex-1 text-xs', changed && 'border-primary')}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {options.map((o) => (
                              <SelectItem key={o.value} value={o.value} className="text-xs">
                                {o.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Input
                          type={isSecret ? 'password' : 'text'}
                          className={cn('h-9 font-mono text-xs', changed && 'border-primary')}
                          value={draft}
                          placeholder={
                            isSecret
                              ? cfg.value
                                ? `${cfg.value}（已配置，输入新值替换）`
                                : '未配置'
                              : ''
                          }
                          autoComplete="off"
                          disabled={!canManage || updateMutation.isPending}
                          onChange={(e) => setDrafts((s) => ({ ...s, [key]: e.target.value }))}
                        />
                      )}
                      {isSecret && cfg.value && (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-9 shrink-0 text-xs text-muted-foreground"
                          disabled={!canManage || updateMutation.isPending}
                          onClick={() => {
                            if (window.confirm(`确定清除 ${LABELS[key] ?? key}？清除后相关能力回退默认配置。`)) {
                              updateMutation.mutate({ key, value: '' });
                            }
                          }}
                        >
                          清除
                        </Button>
                      )}
                      <Button
                        size="sm"
                        className="shrink-0"
                        disabled={!canManage || !changed || updateMutation.isPending}
                        onClick={() => updateMutation.mutate({ key, value: draft })}
                      >
                        <Save className="mr-1.5 h-3.5 w-3.5" />
                        {updateMutation.isPending ? '保存中…' : '保存'}
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export default function OpsPage() {
  const perms = usePermissions();
  const canManageConfig = !!perms?.can_manage_config;
  // 审计与配置整页仅系统管理员可见（侧边栏入口同步隐藏，防止直达 URL 访问）
  if (!canManageConfig) {
    return (
      <div className="space-y-4">
        <h1 className="text-lg font-semibold tracking-tight">审计与配置</h1>
        <EmptyBlock
          title="仅系统管理员可访问"
          hint={`当前角色「${perms?.role_label ?? '未知'}」无权限查看审计日志与配置中心。`}
        />
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">审计与配置</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          全操作审计链路可追溯（含 before/after 快照），配置中心支持 LLM/Embedding 动态接入与密钥安全管理。
        </p>
      </div>
      <Tabs defaultValue="audit">
        <TabsList>
          <TabsTrigger value="audit">审计日志</TabsTrigger>
          <TabsTrigger value="kb-health">知识健康报表</TabsTrigger>
          {canManageConfig && <TabsTrigger value="config">配置中心</TabsTrigger>}
        </TabsList>
        <TabsContent value="audit" className="mt-4">
          <AuditTab />
        </TabsContent>
        <TabsContent value="kb-health" className="mt-4">
          <KbHealthTab />
        </TabsContent>
        {canManageConfig && (
          <TabsContent value="config" className="mt-4">
            <ConfigTab />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
