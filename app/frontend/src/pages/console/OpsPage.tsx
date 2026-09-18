/** C10 审计日志 + 配置中心：审计链路检索、before/after 快照展示、配置项编辑（含 LLM/Embedding 分组管理与密钥脱敏）。 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, KeyRound, Save } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  consoleApi,
  errDetail,
  type AuditLog,
  type ConfigItem,
  type KbHealthReport,
  type KbHealthRiskCase,
  type LifecyclePatrolResult,
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

function AuditTab() {
  const { t } = useTranslation();
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
        <Input className="h-9 w-44 text-xs" placeholder={t('ops.audit.actionPlaceholder')} value={action} onChange={(e) => setAction(e.target.value)} />
        <Input className="h-9 w-44 text-xs" placeholder={t('ops.audit.actorPlaceholder')} value={actor} onChange={(e) => setActor(e.target.value)} />
        <Input className="h-9 w-44 text-xs" placeholder={t('ops.audit.targetPlaceholder')} value={targetType} onChange={(e) => setTargetType(e.target.value)} />
        <span className="text-xs text-muted-foreground">{t('ops.audit.total', { count: query.data?.total ?? 0 })}</span>
      </div>

      <StateGate
        loading={query.isLoading}
        error={query.isError ? errDetail(query.error) : null}
        onRetry={() => query.refetch()}
        isEmpty={items.length === 0}
        empty={t('ops.audit.empty')}
        emptyHint={t('ops.audit.emptyHint')}
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
                      <Badge variant="secondary">{t(`ops.audit.actions.${log.action}`, { defaultValue: log.action })}</Badge>
                      <span className="font-medium">{log.actor}</span>
                      <span className="text-muted-foreground">
                        {t(`ops.audit.targets.${log.target_type}`, { defaultValue: log.target_type })} · {log.target_id}
                      </span>
                      <span className="ml-auto text-muted-foreground">{fmtTime(log.created_at)}</span>
                    </button>
                    {open && (
                      <div className="mt-2.5 grid gap-2.5 lg:grid-cols-2">
                        <div>
                          <p className="mb-1 text-xs font-medium text-muted-foreground">{t('ops.audit.beforeSnapshot')}</p>
                          {log.before ? <JsonPre data={log.before} /> : <p className="text-xs text-muted-foreground">{t('ops.audit.none')}</p>}
                        </div>
                        <div>
                          <p className="mb-1 text-xs font-medium text-muted-foreground">{t('ops.audit.afterSnapshot')}</p>
                          {log.after ? <JsonPre data={log.after} /> : <p className="text-xs text-muted-foreground">{t('ops.audit.none')}</p>}
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

const HEALTH_TONE: Record<'green' | 'yellow' | 'red', { labelKey: string; badge: string }> = {
  green: { labelKey: 'ops.health.toneGreen', badge: 'border-teal-500/30 bg-teal-500/15 text-teal-700 dark:text-teal-400' },
  yellow: { labelKey: 'ops.health.toneYellow', badge: 'border-amber-500/30 bg-amber-500/15 text-amber-700 dark:text-amber-400' },
  red: { labelKey: 'ops.health.toneRed', badge: 'border-red-500/30 bg-red-500/15 text-red-700 dark:text-red-400' },
};

function HealthBadge({ level }: { level: 'green' | 'yellow' | 'red' }) {
  const { t } = useTranslation();
  const tone = HEALTH_TONE[level] ?? HEALTH_TONE.green;
  return (
    <Badge variant="outline" className={cn('text-[10px]', tone.badge)}>
      {t(tone.labelKey)}
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
  const { t } = useTranslation();
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
        <CardTitle className="text-sm">{t('ops.health.qualityTitle')}</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatItem label={t('ops.health.trustIndex')} value={fmt(q?.trust_index_avg, fmtPercent)} />
        <StatItem label={t('ops.health.qualityOkRate')} value={fmt(q?.quality_ok_rate, fmtPercent)} />
        <StatItem label={t('ops.health.retrievalSuccess')} value={fmt(q?.retrieval?.success_rate, fmtPercent)} />
        <StatItem label={t('ops.health.generationSuccess')} value={fmt(q?.generation?.success_rate, fmtPercent)} />
      </CardContent>
    </Card>
  );
}

function KbHealthTab() {
  const { t, i18n } = useTranslation();
  const zh = i18n.language?.startsWith('zh');
  const listJoiner = zh ? '、' : ', ';
  const perms = usePermissions();
  const queryClient = useQueryClient();
  // 归档 / 巡检 / 死信重放 / 到期重试后端均要求 kb_admin 及以上，前端以等价的 can_publish 门控
  const canOperate = !!perms?.can_publish;
  const [patrolPreview, setPatrolPreview] = useState<LifecyclePatrolResult | null>(null);

  const query = useQuery({
    queryKey: ['kb-health'],
    queryFn: () => consoleApi.getKbHealth(),
    refetchInterval: 60_000,
  });

  const invalidateHealth = () => {
    queryClient.invalidateQueries({ queryKey: ['kb-health'] });
    queryClient.invalidateQueries({ queryKey: ['dashboard'] });
  };

  const archiveMutation = useMutation({
    mutationFn: (caseId: string) => consoleApi.archiveCase(caseId),
    onSuccess: (_res, caseId) => {
      toast.success(t('ops.health.toastArchived', { id: caseId }));
      invalidateHealth();
    },
    onError: (e) => toast.error(t('ops.health.toastArchiveFailed', { error: errDetail(e) })),
  });

  const retryMutation = useMutation({
    mutationFn: () => consoleApi.retryDueTasks(20),
    onSuccess: (res) => {
      if (res.executed > 0) {
        toast.success(t('ops.health.toastRetryExecuted', { executed: res.executed, succeeded: res.succeeded, dead: res.dead_lettered }));
      } else {
        toast(t('ops.health.toastRetryNone'));
      }
      invalidateHealth();
    },
    onError: (e) => toast.error(t('ops.health.toastRetryFailed', { error: errDetail(e) })),
  });

  const replayDeadMutation = useMutation({
    mutationFn: async () => {
      const dead = await consoleApi.replayDeadTasks();
      const retry = await consoleApi.retryDueTasks(20);
      return { requeued: dead.requeued, retry };
    },
    onSuccess: (res) => {
      toast.success(
        t('ops.health.toastReplayed', { count: res.requeued, succeeded: res.retry.succeeded, pending: res.retry.pending_remaining }),
      );
      invalidateHealth();
    },
    onError: (e) => toast.error(t('ops.health.toastReplayFailed', { error: errDetail(e) })),
  });

  const patrolMutation = useMutation({
    mutationFn: (dryRun: boolean) => consoleApi.lifecyclePatrol(dryRun),
    onSuccess: (res, dryRun) => {
      if (dryRun) {
        setPatrolPreview(res);
        if (res.candidates.length === 0) {
          toast.success(t('ops.health.toastPatrolNoCandidates', { expire: res.expire_days }));
        }
      } else {
        setPatrolPreview(null);
        toast.success(
          res.archived.length > 0
            ? t('ops.health.toastPatrolArchived', { count: res.archived.length, list: res.archived.join(listJoiner) })
            : t('ops.health.toastPatrolNone'),
        );
        invalidateHealth();
      }
    },
    onError: (e) => toast.error(t('ops.health.toastPatrolFailed', { error: errDetail(e) })),
  });

  if (query.isLoading) return <LoadingBlock rows={6} />;
  if (query.isError || !query.data) {
    return <EmptyBlock title={t('ops.health.loadFailedTitle')} hint={errDetail(query.error)} />;
  }

  const report: KbHealthReport = query.data;
  const { summary, sync, content_safety, aging } = report;

  /** 逐案处置渲染：同步死信 → 重放；未确认 → 重试；统一提供归档出口；归档为生命周期终态 */
  const renderCaseActions = (c: KbHealthRiskCase) => {
    if (c.status === 'archived') {
      return <span className="text-muted-foreground">{t('ops.health.terminalState')}</span>;
    }
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        {c.sync_dead > 0 && (
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[10px]"
            disabled={!canOperate || replayDeadMutation.isPending}
            onClick={() => replayDeadMutation.mutate()}
          >
            {t('ops.health.replayDeadAction')}
          </Button>
        )}
        {!c.sync_verified && c.sync_dead === 0 && (
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[10px]"
            disabled={!canOperate || retryMutation.isPending}
            onClick={() => retryMutation.mutate()}
          >
            {t('ops.health.retrySyncAction')}
          </Button>
        )}
        {(c.content_risk === 'high' || c.content_risk === 'high_overridden') && (
          <span className="text-[10px] leading-snug text-muted-foreground">{t('ops.health.contentRiskHint')}</span>
        )}
        <Button
          size="sm"
          variant="destructive"
          className="h-6 px-2 text-[10px]"
          disabled={!canOperate || archiveMutation.isPending}
          onClick={() => archiveMutation.mutate(c.case_id)}
        >
          {t('ops.health.archiveAction')}
        </Button>
        {!canOperate && <span className="text-[10px] text-muted-foreground">{t('ops.health.needKbAdmin')}</span>}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        {t('ops.health.introLine1')}
      </p>
      <p className="text-xs text-muted-foreground">
        {t('ops.health.introLine2')}
      </p>
      <QualityOverviewCard />
      <div className="grid gap-4 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t('ops.health.gradeTitle')}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <div className="grid grid-cols-3 gap-2">
              <StatItem label={t('ops.health.toneGreen')} value={summary.green} tone="text-teal-600 dark:text-teal-400" />
              <StatItem label={t('ops.health.toneYellow')} value={summary.yellow} tone="text-amber-600 dark:text-amber-400" />
              <StatItem label={t('ops.health.toneRed')} value={summary.red} tone="text-red-600 dark:text-red-400" />
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
              {t('ops.health.healthRateLine', {
                rate: summary.health_rate !== null ? fmtPercent(summary.health_rate) : '—',
                total: summary.total,
                active: summary.active,
                archived: summary.archived,
              })}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t('ops.health.syncTitle')}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <div className="grid grid-cols-2 gap-2">
              <StatItem label={t('ops.health.verified')} value={sync.verified_cases} tone="text-teal-600 dark:text-teal-400" />
              <StatItem label={t('ops.health.unverified')} value={sync.unverified_cases} tone="text-amber-600 dark:text-amber-400" />
              <StatItem label={t('ops.health.pendingTasks')} value={sync.pending_tasks} />
              <StatItem
                label={t('ops.health.deadTasks')}
                value={sync.dead_tasks}
                tone={sync.dead_tasks > 0 ? 'text-red-600 dark:text-red-400' : undefined}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {t('ops.health.syncHint')}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t('ops.health.contentTitle')}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <div className="grid grid-cols-2 gap-2">
              <StatItem label={t('ops.health.scans')} value={content_safety.scans} />
              <StatItem
                label={t('ops.health.blocked')}
                value={content_safety.blocked}
                tone={content_safety.blocked > 0 ? 'text-red-600 dark:text-red-400' : undefined}
              />
              <StatItem label={t('ops.health.flagged')} value={content_safety.flagged} />
              <StatItem label={t('ops.health.overrides')} value={content_safety.overrides} />
            </div>
            <p className="text-xs text-muted-foreground">
              {t('ops.health.contentHint', { version: content_safety.last_rule_version ?? '—' })}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t('ops.health.agingTitle')}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <div className="grid grid-cols-2 gap-2">
              <StatItem label={t('ops.health.staleCandidates')} value={aging.stale_candidates} />
              <StatItem label={t('ops.health.avgAge')} value={aging.avg_age_days !== null ? t('ops.health.daysUnit', { n: aging.avg_age_days }) : '—'} />
            </div>
            <p className="text-xs text-muted-foreground">
              {t('ops.health.agingHint', { expire: report.expire_days, unconditional: report.unconditional_expire_days })}
              {aging.oldest?.age_days !== null && aging.oldest
                ? t('ops.health.oldestCase', { id: aging.oldest.case_id, days: aging.oldest.age_days })
                : ''}
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
            {t('ops.health.riskListTitle')}
            <span className="ml-auto flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="h-7 px-2 text-xs"
                disabled={!canOperate || retryMutation.isPending}
                onClick={() => retryMutation.mutate()}
              >
                {t('ops.health.retrySync')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 px-2 text-xs"
                disabled={!canOperate || replayDeadMutation.isPending}
                onClick={() => replayDeadMutation.mutate()}
              >
                {t('ops.health.replayDead')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 px-2 text-xs"
                disabled={!canOperate || patrolMutation.isPending}
                onClick={() => patrolMutation.mutate(true)}
              >
                {t('ops.health.patrolPreviewBtn')}
              </Button>
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {patrolPreview && (
            <div className="mb-3 space-y-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
              <p className="font-medium text-amber-700 dark:text-amber-400">
                {t('ops.health.patrolPreviewTitle', { count: patrolPreview.candidates.length, expire: patrolPreview.expire_days })}
                {patrolPreview.candidates.length === 0 && t('ops.health.patrolNoCandidates')}
              </p>
              {patrolPreview.candidates.length > 0 && (
                <p className="break-all text-muted-foreground">
                  {patrolPreview.candidates.map((c) => t('ops.health.patrolCandidateItem', { id: c.case_id, score: c.feedback_score ?? '—' })).join(listJoiner)}
                </p>
              )}
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  className="h-7 px-2 text-xs"
                  disabled={!canOperate || patrolMutation.isPending || patrolPreview.candidates.length === 0}
                  onClick={() => patrolMutation.mutate(false)}
                >
                  {patrolMutation.isPending ? t('ops.health.archiving') : t('ops.health.confirmArchive', { count: patrolPreview.candidates.length })}
                </Button>
                <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => setPatrolPreview(null)}>
                  {t('ops.health.cancel')}
                </Button>
              </div>
            </div>
          )}
          {report.risk_cases.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {t('ops.health.noRiskCases')}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1000px] text-left text-xs">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="py-2 pr-3 font-medium">{t('ops.health.thHealth')}</th>
                    <th className="py-2 pr-3 font-medium">{t('ops.health.thCase')}</th>
                    <th className="py-2 pr-3 font-medium">{t('ops.health.thService')}</th>
                    <th className="py-2 pr-3 font-medium">{t('ops.health.thStatus')}</th>
                    <th className="py-2 pr-3 font-medium">{t('ops.health.thFeedback')}</th>
                    <th className="py-2 pr-3 font-medium">{t('ops.health.thAge')}</th>
                    <th className="py-2 pr-3 font-medium">{t('ops.health.thSync')}</th>
                    <th className="py-2 pr-3 font-medium">{t('ops.health.thContentRisk')}</th>
                    <th className="py-2 pr-3 font-medium">{t('ops.health.thReasons')}</th>
                    <th className="py-2 font-medium">{t('ops.health.thAction')}</th>
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
                      <td className="py-2.5 pr-3">{c.age_days !== null ? t('ops.health.daysUnit', { n: c.age_days }) : '—'}</td>
                      <td className="py-2.5 pr-3">
                        {c.sync_dead > 0 ? (
                          <span className="text-red-600 dark:text-red-400">{t('ops.health.deadLabel', { count: c.sync_dead })}</span>
                        ) : c.sync_pending > 0 ? (
                          <span className="text-amber-600 dark:text-amber-400">{t('ops.health.pendingLabel', { count: c.sync_pending })}</span>
                        ) : c.sync_verified ? (
                          <span className="text-teal-600 dark:text-teal-400">{t('ops.health.verifiedLabel')}</span>
                        ) : (
                          <span className="text-amber-600 dark:text-amber-400">{t('ops.health.unverifiedLabel')}</span>
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
                      <td className="py-2.5">{renderCaseActions(c)}</td>
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

/** 密钥类配置：后端仅返回脱敏值，保存空串表示清除（Agent 独立 Key 留空继承全局） */
const SECRET_KEYS = new Set([
  'llm_api_key',
  'embedding_api_key',
  'diagnose_llm_api_key',
  'kb_governance_llm_api_key',
  'oncall_llm_api_key',
]);

/** Agent 独立接入配置键（provider / Base URL / API Key，留空逐项继承全局） */
const AGENT_ACCESS_KEYS = new Set([
  'diagnose_llm_provider',
  'diagnose_llm_base_url',
  'diagnose_llm_api_key',
  'kb_governance_llm_provider',
  'kb_governance_llm_base_url',
  'kb_governance_llm_api_key',
  'oncall_llm_provider',
  'oncall_llm_base_url',
  'oncall_llm_api_key',
]);
/** Agent 独立 provider 用下拉选择：哨兵值表示“留空继承全局”（保存时映射为空串） */
const AGENT_PROVIDER_SELECT_KEYS = new Set([
  'diagnose_llm_provider',
  'kb_governance_llm_provider',
  'oncall_llm_provider',
]);
const INHERIT_SENTINEL = '__inherit__';

/** 下拉选项：labelKey 指向 i18n 文案键 */
const SELECT_OPTIONS: Record<string, { value: string; labelKey: string }[]> = {
  llm_provider: [
    { value: 'atoms_hub', labelKey: 'ops.config.selectOptions.atomsHub' },
    { value: 'openai_compatible', labelKey: 'ops.config.selectOptions.openaiCompatible' },
  ],
  diagnose_llm_provider: [
    { value: INHERIT_SENTINEL, labelKey: 'ops.config.selectOptions.inheritGlobal' },
    { value: 'atoms_hub', labelKey: 'ops.config.selectOptions.atomsHub' },
    { value: 'openai_compatible', labelKey: 'ops.config.selectOptions.openaiCompatible' },
  ],
  kb_governance_llm_provider: [
    { value: INHERIT_SENTINEL, labelKey: 'ops.config.selectOptions.inheritGlobal' },
    { value: 'atoms_hub', labelKey: 'ops.config.selectOptions.atomsHub' },
    { value: 'openai_compatible', labelKey: 'ops.config.selectOptions.openaiCompatible' },
  ],
  oncall_llm_provider: [
    { value: INHERIT_SENTINEL, labelKey: 'ops.config.selectOptions.inheritGlobal' },
    { value: 'atoms_hub', labelKey: 'ops.config.selectOptions.atomsHub' },
    { value: 'openai_compatible', labelKey: 'ops.config.selectOptions.openaiCompatible' },
  ],
  approval_mode: [
    { value: 'OFF', labelKey: 'ops.config.selectOptions.approvalOff' },
    { value: 'SINGLE_REVIEW', labelKey: 'ops.config.selectOptions.approvalSingle' },
    { value: 'MULTI_LEVEL', labelKey: 'ops.config.selectOptions.approvalMulti' },
  ],
  default_role: [
    { value: 'viewer', labelKey: 'ops.config.selectOptions.roleViewer' },
    { value: 'operator', labelKey: 'ops.config.selectOptions.roleOperator' },
    { value: 'sre', labelKey: 'ops.config.selectOptions.roleSre' },
    { value: 'approver', labelKey: 'ops.config.selectOptions.roleApprover' },
    { value: 'kb_admin', labelKey: 'ops.config.selectOptions.roleKbAdmin' },
  ],
};

const CONFIG_GROUPS: { id: string; keys: string[]; testable?: boolean; agent?: string }[] = [
  {
    id: 'global',
    keys: ['llm_provider', 'llm_base_url', 'llm_api_key', 'llm_model', 'llm_temperature', 'llm_timeout_seconds'],
    testable: true,
  },
  {
    id: 'diagnose',
    keys: ['diagnose_llm_provider', 'diagnose_llm_base_url', 'diagnose_llm_api_key', 'diagnose_llm_model', 'diagnose_llm_timeout_seconds', 'diagnose_temperature', 'diagnose_time_budget_seconds'],
    testable: true,
    agent: 'diagnose',
  },
  {
    id: 'governance',
    keys: ['kb_governance_llm_provider', 'kb_governance_llm_base_url', 'kb_governance_llm_api_key', 'kb_governance_llm_model', 'kb_governance_temperature', 'kb_governance_llm_timeout_seconds'],
    testable: true,
    agent: 'kb_governance',
  },
  {
    id: 'oncall',
    keys: ['oncall_llm_provider', 'oncall_llm_base_url', 'oncall_llm_api_key', 'oncall_llm_model', 'oncall_temperature', 'oncall_llm_timeout_seconds'],
    testable: true,
    agent: 'oncall',
  },
  {
    id: 'embedding',
    keys: ['embedding_base_url', 'embedding_api_key', 'embedding_model'],
  },
  {
    id: 'policy',
    keys: ['approval_mode', 'confidence_threshold', 'rerank_weight_json'],
  },
  {
    id: 'system',
    keys: ['feature_flags_json', 'default_role', 'role_bindings_json'],
  },
];

function TestResultBox({ result }: { result: LlmTestResult }) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-2.5 rounded-md border bg-muted/40 p-3 text-xs lg:grid-cols-2">
      <div>
        <p className="flex flex-wrap items-center gap-1.5 font-medium">
          <Badge variant={result.chat.ok ? 'default' : 'destructive'} className="text-[10px]">
            {result.chat.ok ? t('ops.config.testResult.chatOk') : t('ops.config.testResult.chatFailed')}
          </Badge>
          {result.chat.agent && <Badge variant="outline" className="text-[10px]">{t('ops.config.testResult.agentBadge', { agent: result.chat.agent })}</Badge>}
          {result.chat.access_source && (
            <Badge variant={result.chat.access_source === 'agent' ? 'default' : 'outline'} className="text-[10px]">
              {result.chat.access_source === 'agent' ? t('ops.config.testResult.sourceAgent') : t('ops.config.testResult.sourceGlobal')}
            </Badge>
          )}
          {result.chat.model && <span className="font-mono text-muted-foreground">{result.chat.model}</span>}
          {typeof result.chat.resolved_timeout_seconds === 'number' && (
            <span className="text-muted-foreground">{t('ops.config.testResult.timeoutLabel', { seconds: result.chat.resolved_timeout_seconds })}</span>
          )}
          {result.chat.resolved_base_url && (
            <span className="font-mono text-muted-foreground">{result.chat.resolved_base_url}</span>
          )}
          {typeof result.chat.latency_ms === 'number' && <span className="text-muted-foreground">{result.chat.latency_ms}ms</span>}
        </p>
        {result.chat.ok ? (
          result.chat.sample && <p className="mt-1 text-muted-foreground">{t('ops.config.testResult.sampleReply', { sample: result.chat.sample })}</p>
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
            {!result.embedding.enabled ? t('ops.config.testResult.embDisabled') : result.embedding.ok ? t('ops.config.testResult.embOk') : t('ops.config.testResult.embFailed')}
          </Badge>
          {result.embedding.model && <span className="font-mono text-muted-foreground">{result.embedding.model}</span>}
          {typeof result.embedding.dims === 'number' && <span className="text-muted-foreground">{t('ops.config.testResult.dimsLabel', { dims: result.embedding.dims })}</span>}
          {result.embedding.enabled && typeof result.embedding.latency_ms === 'number' && (
            <span className="text-muted-foreground">{result.embedding.latency_ms}ms</span>
          )}
        </p>
        <p className="mt-1 break-all text-muted-foreground">
          {result.embedding.ok ? t('ops.config.testResult.embEnabledNote') : result.embedding.error || result.embedding.note}
        </p>
      </div>
    </div>
  );
}

function ConfigTab() {
  const { t } = useTranslation();
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

  const labelFor = (key: string) => t(`ops.config.labels.${key}`, { defaultValue: key });

  const updateMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) => consoleApi.updateConfig(key, value),
    onSuccess: (res) => {
      toast.success(t('ops.config.toastSaved', { key: res.key }));
      setDrafts((s) => {
        const next = { ...s };
        delete next[res.key];
        return next;
      });
      queryClient.invalidateQueries({ queryKey: ['configs'] });
      queryClient.invalidateQueries({ queryKey: ['permissions'] });
    },
    onError: (e) => toast.error(t('ops.config.toastSaveFailed', { error: errDetail(e) })),
  });

  const testMutation = useMutation({
    mutationFn: (agent?: string) => consoleApi.testLlmConfig(agent),
    onSuccess: (res: LlmTestResult) => {
      setTestResult(res);
      if (res.chat.ok) toast.success(t('ops.config.toastChatOk', { model: res.chat.model ?? '-' }));
      else toast.error(t('ops.config.toastChatFailed', { error: res.chat.error ?? t('ops.config.unknownError') }));
    },
    onError: (e) => toast.error(t('ops.config.toastTestFailed', { error: errDetail(e) })),
  });

  if (!canManage) {
    return (
      <EmptyBlock
        title={t('ops.config.noAccessTitle')}
        hint={t('ops.config.noAccessHint', { role: perms?.role_label ?? t('ops.config.unknownError') })}
      />
    );
  }
  if (query.isLoading) return <LoadingBlock rows={5} />;
  if (query.isError) {
    return (
      <EmptyBlock
        title={t('ops.config.loadFailedTitle')}
        hint={errDetail(query.error)}
      />
    );
  }

  const items = query.data?.items ?? [];
  const byKey = new Map(items.map((cfg: ConfigItem) => [cfg.key, cfg]));

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        {t('ops.config.intro')}
        {!canManage && ` ${t('ops.config.readOnlySuffix', { role: perms?.role_label })}`}
      </p>
      {CONFIG_GROUPS.map((group) => (
        <Card key={group.id}>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              {t(`ops.config.groups.${group.id}.title`)}
              {group.testable && (
                <Button
                  size="sm"
                  variant="outline"
                  className="ml-auto h-7 px-2.5 text-xs"
                  disabled={!canManage || testMutation.isPending}
                  onClick={() => testMutation.mutate(group.agent)}
                >
                  <Activity className="mr-1 h-3.5 w-3.5" />
                  {testMutation.isPending ? t('ops.config.testing') : t('ops.config.testBtn')}
                </Button>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">{t(`ops.config.groups.${group.id}.hint`)}</p>
            {group.testable && testResult && group.agent === testResult.chat.agent && (
              <TestResultBox result={testResult} />
            )}
            <div className="divide-y rounded-md border">
              {group.keys.map((key) => {
                const cfg = byKey.get(key);
                if (!cfg) return null;
                const isSecret = SECRET_KEYS.has(key);
                const isInherit = AGENT_ACCESS_KEYS.has(key);
                const isProviderSelect = AGENT_PROVIDER_SELECT_KEYS.has(key);
                // provider 下拉：空值显示为“继承全局”哨兵项，保存时映射回空串
                const storedValue = isProviderSelect && !cfg.value ? INHERIT_SENTINEL : cfg.value;
                const draft = drafts[key] ?? (isSecret ? '' : storedValue);
                const savedValue = isProviderSelect && draft === INHERIT_SENTINEL ? '' : draft;
                const changed = isSecret ? draft.trim().length > 0 : savedValue !== cfg.value;
                const options = SELECT_OPTIONS[key];
                const placeholder = isSecret
                  ? cfg.value
                    ? t('ops.config.secretConfigured', { value: cfg.value })
                    : isInherit
                      ? t('ops.config.inheritNotConfigured')
                      : t('ops.config.secretNotConfigured')
                  : isInherit && !cfg.value
                    ? t('ops.config.inheritPlaceholder')
                    : '';
                return (
                  <div key={key} className="flex flex-col gap-1.5 px-3 py-2.5 sm:flex-row sm:items-center sm:gap-3">
                    <div className="sm:w-60 sm:shrink-0">
                      <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium">
                        {isSecret && <KeyRound className="h-3 w-3 text-muted-foreground" />}
                        {labelFor(key)}
                        {cfg.is_default && <Badge variant="outline" className="text-[10px]">{t('ops.config.defaultValueBadge')}</Badge>}
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
                                {t(o.labelKey)}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Input
                          type={isSecret ? 'password' : 'text'}
                          className={cn('h-9 font-mono text-xs', changed && 'border-primary')}
                          value={draft}
                          placeholder={placeholder}
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
                            if (window.confirm(isInherit ? t('ops.config.confirmClearInherit', { label: labelFor(key) }) : t('ops.config.confirmClearGlobal', { label: labelFor(key) }))) {
                              updateMutation.mutate({ key, value: '' });
                            }
                          }}
                        >
                          {t('ops.config.clearBtn')}
                        </Button>
                      )}
                      <Button
                        size="sm"
                        className="shrink-0"
                        disabled={!canManage || !changed || updateMutation.isPending}
                        onClick={() => updateMutation.mutate({ key, value: savedValue })}
                      >
                        <Save className="mr-1.5 h-3.5 w-3.5" />
                        {updateMutation.isPending ? t('ops.config.saving') : t('ops.config.save')}
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
  const { t } = useTranslation();
  const perms = usePermissions();
  const canManageConfig = !!perms?.can_manage_config;
  // 审计与配置整页仅系统管理员可见（侧边栏入口同步隐藏，防止直达 URL 访问）
  if (!canManageConfig) {
    return (
      <div className="space-y-4">
        <h1 className="text-lg font-semibold tracking-tight">{t('ops.title')}</h1>
        <EmptyBlock
          title={t('ops.noAccessTitle')}
          hint={t('ops.noAccessHint', { role: perms?.role_label ?? t('ops.config.unknownError') })}
        />
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{t('ops.title')}</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {t('ops.subtitle')}
        </p>
      </div>
      <Tabs defaultValue="audit">
        <TabsList>
          <TabsTrigger value="audit">{t('ops.tabAudit')}</TabsTrigger>
          <TabsTrigger value="kb-health">{t('ops.tabKbHealth')}</TabsTrigger>
          {canManageConfig && <TabsTrigger value="config">{t('ops.tabConfig')}</TabsTrigger>}
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
