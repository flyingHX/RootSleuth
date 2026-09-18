/** C3 运营总览：核心指标、告警分布、TOP 榜、依赖健康与待办。文案经 i18n 双语渲染。 */
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowRight, BellRing, BookOpenText, ClipboardCheck, HeartPulse } from 'lucide-react';
import { consoleApi, errDetail, type DashboardData } from '@/lib/console-api';
import { ErrorBlock, SeverityBadge, StateGate, fmtPercent, fmtTime } from '@/components/console/shared';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

function MetricStrip({ metrics }: { metrics: NonNullable<ReturnType<typeof useDashboardQuery>['data']>['metrics'] }) {
  const { t } = useTranslation();
  const items = [
    { label: t('index.metrics.totalEvents'), value: String(metrics.total_events), hint: t('index.metrics.totalEventsHint') },
    { label: t('index.metrics.noiseReduction'), value: fmtPercent(metrics.noise_reduction), hint: t('index.metrics.noiseReductionHint') },
    { label: t('index.metrics.unknownRate'), value: fmtPercent(metrics.unknown_rate), hint: t('index.metrics.unknownRateHint') },
    { label: t('index.metrics.ragSuccessRate'), value: fmtPercent(metrics.rag_success_rate), hint: t('index.metrics.ragSuccessRateHint') },
    {
      label: t('index.metrics.ragP99'),
      value: metrics.rag_p99_ms !== null ? `${metrics.rag_p99_ms.toFixed(0)} ms` : '—',
      hint: t('index.metrics.p99AvgHint', { avg: metrics.avg_rag_ms !== null ? metrics.avg_rag_ms.toFixed(0) : '—' }),
    },
  ];
  return (
    <Card>
      <CardContent className="grid grid-cols-2 gap-y-5 px-6 py-5 sm:grid-cols-3 lg:grid-cols-5">
        {items.map((item, idx) => (
          <div
            key={item.label}
            className={cn(
              'flex flex-col gap-1',
              idx > 0 && 'lg:border-l lg:pl-5',
              idx === 2 && 'sm:border-l sm:pl-5 lg:border-l lg:pl-5',
            )}
          >
            <span className="text-xs text-muted-foreground">{item.label}</span>
            <span className="text-2xl font-semibold tracking-tight">{item.value}</span>
            <span className="text-xs text-muted-foreground/80">{item.hint}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

/** 诊断质量态势：Trust Index、质量达标率、幻觉率、生成/检索成功率与内容安全（投毒拦截）统计。 */
function QualityStrip({ quality }: { quality: DashboardData['quality'] }) {
  const { t } = useTranslation();
  const items = [
    {
      label: t('quality.trustIndex'),
      value: quality.trust_index_avg !== null ? fmtPercent(quality.trust_index_avg) : '—',
      hint: t('index.quality.sampleHint', { count: quality.sample_count }),
    },
    {
      label: t('index.quality.okRate'),
      value: quality.quality_ok_rate !== null ? fmtPercent(quality.quality_ok_rate) : '—',
      hint: t('index.quality.okRateHint'),
    },
    {
      label: t('quality.hallucinationRate'),
      value: quality.hallucination_rate_avg !== null ? fmtPercent(quality.hallucination_rate_avg) : '—',
      hint: t('index.quality.hallucinationHint'),
    },
    {
      label: t('index.quality.generationSuccessRate'),
      value: quality.generation.success_rate !== null ? fmtPercent(quality.generation.success_rate) : '—',
      hint: t('index.quality.generationHint', { success: quality.generation.success, fail: quality.generation.fail }),
    },
    {
      label: t('index.quality.retrievalSuccessRate'),
      value: quality.retrieval.success_rate !== null ? fmtPercent(quality.retrieval.success_rate) : '—',
      hint: t('index.quality.retrievalP99Hint', {
        ms: quality.retrieval.p99_ms !== null ? `${quality.retrieval.p99_ms.toFixed(0)} ms` : '—',
      }),
    },
    {
      label: t('index.quality.poisonBlocked'),
      value: String(quality.content_safety.blocked),
      hint: t('index.quality.poisonHint', {
        scans: quality.content_safety.scans,
        overrides: quality.content_safety.overrides,
      }),
    },
  ];
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{t('index.quality.title')}</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-y-5 px-6 py-5 sm:grid-cols-3 lg:grid-cols-6">
        {items.map((item, idx) => (
          <div
            key={item.label}
            className={cn(
              'flex flex-col gap-1',
              idx > 0 && 'lg:border-l lg:pl-5',
              idx === 2 && 'sm:border-l sm:pl-5',
              idx === 4 && 'sm:border-l sm:pl-5',
            )}
          >
            <span className="text-xs text-muted-foreground">{item.label}</span>
            <span className="text-xl font-semibold tracking-tight">{item.value}</span>
            <span className="text-xs text-muted-foreground/80">{item.hint}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function BarList({ title, data, emptyHint }: { title: string; data: { name: string; count: number }[]; emptyHint: string }) {
  const max = data.length ? Math.max(...data.map((d) => d.count)) : 0;
  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      {data.length === 0 ? (
        <p className="py-4 text-center text-xs text-muted-foreground">{emptyHint}</p>
      ) : (
        <ul className="space-y-2.5">
          {data.map((d) => (
            <li key={d.name} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1">
              <span className="truncate text-xs">{d.name}</span>
              <span className="text-xs font-medium text-muted-foreground">{d.count}</span>
              <span className="col-span-2 h-1.5 overflow-hidden rounded-full bg-muted">
                <span
                  className="block h-full rounded-full bg-primary/70"
                  style={{ width: `${max ? Math.max(8, (d.count / max) * 100) : 0}%` }}
                />
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function useDashboardQuery() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: () => consoleApi.getDashboard(),
    refetchInterval: 30_000,
  });
}

export default function Index() {
  const { t } = useTranslation();
  const { data, isLoading, isError, error, refetch } = useDashboardQuery();

  if (isLoading) {
    return (
      <div className="space-y-5">
        <Skeleton className="h-28 w-full" />
        <div className="grid gap-5 lg:grid-cols-3">
          <Skeleton className="h-72 lg:col-span-2" />
          <Skeleton className="h-72" />
        </div>
      </div>
    );
  }
  if (isError || !data) {
    return <ErrorBlock message={`${t('index.dashboardLoadFailed')}：${errDetail(error)}`} onRetry={() => refetch()} />;
  }

  const severityEntries = Object.entries(data.by_severity);
  const severityTotal = severityEntries.reduce((sum, [, v]) => sum + v, 0);

  return (
    <div className="space-y-5">
      <MetricStrip metrics={data.metrics} />
      <QualityStrip quality={data.quality} />

      <div className="grid items-start gap-5 lg:grid-cols-3">
        {/* 左列：分布与 TOP 榜 */}
        <div className="space-y-5 lg:col-span-2">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">{t('index.severityDistribution')}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {severityTotal === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">{t('index.noSeverityData')}</p>
              ) : (
                severityEntries.map(([sev, count]) => (
                  <div key={sev} className="flex items-center gap-3">
                    <div className="w-16">
                      <SeverityBadge severity={sev} />
                    </div>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                      <div
                        className={cn(
                          'h-full rounded-full',
                          sev === 'critical' ? 'bg-red-500' : sev === 'warning' ? 'bg-amber-500' : 'bg-sky-500',
                        )}
                        style={{ width: `${Math.max(4, (count / severityTotal) * 100)}%` }}
                      />
                    </div>
                    <span className="w-10 text-right text-xs font-medium">{count}</span>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <div className="grid gap-5 sm:grid-cols-2">
            <Card>
              <CardContent className="pt-6">
                <BarList title={t('index.topErrorTypes')} data={data.top_error_types} emptyHint={t('index.topErrorTypesEmpty')} />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <BarList title={t('index.topServices')} data={data.top_services} emptyHint={t('index.topServicesEmpty')} />
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
              <CardTitle className="text-base">{t('index.recentEvents')}</CardTitle>
              <Link to="/events" className="flex items-center gap-1 text-xs font-medium text-primary hover:underline">
                {t('index.gotoEvents')}
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </CardHeader>
            <CardContent>
              {data.recent_events.length === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">{t('index.noRecentEvents')}</p>
              ) : (
                <ul className="divide-y">
                  {data.recent_events.map((e) => (
                    <li key={e.id} className="flex items-center gap-3 py-2.5 text-sm">
                      <SeverityBadge severity={e.severity} />
                      <span className="min-w-0 flex-1 truncate font-medium">{e.service_name}</span>
                      <span className="hidden min-w-0 flex-1 truncate text-xs text-muted-foreground sm:block">
                        {e.error_type || t('index.unclassified')}
                      </span>
                      <span className="text-xs text-muted-foreground">{fmtTime(e.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>

        {/* 右列：健康 / 知识库 / 待办 */}
        <div className="space-y-5">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">{t('index.healthTitle')}</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-3">
                {Object.entries(data.health).map(([key, h]) => (
                  <li key={key} className="flex items-start gap-2.5">
                    <span
                      className={cn(
                        'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                        h.status === 'healthy' ? 'bg-teal-500' : h.status === 'degraded' ? 'bg-amber-500' : 'bg-red-500',
                      )}
                    />
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{t(`index.health.${key}`, { defaultValue: key })}</p>
                      <p className="text-xs text-muted-foreground">{h.detail}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">{t('index.kbCardTitle')}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <p className="text-xl font-semibold">{data.kb.total}</p>
                  <p className="text-xs text-muted-foreground">{t('index.kbTotal')}</p>
                </div>
                <div>
                  <p className="text-xl font-semibold">{data.kb.archived}</p>
                  <p className="text-xs text-muted-foreground">{t('index.kbArchived')}</p>
                </div>
                <div>
                  <p className="text-xl font-semibold">{data.kb.avg_feedback}</p>
                  <p className="text-xs text-muted-foreground">{t('index.kbAvgFeedback')}</p>
                </div>
              </div>
              <Link
                to="/kb"
                className="mt-4 flex items-center justify-center gap-1.5 rounded-md border py-2 text-xs font-medium text-primary hover:bg-accent"
              >
                <BookOpenText className="h-3.5 w-3.5" />
                {t('index.manageKb')}
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
              <CardTitle className="text-base">{t('index.kbHealthTitle')}</CardTitle>
              <Link to="/ops" className="flex items-center gap-1 text-xs font-medium text-primary hover:underline">
                {t('index.healthReport')}
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <p className="text-xl font-semibold text-teal-600 dark:text-teal-400">{data.kb_health.green}</p>
                  <p className="text-xs text-muted-foreground">{t('index.healthGreen')}</p>
                </div>
                <div>
                  <p className="text-xl font-semibold text-amber-600 dark:text-amber-400">{data.kb_health.yellow}</p>
                  <p className="text-xs text-muted-foreground">{t('index.healthYellow')}</p>
                </div>
                <div>
                  <p className="text-xl font-semibold text-red-600 dark:text-red-400">{data.kb_health.red}</p>
                  <p className="text-xs text-muted-foreground">{t('index.healthRed')}</p>
                </div>
              </div>
              <div className="mt-4 space-y-1.5">
                <div className="flex h-2 overflow-hidden rounded-full bg-muted">
                  {data.kb_health.total > 0 && (
                    <>
                      <span className="bg-teal-500" style={{ width: `${(data.kb_health.green / data.kb_health.total) * 100}%` }} />
                      <span className="bg-amber-500" style={{ width: `${(data.kb_health.yellow / data.kb_health.total) * 100}%` }} />
                      <span className="bg-red-500" style={{ width: `${(data.kb_health.red / data.kb_health.total) * 100}%` }} />
                    </>
                  )}
                </div>
                <p className="flex items-center justify-between text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    <HeartPulse className="h-3 w-3" />
                    {t('index.healthRate', {
                      rate: data.kb_health.health_rate !== null ? fmtPercent(data.kb_health.health_rate) : '—',
                    })}
                  </span>
                  <span>{t('index.healthTotal', { count: data.kb_health.total })}</span>
                </p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">{t('index.todoTitle')}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2.5">
              <Link
                to="/approvals"
                className="flex items-center gap-2.5 rounded-md border p-3 transition-colors hover:bg-accent"
              >
                <ClipboardCheck className="h-4 w-4 text-primary" />
                <span className="flex-1 text-sm">{t('index.todoApprovals')}</span>
                <span className="text-lg font-semibold">{data.todo.pending_approvals}</span>
              </Link>
              <Link
                to="/rules"
                className="flex items-center gap-2.5 rounded-md border p-3 transition-colors hover:bg-accent"
              >
                <BellRing className="h-4 w-4 text-primary" />
                <span className="flex-1 text-sm">{t('index.todoUnknowns')}</span>
                <span className="text-lg font-semibold">{data.todo.pending_unknowns}</span>
              </Link>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
