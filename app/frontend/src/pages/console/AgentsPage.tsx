/** Agent 工作台：深度诊断 / 知识治理 / 值班报告 / 会话轨迹 / CMDB 资产。 */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, BrainCircuit, ChevronDown, ChevronUp, Database, FileText, History, Search, Users } from 'lucide-react';
import { toast } from 'sonner';
import {
  consoleApi,
  errDetail,
  type AgentCluster,
  type AgentDiagnoseResult,
  type AgentDraftOutcome,
  type AgentKbGovernanceResult,
  type AgentSession,
  type AgentTraceStep,
  type CmdbAsset,
} from '@/lib/console-api';
import {
  ConfidenceBadge,
  CopyButton,
  JsonPre,
  QualityMetricsCard,
  SeverityBadge,
  SpinnerLine,
  StateGate,
  StatusBadge,
  fmtTime,
  type QualityMetrics,
} from '@/components/console/shared';
import { usePermissions } from '@/components/console/ConsoleLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';

const ONCALL_WINDOWS = [
  { value: '1h', label: '近 1 小时' },
  { value: '24h', label: '近 24 小时' },
  { value: '7d', label: '近 7 天' },
];

const SESSION_TYPE_LABEL: Record<string, string> = {
  diagnose: '深度诊断',
  kb_governance: '知识治理',
  oncall: '值班报告',
};

const GOVERNANCE_WINDOWS = [
  { value: '1h', label: '近 1 小时' },
  { value: '24h', label: '近 24 小时' },
  { value: '7d', label: '近 7 天' },
];

/** 从持久化会话行还原深度诊断结果（进入页面时展示最近一次执行结果）。
 * 后端保存结构：result={"conclusion": {...}, "threshold": n, "quality": {...}, "rag": {...}}，模型/轮次/轨迹在会话列上。 */
function parseDiagnoseSession(session: AgentSession | null | undefined): AgentDiagnoseResult | null {
  if (!session) return null;
  const r = (session.result ?? {}) as Record<string, unknown>;
  const c = r.conclusion as Record<string, unknown> | null | undefined;
  if (!c || typeof c !== 'object' || typeof c.root_cause !== 'string') return null;
  return {
    status: session.status,
    session_id: session.id,
    event_id: session.event_id ?? 0,
    message: `最近一次深度诊断结果（会话 #${session.id}）`,
    agent: {
      model: session.model,
      iterations: session.iterations ?? 0,
      duration_ms: session.duration_ms ?? 0,
      tool_trace: session.tool_trace ?? [],
      conclusion: {
        root_cause: String(c.root_cause ?? ''),
        solution: String(c.solution ?? ''),
        confidence: Number(c.confidence ?? 0),
        evidence_chain: Array.isArray(c.evidence_chain) ? (c.evidence_chain as string[]) : [],
        command: typeof c.command === 'string' ? c.command : '',
        low_confidence: Boolean(c.low_confidence),
        threshold: typeof r.threshold === 'number' ? r.threshold : undefined,
      },
      quality: (r.quality ?? null) as QualityMetrics | null,
      rag: (r.rag ?? null) as NonNullable<AgentDiagnoseResult['agent']>['rag'],
      usage: (r.usage ?? null) as Record<string, unknown> | null,
    },
  };
}

/** 从持久化会话行还原知识治理结果。后端保存结构：result 顶层含 clusters、drafts 与 merge_result。 */
function parseGovernanceSession(session: AgentSession | null | undefined): AgentKbGovernanceResult['governance'] | null {
  if (!session) return null;
  const r = (session.result ?? {}) as Record<string, unknown>;
  if (!Array.isArray(r.clusters)) return null;
  return {
    time_window: typeof r.time_window === 'string' ? r.time_window : undefined,
    analysis: typeof r.analysis === 'string' ? r.analysis : '',
    clusters: r.clusters as AgentCluster[],
    drafts_submitted: Array.isArray(r.drafts_submitted) ? (r.drafts_submitted as AgentDraftOutcome[]) : [],
    drafts_skipped: Array.isArray(r.drafts_skipped) ? (r.drafts_skipped as AgentDraftOutcome[]) : [],
    merge_result: (r.merge_result ?? {}) as Record<string, unknown>,
    quality: (r.quality ?? null) as QualityMetrics | null,
    model: session.model,
    duration_ms: session.duration_ms ?? 0,
  };
}

