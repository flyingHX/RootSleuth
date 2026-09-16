/** C3 运营总览：核心指标、告警分布、TOP 榜、依赖健康与待办。 */
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowRight, BellRing, BookOpenText, ClipboardCheck, HeartPulse } from 'lucide-react';
import { consoleApi, errDetail, type DashboardData } from '@/lib/console-api';
import { ErrorBlock, SeverityBadge, StateGate, fmtPercent, fmtTime } from '@/components/console/shared';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

const HEALTH_LABEL: Record<string, string> = {
  milvus: 'Milvus 向量检索',
  redis: 'Redis 去重聚合',
  elasticsearch: 'Elasticsearch 冷存储',
  llm: 'LLM 诊断（deepseek-v4-flash）',
};

function MetricStrip({ metrics }: { metrics: NonNullable<ReturnType<typeof useDashboardQuery>['data']>['metrics'] }) {
  const items = [
    { label: '告警总量（近 7 天）', value: String(metrics.total_events), hint: '标准化后事件数' },
    { label: '降噪率', value: fmtPercent(metrics.noise_reduction), hint: '指纹去重合并比例' },
    { label: '未知率', value: fmtPercent(metrics.unknown_rate), hint: '无相似案例召回占比' },
    { label: 'RAG 成功率', value: fmtPercent(metrics.rag_success_rate), hint: '召回 + 诊断成功' },
    {
      label: 'RAG P99',
      value: metrics.rag_p99_ms !== null ? `${metrics.rag_p99_ms.toFixed(0)} ms` : '—',
      hint: `平均 ${metrics.avg_rag_ms !== null ? metrics.avg_rag_ms.toFixed(0) : '—'} ms`,
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
  const items = [
    {
      label: 'Trust Index',
      value: quality.trust_index_avg !== null ? quality.trust_index_avg.toFixed(3) : '—',
      hint: `近 ${quality.sample_count} 次诊断样本`,
    },
    {
      label: '质量达标率',
      value: quality.quality_ok_rate !== null ? fmtPercent(quality.quality_ok_rate) : '—',
      hint: '五项质量指标全达标占比',
    },
    {
      label: '幻觉率',
      value: quality.hallucination_rate_avg !== null ? fmtPercent(quality.hallucination_rate_avg) : '—',
      hint: '诊断结论幻觉占比（越低越好）',
    },
    {
      label: '生成成功率',
      value: quality.generation.success_rate !== null ? fmtPercent(quality.generation.success_rate) : '—',
      hint: `LLM 成功 ${quality.generation.success} / 失败 ${quality.generation.fail}`,
    },
    {
      label: '检索成功率',
      value: quality.retrieval.success_rate !== null ? fmtPercent(quality.retrieval.success_rate) : '—',
      hint: `P99 ${quality.retrieval.p99_ms !== null ? `${quality.retrieval.p99_ms.toFixed(0)} ms` : '—'}`,
    },
    {
      label: '投毒拦截',
      value: String(quality.content_safety.blocked),
      hint: `内容扫描 ${quality.content_safety.scans} 次 · 误报申诉放行 ${quality.content_safety.overrides}`,
    },
  ];
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">诊断质量态势</CardTitle>
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
    return <ErrorBlock message={`Dashboard 加载失败：${errDetail(error)}`} onRetry={() => refetch()} />;
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
              <CardTitle className="text-base">告警严重度分布</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {severityTotal === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">暂无告警数据</p>
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
                <BarList title="TOP 错误类型" data={data.top_error_types} emptyHint="暂无分类数据" />
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-6">
                <BarList title="TOP 服务" data={data.top_services} emptyHint="暂无服务数据" />
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
              <CardTitle className="text-base">最新告警</CardTitle>
              <Link to="/events" className="flex items-center gap-1 text-xs font-medium text-primary hover:underline">
                进入告警工作台
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </CardHeader>
            <CardContent>
              {data.recent_events.length === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">暂无告警事件</p>
              ) : (
                <ul className="divide-y">
                  {data.recent_events.map((e) => (
                    <li key={e.id} className="flex items-center gap-3 py-2.5 text-sm">
                      <SeverityBadge severity={e.severity} />
                      <span className="min-w-0 flex-1 truncate font-medium">{e.service_name}</span>
                      <span className="hidden min-w-0 flex-1 truncate text-xs text-muted-foreground sm:block">
                        {e.error_type || '未分类'}
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
              <CardTitle className="text-base">依赖健康</CardTitle>
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
                      <p className="text-sm font-medium">{HEALTH_LABEL[key] ?? key}</p>
                      <p className="text-xs text-muted-foreground">{h.detail}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">知识库</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <p className="text-xl font-semibold">{data.kb.total}</p>
                  <p className="text-xs text-muted-foreground">案例总数</p>
                </div>
                <div>
                  <p className="text-xl font-semibold">{data.kb.archived}</p>
                  <p className="text-xs text-muted-foreground">已归档</p>
                </div>
                <div>
                  <p className="text-xl font-semibold">{data.kb.avg_feedback}</p>
                  <p className="text-xs text-muted-foreground">平均反馈分</p>
                </div>
              </div>
              <Link
                to="/kb"
                className="mt-4 flex items-center justify-center gap-1.5 rounded-md border py-2 text-xs font-medium text-primary hover:bg-accent"
              >
                <BookOpenText className="h-3.5 w-3.5" />
                管理知识库
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
              <CardTitle className="text-base">知识健康</CardTitle>
              <Link to="/ops" className="flex items-center gap-1 text-xs font-medium text-primary hover:underline">
                健康报表
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <p className="text-xl font-semibold text-teal-600 dark:text-teal-400">{data.kb_health.green}</p>
                  <p className="text-xs text-muted-foreground">健康</p>
                </div>
                <div>
                  <p className="text-xl font-semibold text-amber-600 dark:text-amber-400">{data.kb_health.yellow}</p>
                  <p className="text-xs text-muted-foreground">关注</p>
                </div>
                <div>
                  <p className="text-xl font-semibold text-red-600 dark:text-red-400">{data.kb_health.red}</p>
                  <p className="text-xs text-muted-foreground">风险</p>
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
                    健康率 {data.kb_health.health_rate !== null ? fmtPercent(data.kb_health.health_rate) : '—'}
                  </span>
                  <span>共 {data.kb_health.total} 个案例</span>
                </p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">待办事项</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2.5">
              <Link
                to="/approvals"
                className="flex items-center gap-2.5 rounded-md border p-3 transition-colors hover:bg-accent"
              >
                <ClipboardCheck className="h-4 w-4 text-primary" />
                <span className="flex-1 text-sm">待审批请求</span>
                <span className="text-lg font-semibold">{data.todo.pending_approvals}</span>
              </Link>
              <Link
                to="/rules"
                className="flex items-center gap-2.5 rounded-md border p-3 transition-colors hover:bg-accent"
              >
                <BellRing className="h-4 w-4 text-primary" />
                <span className="flex-1 text-sm">待处理未知模板</span>
                <span className="text-lg font-semibold">{data.todo.pending_unknowns}</span>
              </Link>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
