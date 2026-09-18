/** Agent 工作台：深度诊断 / 知识治理 / 值班报告 / 会话轨迹 / CMDB 资产。 */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
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

const WINDOW_VALUE_KEYS: Record<string, string> = { '1h': 'h1', '24h': 'h24', '7d': 'd7' };
const TIME_WINDOWS = ['1h', '24h', '7d'];
const SESSION_TYPES = ['diagnose', 'kb_governance', 'oncall'];

/** 中文语境用顿号连接列表，英文语境用逗号。 */
function listJoin(items: string[], zh: boolean): string {
  return items.join(zh ? '、' : ', ');
}

/** 从持久化会话行还原深度诊断结果（进入页面时展示最近一次执行结果）。
 * 后端保存结构：result={"conclusion": {...}, "threshold": n, "quality": {...}, "rag": {...}}，模型/轮次/轨迹在会话列上。
 * message 留空，由调用方按当前语言渲染。 */
function parseDiagnoseSession(session: AgentSession | null | undefined): AgentDiagnoseResult | null {
  if (!session) return null;
  const r = (session.result ?? {}) as Record<string, unknown>;
  const c = r.conclusion as Record<string, unknown> | null | undefined;
  if (!c || typeof c !== 'object' || typeof c.root_cause !== 'string') return null;
  return {
    status: session.status,
    session_id: session.id,
    event_id: session.event_id ?? 0,
    message: '',
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
  const { t } = useTranslation();
  if (!steps || steps.length === 0) {
    return <p className="text-xs text-muted-foreground">{t('agents.trace.none')}</p>;
  }
  return (
    <ol className="space-y-2">
      {steps.map((s, i) => {
        const title = s.tool || s.step || t('agents.trace.stepN', { n: s.iteration ?? i + 1 });
        const observation = s.observation ?? s.result;
        return (
          <li key={i} className="rounded-md border p-2.5">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge variant="secondary" className="font-mono">#{i + 1}</Badge>
              {s.iteration !== undefined && <Badge variant="outline" className="font-mono">{t('agents.trace.iteration', { n: s.iteration })}</Badge>}
              <span className="font-mono font-medium">{title}</span>
              {s.status && <StatusBadge status={s.status} />}
            </div>
            {s.thought && <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{t('agents.trace.thought', { thought: s.thought })}</p>}
            {s.args && Object.keys(s.args).length > 0 && (
              <pre className="log-block mt-1.5 max-h-24 overflow-y-auto text-[11px]">args: {JSON.stringify(s.args, null, 2)}</pre>
            )}
            {s.error && <p className="mt-1.5 text-xs text-red-600">{t('agents.trace.error', { error: s.error })}</p>}
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
  const { t } = useTranslation();
  if (!items || items.length === 0) return <p className="text-xs text-muted-foreground">{t('agents.none')}</p>;
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
  const { t, i18n } = useTranslation();
  const zh = i18n.language?.startsWith('zh');
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
      toast.success(res.message || t('agents.diagnose.successToast'));
      queryClient.invalidateQueries({ queryKey: ['agent-session-latest'] });
    },
    onError: (e) => toast.error(t('agents.diagnose.errorToast', { error: errDetail(e) })),
  });

  const result = mutation.data ?? latestResult;
  // 最近一次会话结果的 message 按当前语言渲染（会话号取自 session_id）
  const resultMessage = mutation.data
    ? mutation.data.message
    : latestResult
      ? t('agents.diagnose.latestMessage', { id: latestResult.session_id })
      : '';

  return (
    <div className="space-y-4">
      {perms?.can_diagnose && (
      <div className="flex flex-wrap items-end gap-2.5">
        <div className="w-72 min-w-56">
          <Label className="mb-1 text-xs">{t('agents.diagnose.selectEvent')}</Label>
          <Select value={eventId} onValueChange={setEventId}>
            <SelectTrigger className="h-9 text-xs"><SelectValue placeholder={t('agents.diagnose.selectPlaceholder')} /></SelectTrigger>
            <SelectContent>
              {events.map((e) => (
                <SelectItem key={e.id} value={String(e.id)}>
                  {t('agents.diagnose.eventOption', { id: e.id, service: e.service_name, errorType: e.error_type || t('agents.diagnose.unclassified') })}
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
          {mutation.isPending ? t('agents.diagnose.running') : t('agents.diagnose.start')}
        </Button>
      </div>
      )}
      {eventsQuery.isLoading && <SpinnerLine text={t('agents.diagnose.loadingEvents')} />}

      {mutation.isPending && (
        <SpinnerLine text={t('agents.diagnose.tracing')} />
      )}

      {result && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <StatusBadge status={result.status} />
            {!mutation.data && <Badge variant="secondary">{t('agents.diagnose.latestBadge')}</Badge>}
            {result.agent && (
              <>
                <Badge variant="outline" className="font-mono">{result.agent.model}</Badge>
                <Badge variant="outline">{t('agents.diagnose.roundsBadge', { n: result.agent.iterations })}</Badge>
                <Badge variant="outline">{Math.round(result.agent.duration_ms)} ms</Badge>
                <Badge variant="outline">{t('agents.diagnose.toolCallsBadge', { n: result.agent.tool_trace.length })}</Badge>
                {result.agent.rag?.kb_search_used && (
                  <Badge variant="outline">{t('agents.diagnose.kbRecallBadge', { n: result.agent.rag.case_count ?? 0 })}</Badge>
                )}
                {result.agent.stability && (
                  <Badge variant="outline" className="font-mono text-[10px]">
                    {t('agents.diagnose.stabilityBadge', {
                      temp: result.agent.stability.temperature ?? 0,
                      count: result.agent.stability.eval_context?.merged_count ?? 0,
                      fingerprint: (result.agent.stability.context_fingerprint ?? '').slice(0, 8),
                    })}
                  </Badge>
                )}
              </>
            )}
            <span className="text-muted-foreground">{resultMessage}</span>
          </div>

          {result.agent && (
            <>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">{t('agents.diagnose.conclusionTitle')}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <QualityMetricsCard quality={result.agent.quality} title={t('agents.diagnose.qualityTitle')} />
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-muted-foreground">{t('agents.diagnose.confidence')}</span>
                    <ConfidenceBadge
                      value={result.agent.conclusion.confidence}
                      low={result.agent.conclusion.low_confidence}
                    />
                    {result.agent.conclusion.low_confidence && (
                      <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700">
                        {t('agents.diagnose.lowConfidenceBadge', { pct: ((result.agent.conclusion.threshold ?? 0.7) * 100).toFixed(0) })}
                      </Badge>
                    )}
                  </div>
                  <p className="text-[11px] leading-relaxed text-muted-foreground">
                    {t('agents.diagnose.metricNote')}
                  </p>
                  <div>
                    <p className="mb-1 text-xs font-medium text-muted-foreground">{t('agents.diagnose.rootCause')}</p>
                    <p className="leading-relaxed">{result.agent.conclusion.root_cause}</p>
                  </div>
                  <div>
                    <p className="mb-1 text-xs font-medium text-muted-foreground">{t('agents.diagnose.solution')}</p>
                    <p className="leading-relaxed">{result.agent.conclusion.solution}</p>
                  </div>
                  {result.agent.conclusion.command && (
                    <div>
                      <div className="mb-1 flex items-center justify-between">
                        <p className="text-xs font-medium text-muted-foreground">{t('agents.diagnose.command')}</p>
                        <CopyButton text={result.agent.conclusion.command} label={t('agents.diagnose.copyCommand')} size="xs" />
                      </div>
                      <pre className="log-block">{result.agent.conclusion.command}</pre>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">{t('agents.diagnose.evidenceTitle', { count: result.agent.conclusion.evidence_chain.length })}</CardTitle>
                </CardHeader>
                <CardContent>
                  <EvidenceChain items={result.agent.conclusion.evidence_chain} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">{t('agents.diagnose.traceTitle')}</CardTitle>
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
  const { t, i18n } = useTranslation();
  const zh = i18n.language?.startsWith('zh');
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
      toast.success(res.message || t('agents.governance.successToast'));
      queryClient.invalidateQueries({ queryKey: ['agent-session-latest'] });
    },
    onError: (e) => toast.error(t('agents.governance.errorToast', { error: errDetail(e) })),
  });
  const g = mutation.data?.governance ?? latestGovernance;

  return (
    <div className="space-y-4">
      {perms?.can_edit_kb && (
      <div className="flex flex-wrap items-end gap-2.5">
        <div className="w-36">
          <Label className="mb-1 text-xs">{t('agents.windowLabel')}</Label>
          <Select value={timeWindow} onValueChange={setTimeWindow}>
            <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>{TIME_WINDOWS.map((v) => <SelectItem key={v} value={v}>{t(`agents.window.${WINDOW_VALUE_KEYS[v]}`)}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <Button className="h-9" disabled={mutation.isPending || !perms?.can_edit_kb} onClick={() => mutation.mutate()}>
          <Users className="mr-1.5 h-4 w-4" />
          {mutation.isPending ? t('agents.governance.running') : t('agents.governance.run')}
        </Button>
        <p className="text-xs text-muted-foreground">{t('agents.governance.hint')}</p>
      </div>
      )}

      {mutation.isPending && <SpinnerLine text={t('agents.governance.tracing')} />}

      {g && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {mutation.data ? (
              <Badge variant="default">{t('agents.governance.currentBadge')}</Badge>
            ) : (
              <Badge variant="secondary">
                {latestSession
                  ? t('agents.governance.latestBadgeWithSession', { id: latestSession.id, time: fmtTime(latestSession.created_at) })
                  : t('agents.governance.latestBadge')}
              </Badge>
            )}
            {g.time_window && <Badge variant="outline">{t('agents.governance.timeWindowBadge', { w: g.time_window })}</Badge>}
          </div>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('agents.governance.clusterTitle')}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm leading-relaxed">{g.analysis}</p>
              {g.clusters.length === 0 ? (
                <p className="text-xs text-muted-foreground">{t('agents.governance.noClusters')}</p>
              ) : (
                <div className="overflow-hidden rounded-md border text-xs">
                  <table className="w-full table-fixed">
                    <thead>
                      <tr className="border-b bg-muted/60 text-left text-muted-foreground">
                        <th className="w-12 px-3 py-2 font-medium">{t('agents.governance.thAlerts')}</th>
                        <th className="px-3 py-2 font-medium">{t('agents.governance.thTemplate')}</th>
                        <th className="w-40 px-3 py-2 font-medium">{t('agents.governance.thServices')}</th>
                        <th className="w-20 px-3 py-2 font-medium">{t('agents.governance.thSeverity')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {g.clusters.map((c, i) => (
                        <tr key={i} className="border-b last:border-b-0">
                          <td className="px-3 py-2 font-mono font-medium">{c.count}</td>
                          <td className="px-3 py-2 break-all">{c.template}</td>
                          <td className="px-3 py-2 truncate" title={listJoin(c.services, zh)}>{listJoin(c.services, zh)}</td>
                          <td className="px-3 py-2"><SeverityBadge severity={c.max_severity} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          <QualityMetricsCard quality={g.quality} title={t('agents.governance.qualityTitle')} />

          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">{t('agents.governance.submittedTitle', { count: g.drafts_submitted.length })}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {g.drafts_submitted.length === 0 && <p className="text-xs text-muted-foreground">{t('agents.governance.noSubmitted')}</p>}
                {g.drafts_submitted.map((d, i) => (
                  <div key={i} className="rounded-md border p-2.5 text-xs">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-medium">{d.case_id}</span>
                      {d.auto_published && <Badge variant="outline">{t('agents.governance.autoPublishedBadge')}</Badge>}
                      {d.approval_request_id && <Badge variant="outline">{t('agents.governance.approvalBadge', { id: d.approval_request_id })}</Badge>}
                    </div>
                    <p className="mt-1 truncate text-muted-foreground" title={d.alert_template ?? ''}>{d.alert_template}</p>
                    {d.quality?.trust_index != null && (
                      <p className="mt-1 text-muted-foreground">
                        {t('agents.governance.singleQuality', { pct: (d.quality.trust_index * 100).toFixed(1) })}
                        {d.quality.quality_ok === false && <span className="text-amber-700">{t('agents.governance.belowGate')}</span>}
                      </p>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">{t('agents.governance.skippedTitle', { count: g.drafts_skipped.length })}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {g.drafts_skipped.length === 0 && <p className="text-xs text-muted-foreground">{t('agents.governance.noSkipped')}</p>}
                {g.drafts_skipped.map((d, i) => (
                  <div key={i} className="rounded-md border p-2.5 text-xs">
                    <p className="truncate" title={d.alert_template ?? ''}>{d.alert_template}</p>
                    <p className="mt-1 text-amber-700">{d.reason}</p>
                    {d.quality?.trust_index != null && (
                      <p className="mt-1 text-muted-foreground">
                        {t('agents.governance.singleQuality', { pct: (d.quality.trust_index * 100).toFixed(1) })}
                      </p>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('agents.governance.mergeResultTitle')}</CardTitle>
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
  const { t, i18n } = useTranslation();
  const zh = i18n.language?.startsWith('zh');
  const perms = usePermissions();
  const queryClient = useQueryClient();
  const [window, setWindow] = useState('24h');
  const [expandedReport, setExpandedReport] = useState<number | null>(null);
  const mutation = useMutation({
    mutationFn: () => consoleApi.agentOncallReport(window),
    onSuccess: (res) => {
      toast.success(res.message || t('agents.oncall.successToast'));
      queryClient.invalidateQueries({ queryKey: ['agent-oncall-reports'] });
    },
    onError: (e) => toast.error(t('agents.oncall.errorToast', { error: errDetail(e) })),
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
          <Label className="mb-1 text-xs">{t('agents.windowLabel')}</Label>
          <Select value={window} onValueChange={setWindow}>
            <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>{TIME_WINDOWS.map((v) => <SelectItem key={v} value={v}>{t(`agents.window.${WINDOW_VALUE_KEYS[v]}`)}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <Button className="h-9" disabled={mutation.isPending || !perms?.can_diagnose} onClick={() => mutation.mutate()}>
          <FileText className="mr-1.5 h-4 w-4" />
          {mutation.isPending ? t('agents.oncall.running') : t('agents.oncall.run')}
        </Button>
      </div>
      )}

      {mutation.isPending && <SpinnerLine text={t('agents.oncall.tracing')} />}

      {r && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge variant="outline" className="border-red-500/40 bg-red-500/10 font-semibold text-red-600">{r.priority}</Badge>
            <Badge variant="outline">{t('agents.oncall.windowBadge', { w: r.time_window })}</Badge>
            <Badge variant="outline">{t('agents.oncall.alertsBadge', { count: r.event_count })}</Badge>
            {Object.entries(r.by_severity).map(([k, v]) => (
              <span key={k} className="inline-flex items-center gap-1">
                <SeverityBadge severity={k} />×{v}
              </span>
            ))}
            {mutation.data?.status === 'degraded' && (
              <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700">{t('agents.oncall.degradedBadge')}</Badge>
            )}
          </div>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('agents.oncall.impactTitle')}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm leading-relaxed">{r.impact_summary}</p>
              <div>
                <p className="mb-1.5 text-xs font-medium text-muted-foreground">{t('agents.oncall.actions')}</p>
                <ol className="list-decimal space-y-1 pl-5 text-sm">
                  {r.actions.length === 0 && <li className="list-none text-xs text-muted-foreground">{t('agents.none')}</li>}
                  {r.actions.map((a, i) => <li key={i}>{a}</li>)}
                </ol>
              </div>
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">{t('agents.oncall.owners')}</p>
                <div className="flex flex-wrap gap-1.5">
                  {r.owners_to_notify.length === 0 && <span className="text-xs text-muted-foreground">{t('agents.none')}</span>}
                  {r.owners_to_notify.map((o) => <Badge key={o} variant="secondary">{o}</Badge>)}
                </div>
              </div>
            </CardContent>
          </Card>

          {r.quality && <QualityMetricsCard quality={r.quality} title={t('agents.oncall.qualityTitle')} />}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('agents.oncall.cmdbTitle', { count: r.affected_systems.length })}</CardTitle>
            </CardHeader>
            <CardContent>
              {r.affected_systems.length === 0 ? (
                <p className="text-xs text-muted-foreground">{t('agents.oncall.noCmdb')}</p>
              ) : (
                <div className="overflow-hidden rounded-md border text-xs">
                  <table className="w-full table-fixed">
                    <thead>
                      <tr className="border-b bg-muted/60 text-left text-muted-foreground">
                        <th className="w-36 px-3 py-2 font-medium">{t('agents.oncall.thSystem')}</th>
                        <th className="w-16 px-3 py-2 font-medium">{t('agents.oncall.thAlerts')}</th>
                        <th className="w-24 px-3 py-2 font-medium">{t('agents.oncall.thSeverity')}</th>
                        <th className="w-36 px-3 py-2 font-medium">{t('agents.oncall.thOwners')}</th>
                        <th className="px-3 py-2 font-medium">{t('agents.oncall.thServices')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.affected_systems.map((s) => (
                        <tr key={s.system} className="border-b last:border-b-0">
                          <td className="px-3 py-2 font-medium">{s.system}</td>
                          <td className="px-3 py-2 font-mono">{s.event_count}</td>
                          <td className="px-3 py-2"><SeverityBadge severity={s.max_severity} /></td>
                          <td className="px-3 py-2 truncate" title={listJoin(s.owners, zh)}>{listJoin(s.owners, zh) || '—'}</td>
                          <td className="px-3 py-2 truncate" title={listJoin(s.services.map((x) => x.service), zh)}>
                            {listJoin(s.services.map((x) => x.service), zh)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {Object.keys(r.unmapped_services ?? {}).length > 0 && (
                <p className="mt-2 text-xs text-amber-700">
                  {t('agents.oncall.unmappedServices', { list: listJoin(Object.entries(r.unmapped_services).map(([k, v]) => `${k}(${v})`), zh) })}
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('agents.oncall.chatopsTitle')}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="mb-2 flex justify-end">
                <CopyButton text={r.chatops_text} label={t('agents.oncall.copyChatops')} size="xs" />
              </div>
              <pre className="log-block max-h-72 overflow-y-auto whitespace-pre-wrap">{r.chatops_text}</pre>
            </CardContent>
          </Card>
        </div>
      )}

      <Separator />

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{t('agents.oncall.historyTitle', { count: history.length })}</CardTitle>
        </CardHeader>
        <CardContent>
          <StateGate
            loading={historyQuery.isLoading}
            error={historyQuery.isError ? errDetail(historyQuery.error) : null}
            onRetry={() => historyQuery.refetch()}
            isEmpty={history.length === 0}
            empty={t('agents.oncall.noHistory')}
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
                      <span className="text-muted-foreground">{t('agents.oncall.historyAlerts', { count: h.event_count, critical: h.critical_count, warning: h.warning_count })}</span>
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
                              <p className="mb-1 font-medium text-muted-foreground">{t('agents.oncall.impactTitle')}</p>
                              <p className="leading-relaxed">{rep.impact_summary}</p>
                            </div>
                            <div>
                              <p className="mb-1 font-medium text-muted-foreground">{t('agents.oncall.actions')}</p>
                              <ol className="list-decimal space-y-1 pl-5">
                                {rep.actions.length === 0 && <li className="list-none text-muted-foreground">{t('agents.none')}</li>}
                                {rep.actions.map((a, i) => <li key={i}>{a}</li>)}
                              </ol>
                            </div>
                            <div>
                              <p className="mb-1 font-medium text-muted-foreground">{t('agents.oncall.owners')}</p>
                              <div className="flex flex-wrap gap-1.5">
                                {rep.owners_to_notify.length === 0 && <span className="text-muted-foreground">{t('agents.none')}</span>}
                                {rep.owners_to_notify.map((o) => <Badge key={o} variant="secondary">{o}</Badge>)}
                              </div>
                            </div>
                            {rep.quality && <QualityMetricsCard quality={rep.quality} title={t('agents.oncall.qualityTitleShort')} />}
                          </>
                        ) : (
                          <p className="text-muted-foreground">{t('agents.oncall.noStructuredDetail')}</p>
                        )}
                        <div>
                          <p className="mb-1 font-medium text-muted-foreground">{t('agents.oncall.cmdbTitle', { count: systems.length })}</p>
                          {systems.length === 0 ? (
                            <p className="text-muted-foreground">{t('agents.oncall.noCmdb')}</p>
                          ) : (
                            <div className="overflow-hidden rounded-md border">
                              <table className="w-full table-fixed">
                                <thead>
                                  <tr className="border-b bg-muted/60 text-left text-muted-foreground">
                                    <th className="w-36 px-3 py-1.5 font-medium">{t('agents.oncall.thSystem')}</th>
                                    <th className="w-16 px-3 py-1.5 font-medium">{t('agents.oncall.thAlerts')}</th>
                                    <th className="w-24 px-3 py-1.5 font-medium">{t('agents.oncall.thSeverity')}</th>
                                    <th className="w-36 px-3 py-1.5 font-medium">{t('agents.oncall.thOwners')}</th>
                                    <th className="px-3 py-1.5 font-medium">{t('agents.oncall.thServices')}</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {systems.map((s) => (
                                    <tr key={s.system} className="border-b last:border-b-0">
                                      <td className="px-3 py-1.5 font-medium">{s.system}</td>
                                      <td className="px-3 py-1.5 font-mono">{s.event_count}</td>
                                      <td className="px-3 py-1.5"><SeverityBadge severity={s.max_severity} /></td>
                                      <td className="px-3 py-1.5 truncate" title={listJoin(s.owners, zh)}>{listJoin(s.owners, zh) || '—'}</td>
                                      <td className="px-3 py-1.5 truncate" title={listJoin(s.services.map((x) => x.service), zh)}>
                                        {listJoin(s.services.map((x) => x.service), zh)}
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
                            <p className="font-medium text-muted-foreground">{t('agents.oncall.chatopsText')}</p>
                            {chatops && <CopyButton text={chatops} label={t('agents.oncall.copyText')} size="xs" />}
                          </div>
                          {chatops ? (
                            <pre className="log-block max-h-60 overflow-y-auto whitespace-pre-wrap">{chatops}</pre>
                          ) : (
                            <p className="text-muted-foreground">{t('agents.none')}</p>
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
  const { t } = useTranslation();
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
        <Label className="mb-1 text-xs">{t('agents.sessions.typeLabel')}</Label>
        <Select value={sessionType} onValueChange={setSessionType}>
          <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('agents.sessionType.all')}</SelectItem>
            {SESSION_TYPES.map((v) => (
              <SelectItem key={v} value={v}>{t(`agents.sessionType.${v}`)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <StateGate
        loading={query.isLoading}
        error={query.isError ? errDetail(query.error) : null}
        onRetry={() => query.refetch()}
        isEmpty={sessions.length === 0}
        empty={t('agents.sessions.empty')}
        emptyHint={t('agents.sessions.emptyHint')}
      >
        <div className="space-y-2">
          {sessions.map((s: AgentSession) => (
            <div key={s.id} className="rounded-md border">
              <button
                className="flex w-full flex-wrap items-center gap-2 px-3 py-2.5 text-left text-xs hover:bg-accent/60"
                onClick={() => setExpanded(expanded === s.id ? null : s.id)}
              >
                <span className="font-mono font-medium">#{s.id}</span>
                <Badge variant="secondary">{t(`agents.sessionType.${s.session_type}`, { defaultValue: s.session_type })}</Badge>
                <StatusBadge status={s.status} />
                {s.event_id && <span className="text-muted-foreground">{t('agents.sessions.eventRef', { id: s.event_id })}</span>}
                <span className="font-mono text-muted-foreground">{s.model}</span>
                {s.iterations !== null && <Badge variant="outline">{t('agents.sessions.rounds', { n: s.iterations })}</Badge>}
                {s.duration_ms !== null && <span className="text-muted-foreground">{Math.round(s.duration_ms)} ms</span>}
                <span className="ml-auto text-muted-foreground">{s.actor} · {fmtTime(s.created_at)}</span>
              </button>
              {expanded === s.id && (
                <div className="space-y-3 border-t px-3 py-3">
                  {s.summary && <p className="text-xs leading-relaxed">{s.summary}</p>}
                  {s.error_message && <p className="text-xs text-red-600">{t('agents.trace.error', { error: s.error_message })}</p>}
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-muted-foreground">{t('agents.sessions.traceTitle')}</p>
                    <TraceSteps steps={s.tool_trace ?? []} />
                  </div>
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-muted-foreground">{t('agents.sessions.resultTitle')}</p>
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
  const { t } = useTranslation();
  const [q, setQ] = useState('');
  const [input, setInput] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => setQ(input.trim()), 300);
    return () => clearTimeout(timer);
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
          placeholder={t('agents.cmdb.searchPlaceholder')}
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
      </div>

      <StateGate
        loading={query.isLoading}
        error={query.isError ? errDetail(query.error) : null}
        onRetry={() => query.refetch()}
        isEmpty={assets.length === 0}
        empty={t('agents.cmdb.empty')}
      >
        <div className="overflow-x-auto rounded-md border text-xs">
          <table className="w-full min-w-[860px] table-fixed">
            <thead>
              <tr className="border-b bg-muted/60 text-left text-muted-foreground">
                <th className="w-36 px-3 py-2 font-medium">{t('agents.cmdb.thHostname')}</th>
                <th className="w-32 px-3 py-2 font-medium">IP</th>
                <th className="w-36 px-3 py-2 font-medium">{t('agents.cmdb.thSystem')}</th>
                <th className="w-36 px-3 py-2 font-medium">{t('agents.cmdb.thService')}</th>
                <th className="w-24 px-3 py-2 font-medium">{t('agents.cmdb.thCluster')}</th>
                <th className="w-20 px-3 py-2 font-medium">{t('agents.cmdb.thEnv')}</th>
                <th className="w-24 px-3 py-2 font-medium">{t('agents.cmdb.thOwner')}</th>
                <th className="px-3 py-2 font-medium">{t('agents.cmdb.thDeps')}</th>
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
  const { t } = useTranslation();
  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <Bot className="h-5 w-5" />
          {t('agents.page.title')}
        </h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {t('agents.page.subtitle')}
        </p>
      </div>

      <Tabs defaultValue="diagnose">
        <TabsList>
          <TabsTrigger value="diagnose" className="gap-1.5"><BrainCircuit className="h-3.5 w-3.5" />{t('agents.page.tabDiagnose')}</TabsTrigger>
          <TabsTrigger value="governance" className="gap-1.5"><Users className="h-3.5 w-3.5" />{t('agents.page.tabGovernance')}</TabsTrigger>
          <TabsTrigger value="oncall" className="gap-1.5"><FileText className="h-3.5 w-3.5" />{t('agents.page.tabOncall')}</TabsTrigger>
          <TabsTrigger value="sessions" className="gap-1.5"><History className="h-3.5 w-3.5" />{t('agents.page.tabSessions')}</TabsTrigger>
          <TabsTrigger value="cmdb" className="gap-1.5"><Database className="h-3.5 w-3.5" />{t('agents.page.tabCmdb')}</TabsTrigger>
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