/** 工具轨迹渲染：诊断 Agent 的 tool 步骤与治理/值班 Agent 的 step 步骤统一展示。 */
function TraceSteps({ steps }: { steps: AgentTraceStep[] }) {
  if (!steps || steps.length === 0) {
    return <p className="text-xs text-muted-foreground">无工具轨迹</p>;
  }
  return (
    <ol className="space-y-2">
      {steps.map((s, i) => {
        const title = s.tool || s.step || `第 ${s.iteration ?? i + 1} 步`;
        const observation = s.observation ?? s.result;
        return (
          <li key={i} className="rounded-md border p-2.5">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge variant="secondary" className="font-mono">#{i + 1}</Badge>
              {s.iteration !== undefined && <Badge variant="outline" className="font-mono">轮次 {s.iteration}</Badge>}
              <span className="font-mono font-medium">{title}</span>
              {s.status && <StatusBadge status={s.status} />}
            </div>
            {s.thought && <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">思考：{s.thought}</p>}
            {s.args && Object.keys(s.args).length > 0 && (
              <pre className="log-block mt-1.5 max-h-24 overflow-y-auto text-[11px]">args: {JSON.stringify(s.args, null, 2)}</pre>
            )}
            {s.error && <p className="mt-1.5 text-xs text-red-600">错误：{s.error}</p>}
            {s.raw && <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">raw: {s.raw}</p>}
            {observation !== undefined && observation !== null && (
              <pre className="log-block mt-1.5 max-h-32 overflow-y-auto text-[11px]">{JSON.stringify(observation, null, 2)}</pre>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** 证据链列表。 */
function EvidenceChain({ items }: { items: string[] }) {
  if (!items || items.length === 0) return <p className="text-xs text-muted-foreground">无</p>;
  return (
    <ul className="space-y-1">
      {items.map((e, i) => (
        <li key={i} className="flex gap-2 text-xs leading-relaxed">
          <Badge variant="secondary" className="mt-0.5 shrink-0 font-mono">E{i + 1}</Badge>
          <span>{e}</span>
        </li>
      ))}
    </ul>
  );
}

// ---------------- 深度诊断 ----------------

function DiagnoseTab() {
  const perms = usePermissions();
  const queryClient = useQueryClient();
  const [eventId, setEventId] = useState<string>('');

  const eventsQuery = useQuery({
    queryKey: ['agent-events'],
    queryFn: () => consoleApi.listEvents({ skip: 0, limit: 50, time_range: '7d' }),
  });
  const events = useMemo(() => eventsQuery.data?.items ?? [], [eventsQuery.data]);

  useEffect(() => {
    if (!eventId && events.length > 0) setEventId(String(events[0].id));
  }, [events, eventId]);

  // 进入页面时展示最近一次诊断结果（优先读取持久化 agent_sessions）
  const latestQuery = useQuery({
    queryKey: ['agent-session-latest', 'diagnose'],
    queryFn: () => consoleApi.listAgentSessions({ session_type: 'diagnose', limit: 1 }),
  });
  const latestResult = useMemo(
    () => parseDiagnoseSession(latestQuery.data?.items?.[0]),
    [latestQuery.data],
  );

  const mutation = useMutation({
    mutationFn: () => consoleApi.agentDiagnose(Number(eventId)),
    onSuccess: (res) => {
      toast.success(res.message || 'Agent 深度诊断完成');
      queryClient.invalidateQueries({ queryKey: ['agent-session-latest'] });
    },
    onError: (e) => toast.error(`Agent 诊断失败：${errDetail(e)}`),
  });

  const result = mutation.data ?? latestResult;

  return (
    <div className="space-y-4">
      {perms?.can_diagnose && (
      <div className="flex flex-wrap items-end gap-2.5">
        <div className="w-72 min-w-56">
          <Label className="mb-1 text-xs">选择告警事件</Label>
          <Select value={eventId} onValueChange={setEventId}>
            <SelectTrigger className="h-9 text-xs"><SelectValue placeholder="选择事件" /></SelectTrigger>
            <SelectContent>
              {events.map((e) => (
                <SelectItem key={e.id} value={String(e.id)}>
                  #{e.id} {e.service_name} · {e.error_type || '未分类'}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          className="h-9"
          disabled={!eventId || mutation.isPending || !perms?.can_diagnose}
          onClick={() => mutation.mutate()}
        >
          <BrainCircuit className="mr-1.5 h-4 w-4" />
          {mutation.isPending ? 'Agent 推理中…' : '启动深度诊断'}
        </Button>
      </div>
      )}
      {eventsQuery.isLoading && <SpinnerLine text="加载告警列表…" />}

      {mutation.isPending && (
        <SpinnerLine text="Agent 正在多轮取证（告警详情 → CMDB → 日志 → 知识库），通常需要十几秒…" />
      )}

      {result && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <StatusBadge status={result.status} />
            {!mutation.data && <Badge variant="secondary">最近一次执行结果</Badge>}
            {result.agent && (
              <>
                <Badge variant="outline" className="font-mono">{result.agent.model}</Badge>
                <Badge variant="outline">{result.agent.iterations} 轮推理</Badge>
                <Badge variant="outline">{Math.round(result.agent.duration_ms)} ms</Badge>
                <Badge variant="outline">{result.agent.tool_trace.length} 次工具调用</Badge>
                {result.agent.rag?.kb_search_used && (
                  <Badge variant="outline">知识库召回 {result.agent.rag.case_count ?? 0} 条案例</Badge>
                )}
                {result.agent.stability && (
                  <Badge variant="outline" className="font-mono text-[10px]">
                    T={result.agent.stability.temperature ?? 0} · 评估上下文{" "}
                    {result.agent.stability.eval_context?.merged_count ?? 0} 条 ·{" "}
                    {(result.agent.stability.context_fingerprint ?? "").slice(0, 8)}
                  </Badge>
                )}
              </>
            )}
            <span className="text-muted-foreground">{result.message}</span>
          </div>

          {result.agent && (
            <>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Agent 结论</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <QualityMetricsCard quality={result.agent.quality} title="Agent 诊断质量评估" />
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-muted-foreground">置信度</span>
                    <ConfidenceBadge
                      value={result.agent.conclusion.confidence}
                      low={result.agent.conclusion.low_confidence}
                    />
                    {result.agent.conclusion.low_confidence && (
                      <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700">
                        建议人工复核（阈值 {((result.agent.conclusion.threshold ?? 0.7) * 100).toFixed(0)}%）
                      </Badge>
                    )}
                  </div>
                  <p className="text-[11px] leading-relaxed text-muted-foreground">
                    口径说明：置信度是模型对本次结论的自评把握度；Trust Index 是证据一致性客观评分（0.4×忠实度 + 0.35×引用覆盖 + 0.25×反幻觉，达标线 85%），二者独立衡量，可出现「置信度高但 Trust Index 低」（结论缺证据支撑）或反之，均需人工关注。
                  </p>
                  <div>
                    <p className="mb-1 text-xs font-medium text-muted-foreground">根因分析</p>
                    <p className="leading-relaxed">{result.agent.conclusion.root_cause}</p>
                  </div>
                  <div>
                    <p className="mb-1 text-xs font-medium text-muted-foreground">处置建议</p>
                    <p className="leading-relaxed">{result.agent.conclusion.solution}</p>
                  </div>
                  {result.agent.conclusion.command && (
                    <div>
                      <div className="mb-1 flex items-center justify-between">
                        <p className="text-xs font-medium text-muted-foreground">处置命令</p>
                        <CopyButton text={result.agent.conclusion.command} label="复制命令" size="xs" />
                      </div>
                      <pre className="log-block">{result.agent.conclusion.command}</pre>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">证据链（{result.agent.conclusion.evidence_chain.length} 条）</CardTitle>
                </CardHeader>
                <CardContent>
                  <EvidenceChain items={result.agent.conclusion.evidence_chain} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">工具调用轨迹</CardTitle>
                </CardHeader>
                <CardContent>
                  <TraceSteps steps={result.agent.tool_trace} />
                </CardContent>
              </Card>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------- 知识治理 ----------------

function GovernanceTab() {
  const perms = usePermissions();
  const queryClient = useQueryClient();
  const [timeWindow, setTimeWindow] = useState('24h');
  // 进入页面时读取最近一次治理会话（持久化 agent_sessions），重新运行后刷新
  const latestQuery = useQuery({
    queryKey: ['agent-session-latest', 'kb_governance'],
    queryFn: () => consoleApi.listAgentSessions({ session_type: 'kb_governance', limit: 1 }),
  });
  const latestSession = latestQuery.data?.items?.[0] ?? null;
  const latestGovernance = useMemo(() => parseGovernanceSession(latestSession), [latestSession]);
  const mutation = useMutation({
    mutationFn: () => consoleApi.agentKbGovernance(timeWindow),
    onSuccess: (res) => {
      toast.success(res.message || '知识治理 Agent 运行完成');
      queryClient.invalidateQueries({ queryKey: ['agent-session-latest'] });
    },
    onError: (e) => toast.error(`知识治理 Agent 失败：${errDetail(e)}`),
  });
  const g = mutation.data?.governance ?? latestGovernance;

  return (
    <div className="space-y-4">
      {perms?.can_edit_kb && (
      <div className="flex flex-wrap items-end gap-2.5">
        <div className="w-36">
          <Label className="mb-1 text-xs">统计时间窗</Label>
          <Select value={timeWindow} onValueChange={setTimeWindow}>
            <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>{GOVERNANCE_WINDOWS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <Button className="h-9" disabled={mutation.isPending || !perms?.can_edit_kb} onClick={() => mutation.mutate()}>
          <Users className="mr-1.5 h-4 w-4" />
          {mutation.isPending ? 'Agent 治理中…' : '运行知识治理 Agent'}
        </Button>
        <p className="text-xs text-muted-foreground">聚类时间窗内告警簇 → AI 起草案例（走审批）→ 生成合并提案。</p>
      </div>
      )}

      {mutation.isPending && <SpinnerLine text="Agent 正在聚类与起草案例，通常需要十几秒…" />}

      {g && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {mutation.data ? (
              <Badge variant="default">本次执行结果</Badge>
            ) : (
              <Badge variant="secondary">
                最近一次执行结果{latestSession ? `（会话 #${latestSession.id} · ${fmtTime(latestSession.created_at)}）` : ''}
              </Badge>
            )}
            {g.time_window && <Badge variant="outline">时间窗 {g.time_window}</Badge>}
          </div>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">簇分析</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm leading-relaxed">{g.analysis}</p>
              {g.clusters.length === 0 ? (
                <p className="text-xs text-muted-foreground">当前无待治理告警簇。</p>
              ) : (
                <div className="overflow-hidden rounded-md border text-xs">
                  <table className="w-full table-fixed">
                    <thead>
                      <tr className="border-b bg-muted/60 text-left text-muted-foreground">
                        <th className="w-12 px-3 py-2 font-medium">告警数</th>
                        <th className="px-3 py-2 font-medium">告警模板</th>
                        <th className="w-40 px-3 py-2 font-medium">涉及服务</th>
                        <th className="w-20 px-3 py-2 font-medium">最高级别</th>
                      </tr>
                    </thead>
                    <tbody>
                      {g.clusters.map((c, i) => (
                        <tr key={i} className="border-b last:border-b-0">
                          <td className="px-3 py-2 font-mono font-medium">{c.count}</td>
                          <td className="px-3 py-2 break-all">{c.template}</td>
                          <td className="px-3 py-2 truncate" title={c.services.join('、')}>{c.services.join('、')}</td>
                          <td className="px-3 py-2"><SeverityBadge severity={c.max_severity} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          <QualityMetricsCard quality={g.quality} title="本次起草质量评估（Trust Index 口径）" />

          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">已提交案例（{g.drafts_submitted.length}）</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {g.drafts_submitted.length === 0 && <p className="text-xs text-muted-foreground">本次无新增（可能已存在同名案例或变更集在途）。</p>}
                {g.drafts_submitted.map((d, i) => (
                  <div key={i} className="rounded-md border p-2.5 text-xs">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-medium">{d.case_id}</span>
                      {d.auto_published && <Badge variant="outline">免审直发</Badge>}
                      {d.approval_request_id && <Badge variant="outline">审批单 #{d.approval_request_id}</Badge>}
                    </div>
                    <p className="mt-1 truncate text-muted-foreground" title={d.alert_template ?? ''}>{d.alert_template}</p>
                    {d.quality?.trust_index != null && (
                      <p className="mt-1 text-muted-foreground">
                        单条质量：Trust Index {(d.quality.trust_index * 100).toFixed(1)}%
                        {d.quality.quality_ok === false && <span className="text-amber-700">（未达 85% 达标线）</span>}
                      </p>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">跳过/失败（{g.drafts_skipped.length}）</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {g.drafts_skipped.length === 0 && <p className="text-xs text-muted-foreground">无跳过项。</p>}
                {g.drafts_skipped.map((d, i) => (
                  <div key={i} className="rounded-md border p-2.5 text-xs">
                    <p className="truncate" title={d.alert_template ?? ''}>{d.alert_template}</p>
                    <p className="mt-1 text-amber-700">{d.reason}</p>
                    {d.quality?.trust_index != null && (
                      <p className="mt-1 text-muted-foreground">
                        单条质量：Trust Index {(d.quality.trust_index * 100).toFixed(1)}%
                      </p>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">合并提案结果</CardTitle>
            </CardHeader>
            <CardContent>
              <JsonPre data={g.merge_result} />
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

// ---------------- 值班报告 ----------------

function OncallTab() {
  const perms = usePermissions();
  const queryClient = useQueryClient();
  const [window, setWindow] = useState('24h');
  const [expandedReport, setExpandedReport] = useState<number | null>(null);
  const mutation = useMutation({
    mutationFn: () => consoleApi.agentOncallReport(window),
    onSuccess: (res) => {
      toast.success(res.message || '值班报告已生成');
      queryClient.invalidateQueries({ queryKey: ['agent-oncall-reports'] });
    },
    onError: (e) => toast.error(`值班 Agent 失败：${errDetail(e)}`),
  });
  const r = mutation.data?.report;

  const historyQuery = useQuery({
    queryKey: ['agent-oncall-reports'],
    queryFn: () => consoleApi.listOncallReports(),
  });
  const history = historyQuery.data?.items ?? [];

  return (
    <div className="space-y-4">
      {perms?.can_diagnose && (
      <div className="flex flex-wrap items-end gap-2.5">
        <div className="w-36">
          <Label className="mb-1 text-xs">统计时间窗</Label>
          <Select value={window} onValueChange={setWindow}>
            <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>{ONCALL_WINDOWS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <Button className="h-9" disabled={mutation.isPending || !perms?.can_diagnose} onClick={() => mutation.mutate()}>
          <FileText className="mr-1.5 h-4 w-4" />
          {mutation.isPending ? 'Agent 汇总中…' : '生成值班报告'}
        </Button>
      </div>
      )}

      {mutation.isPending && <SpinnerLine text="Agent 正在统计时间窗影响面并撰写 ChatOps 建议…" />}

      {r && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge variant="outline" className="border-red-500/40 bg-red-500/10 font-semibold text-red-600">{r.priority}</Badge>
            <Badge variant="outline">{r.time_window} 窗口</Badge>
            <Badge variant="outline">告警 {r.event_count} 条</Badge>
            {Object.entries(r.by_severity).map(([k, v]) => (
              <span key={k} className="inline-flex items-center gap-1">
                <SeverityBadge severity={k} />×{v}
              </span>
            ))}
            {mutation.data?.status === 'degraded' && (
              <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700">AI 失败，确定性降级报告</Badge>
            )}
          </div>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">影响面摘要</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm leading-relaxed">{r.impact_summary}</p>
              <div>
                <p className="mb-1.5 text-xs font-medium text-muted-foreground">处置动作</p>
                <ol className="list-decimal space-y-1 pl-5 text-sm">
                  {r.actions.length === 0 && <li className="list-none text-xs text-muted-foreground">无</li>}
                  {r.actions.map((a, i) => <li key={i}>{a}</li>)}
                </ol>
              </div>
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">需通知负责人</p>
                <div className="flex flex-wrap gap-1.5">
                  {r.owners_to_notify.length === 0 && <span className="text-xs text-muted-foreground">无</span>}
                  {r.owners_to_notify.map((o) => <Badge key={o} variant="secondary">{o}</Badge>)}
                </div>
              </div>
            </CardContent>
          </Card>

          {r.quality && <QualityMetricsCard quality={r.quality} title="报告质量评估（Trust Index 口径）" />}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">CMDB 影响面（{r.affected_systems.length} 个系统）</CardTitle>
            </CardHeader>
            <CardContent>
              {r.affected_systems.length === 0 ? (
                <p className="text-xs text-muted-foreground">窗口内告警未映射到 CMDB 系统。</p>
              ) : (
                <div className="overflow-hidden rounded-md border text-xs">
                  <table className="w-full table-fixed">
                    <thead>
                      <tr className="border-b bg-muted/60 text-left text-muted-foreground">
                        <th className="w-36 px-3 py-2 font-medium">系统</th>
                        <th className="w-16 px-3 py-2 font-medium">告警数</th>
                        <th className="w-24 px-3 py-2 font-medium">最高级别</th>
                        <th className="w-36 px-3 py-2 font-medium">负责人</th>
                        <th className="px-3 py-2 font-medium">涉及服务</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.affected_systems.map((s) => (
                        <tr key={s.system} className="border-b last:border-b-0">
                          <td className="px-3 py-2 font-medium">{s.system}</td>
                          <td className="px-3 py-2 font-mono">{s.event_count}</td>
                          <td className="px-3 py-2"><SeverityBadge severity={s.max_severity} /></td>
                          <td className="px-3 py-2 truncate" title={s.owners.join('、')}>{s.owners.join('、') || '—'}</td>
                          <td className="px-3 py-2 truncate" title={s.services.map((x) => x.service).join('、')}>
                            {s.services.map((x) => x.service).join('、')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {Object.keys(r.unmapped_services ?? {}).length > 0 && (
                <p className="mt-2 text-xs text-amber-700">
                  未映射 CMDB 的服务：{Object.entries(r.unmapped_services).map(([k, v]) => `${k}(${v})`).join('、')}
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">ChatOps 处置建议</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="mb-2 flex justify-end">
                <CopyButton text={r.chatops_text} label="复制 ChatOps 文本" size="xs" />
              </div>
              <pre className="log-block max-h-72 overflow-y-auto whitespace-pre-wrap">{r.chatops_text}</pre>
            </CardContent>
          </Card>
        </div>
      )}

      <Separator />

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">历史报告（{history.length}）</CardTitle>
        </CardHeader>
        <CardContent>
          <StateGate
            loading={historyQuery.isLoading}
            error={historyQuery.isError ? errDetail(historyQuery.error) : null}
            onRetry={() => historyQuery.refetch()}
            isEmpty={history.length === 0}
            empty="暂无历史报告"
          >
            <div className="space-y-2">
              {history.map((h) => {
                const open = expandedReport === h.id;
                const rep = h.report;
                const systems = h.affected_systems ?? [];
                const chatops = h.chatops_text || rep?.chatops_text || '';
                return (
                  <div key={h.id} className="rounded-md border text-xs">
                    <button
                      className="flex w-full flex-wrap items-center gap-2 px-3 py-2.5 text-left hover:bg-accent/60"
                      onClick={() => setExpandedReport(open ? null : h.id)}
                    >
                      <span className="font-mono font-medium">#{h.id}</span>
                      <Badge variant="outline">{h.time_window}</Badge>
                      <span className="text-muted-foreground">{h.event_count} 条告警（严重 {h.critical_count} / 警告 {h.warning_count}）</span>
                      {rep?.priority && (
                        <Badge variant="outline" className="border-red-500/40 bg-red-500/10 font-semibold text-red-600">{rep.priority}</Badge>
                      )}
                      <span className="ml-auto text-muted-foreground">{h.actor} · {fmtTime(h.created_at)}</span>
                      {open ? <ChevronUp className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                    </button>
                    {open && (
                      <div className="space-y-3 border-t px-3 py-3">
                        {rep ? (
                          <>
                            <div>
                              <p className="mb-1 font-medium text-muted-foreground">影响面摘要</p>
                              <p className="leading-relaxed">{rep.impact_summary}</p>
                            </div>
                            <div>
                              <p className="mb-1 font-medium text-muted-foreground">处置动作</p>
                              <ol className="list-decimal space-y-1 pl-5">
                                {rep.actions.length === 0 && <li className="list-none text-muted-foreground">无</li>}
                                {rep.actions.map((a, i) => <li key={i}>{a}</li>)}
                              </ol>
                            </div>
                            <div>
                              <p className="mb-1 font-medium text-muted-foreground">需通知负责人</p>
                              <div className="flex flex-wrap gap-1.5">
                                {rep.owners_to_notify.length === 0 && <span className="text-muted-foreground">无</span>}
                                {rep.owners_to_notify.map((o) => <Badge key={o} variant="secondary">{o}</Badge>)}
                              </div>
                            </div>
                            {rep.quality && <QualityMetricsCard quality={rep.quality} title="报告质量评估" />}
                          </>
                        ) : (
                          <p className="text-muted-foreground">该报告缺少结构化详情（仅保留摘要统计）。</p>
                        )}
                        <div>
                          <p className="mb-1 font-medium text-muted-foreground">CMDB 影响面（{systems.length} 个系统）</p>
                          {systems.length === 0 ? (
                            <p className="text-muted-foreground">窗口内告警未映射到 CMDB 系统。</p>
                          ) : (
                            <div className="overflow-hidden rounded-md border">
                              <table className="w-full table-fixed">
                                <thead>
                                  <tr className="border-b bg-muted/60 text-left text-muted-foreground">
                                    <th className="w-36 px-3 py-1.5 font-medium">系统</th>
                                    <th className="w-16 px-3 py-1.5 font-medium">告警数</th>
                                    <th className="w-24 px-3 py-1.5 font-medium">最高级别</th>
                                    <th className="w-36 px-3 py-1.5 font-medium">负责人</th>
                                    <th className="px-3 py-1.5 font-medium">涉及服务</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {systems.map((s) => (
                                    <tr key={s.system} className="border-b last:border-b-0">
                                      <td className="px-3 py-1.5 font-medium">{s.system}</td>
                                      <td className="px-3 py-1.5 font-mono">{s.event_count}</td>
                                      <td className="px-3 py-1.5"><SeverityBadge severity={s.max_severity} /></td>
                                      <td className="px-3 py-1.5 truncate" title={s.owners.join('、')}>{s.owners.join('、') || '—'}</td>
                                      <td className="px-3 py-1.5 truncate" title={s.services.map((x) => x.service).join('、')}>
                                        {s.services.map((x) => x.service).join('、')}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </div>
                        <div>
                          <div className="mb-1 flex items-center justify-between">
                            <p className="font-medium text-muted-foreground">ChatOps 文本</p>
                            {chatops && <CopyButton text={chatops} label="复制文本" size="xs" />}
                          </div>
                          {chatops ? (
                            <pre className="log-block max-h-60 overflow-y-auto whitespace-pre-wrap">{chatops}</pre>
                          ) : (
                            <p className="text-muted-foreground">无</p>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </StateGate>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------- 会话轨迹 ----------------

function SessionsTab() {
  const [sessionType, setSessionType] = useState('all');
  const [expanded, setExpanded] = useState<number | null>(null);

  const query = useQuery({
    queryKey: ['agent-sessions', sessionType],
    queryFn: () =>
      consoleApi.listAgentSessions({
        session_type: sessionType === 'all' ? undefined : sessionType,
        limit: 50,
      }),
  });
  const sessions = query.data?.items ?? [];

  return (
    <div className="space-y-3">
      <div className="w-36">
        <Label className="mb-1 text-xs">会话类型</Label>
        <Select value={sessionType} onValueChange={setSessionType}>
          <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            {Object.entries(SESSION_TYPE_LABEL).map(([v, label]) => (
              <SelectItem key={v} value={v}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <StateGate
        loading={query.isLoading}
        error={query.isError ? errDetail(query.error) : null}
        onRetry={() => query.refetch()}
        isEmpty={sessions.length === 0}
        empty="暂无 Agent 会话"
        emptyHint="运行任一 Agent 后，这里会记录完整工具轨迹与结果"
      >
        <div className="space-y-2">
          {sessions.map((s: AgentSession) => (
            <div key={s.id} className="rounded-md border">
              <button
                className="flex w-full flex-wrap items-center gap-2 px-3 py-2.5 text-left text-xs hover:bg-accent/60"
                onClick={() => setExpanded(expanded === s.id ? null : s.id)}
              >
                <span className="font-mono font-medium">#{s.id}</span>
                <Badge variant="secondary">{SESSION_TYPE_LABEL[s.session_type] ?? s.session_type}</Badge>
                <StatusBadge status={s.status} />
                {s.event_id && <span className="text-muted-foreground">事件 #{s.event_id}</span>}
                <span className="font-mono text-muted-foreground">{s.model}</span>
                {s.iterations !== null && <Badge variant="outline">{s.iterations} 轮</Badge>}
                {s.duration_ms !== null && <span className="text-muted-foreground">{Math.round(s.duration_ms)} ms</span>}
                <span className="ml-auto text-muted-foreground">{s.actor} · {fmtTime(s.created_at)}</span>
              </button>
              {expanded === s.id && (
                <div className="space-y-3 border-t px-3 py-3">
                  {s.summary && <p className="text-xs leading-relaxed">{s.summary}</p>}
                  {s.error_message && <p className="text-xs text-red-600">错误：{s.error_message}</p>}
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-muted-foreground">执行轨迹</p>
                    <TraceSteps steps={s.tool_trace ?? []} />
                  </div>
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-muted-foreground">结构化结果</p>
                    <JsonPre data={s.result} />
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </StateGate>
    </div>
  );
}

// ---------------- CMDB 资产 ----------------

function CmdbTab() {
  const [q, setQ] = useState('');
  const [input, setInput] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setQ(input.trim()), 300);
    return () => clearTimeout(t);
  }, [input]);

  const query = useQuery({
    queryKey: ['agent-cmdb', q],
    queryFn: () => consoleApi.listCmdbAssets(q || undefined),
  });
  const assets = query.data?.items ?? [];

  return (
    <div className="space-y-3">
      <div className="relative w-72">
        <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          className="h-9 pl-8 text-xs"
          placeholder="搜索主机 / IP / 服务 / 系统"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
      </div>

      <StateGate
        loading={query.isLoading}
        error={query.isError ? errDetail(query.error) : null}
        onRetry={() => query.refetch()}
        isEmpty={assets.length === 0}
        empty="没有匹配的 CMDB 资产"
      >
        <div className="overflow-x-auto rounded-md border text-xs">
          <table className="w-full min-w-[860px] table-fixed">
            <thead>
              <tr className="border-b bg-muted/60 text-left text-muted-foreground">
                <th className="w-36 px-3 py-2 font-medium">主机名</th>
                <th className="w-32 px-3 py-2 font-medium">IP</th>
                <th className="w-36 px-3 py-2 font-medium">所属系统</th>
                <th className="w-36 px-3 py-2 font-medium">服务</th>
                <th className="w-24 px-3 py-2 font-medium">集群</th>
                <th className="w-20 px-3 py-2 font-medium">环境</th>
                <th className="w-24 px-3 py-2 font-medium">负责人</th>
                <th className="px-3 py-2 font-medium">依赖 / 日志路径</th>
              </tr>
            </thead>
            <tbody>
              {assets.map((a: CmdbAsset) => (
                <tr key={a.id} className="border-b last:border-b-0">
                  <td className="px-3 py-2 font-mono">{a.hostname}</td>
                  <td className="px-3 py-2 font-mono">{a.ip}</td>
                  <td className="px-3 py-2">{a.system_name}</td>
                  <td className="px-3 py-2 truncate" title={a.service_name}>{a.service_name}</td>
                  <td className="px-3 py-2 truncate">{a.cluster || '—'}</td>
                  <td className="px-3 py-2">{a.environment || '—'}</td>
                  <td className="px-3 py-2 truncate" title={a.owner_email ?? ''}>{a.owner || '—'}</td>
                  <td className="px-3 py-2">
                    <p className="truncate" title={a.dependencies.join('、')}>{a.dependencies.join('、') || '—'}</p>
                    <p className="truncate font-mono text-[11px] text-muted-foreground" title={a.log_path ?? ''}>{a.log_path || ''}</p>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </StateGate>
    </div>
  );
}

// ---------------- 页面 ----------------

export default function AgentsPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <Bot className="h-5 w-5" />
          Agent 工作台
        </h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          三类运维 Agent：深度诊断（多轮工具推理）、知识治理（聚类起草走审批）、值班报告（影响面 + ChatOps 建议）。
        </p>
      </div>

      <Tabs defaultValue="diagnose">
        <TabsList>
          <TabsTrigger value="diagnose" className="gap-1.5"><BrainCircuit className="h-3.5 w-3.5" />深度诊断</TabsTrigger>
          <TabsTrigger value="governance" className="gap-1.5"><Users className="h-3.5 w-3.5" />知识治理</TabsTrigger>
          <TabsTrigger value="oncall" className="gap-1.5"><FileText className="h-3.5 w-3.5" />值班报告</TabsTrigger>
          <TabsTrigger value="sessions" className="gap-1.5"><History className="h-3.5 w-3.5" />会话轨迹</TabsTrigger>
          <TabsTrigger value="cmdb" className="gap-1.5"><Database className="h-3.5 w-3.5" />CMDB</TabsTrigger>
        </TabsList>
        <TabsContent value="diagnose" className="mt-4"><DiagnoseTab /></TabsContent>
        <TabsContent value="governance" className="mt-4"><GovernanceTab /></TabsContent>
        <TabsContent value="oncall" className="mt-4"><OncallTab /></TabsContent>
        <TabsContent value="sessions" className="mt-4"><SessionsTab /></TabsContent>
        <TabsContent value="cmdb" className="mt-4"><CmdbTab /></TabsContent>
      </Tabs>
    </div>
  );
}
