/** C4/C5 告警工作台：筛选列表 + 详情（召回链路、AI 诊断、命令复制、反馈闭环）。文案经 i18n 双语渲染。 */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ThumbsDown, ThumbsUp, Stethoscope, FileJson } from 'lucide-react';
import { toast } from 'sonner';
import {
  consoleApi,
  errDetail,
  type EventDetail,
  type EventItem,
  type DiagnosisResult,
} from '@/lib/console-api';
import {
  ConfidenceBadge,
  CopyButton,
  EventStatusBadge,
  JsonPre,
  QualityMetricsCard,
  RagBadge,
  SeverityBadge,
  SpinnerLine,
  StateGate,
  fmtTime,
} from '@/components/console/shared';
import { usePermissions } from '@/components/console/ConsoleLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

const PAGE_SIZE = 20;

const SEVERITY_OPTIONS = [
  { value: 'all', labelKey: 'events.filter.allSeverity' },
  { value: 'critical', labelKey: 'shared.severity.critical' },
  { value: 'warning', labelKey: 'shared.severity.warning' },
  { value: 'info', labelKey: 'shared.severity.info' },
];
const STATUS_OPTIONS = [
  { value: 'all', labelKey: 'events.filter.allStatus' },
  { value: 'pending', labelKey: 'shared.eventStatus.pending' },
  { value: 'diagnosed', labelKey: 'shared.eventStatus.diagnosed' },
  { value: 'unknown', labelKey: 'shared.eventStatus.unknown' },
];
const TIME_OPTIONS = [
  { value: '1h', labelKey: 'events.filter.time1h' },
  { value: '24h', labelKey: 'events.filter.time24h' },
  { value: '7d', labelKey: 'events.filter.time7d' },
  { value: 'all', labelKey: 'events.filter.timeAll' },
];

interface Filters {
  severity: string;
  service: string;
  cluster: string;
  error_type: string;
  status: string;
  time_range: string;
  q: string;
}

const DEFAULT_FILTERS: Filters = {
  severity: 'all',
  service: '',
  cluster: '',
  error_type: '',
  status: 'all',
  time_range: '7d',
  q: '',
};

function filtersToParams(f: Filters, skip: number): Record<string, string | number> {
  const params: Record<string, string | number> = { skip, limit: PAGE_SIZE, time_range: f.time_range };
  if (f.severity !== 'all') params.severity = f.severity;
  if (f.status !== 'all') params.status = f.status;
  if (f.service.trim()) params.service = f.service.trim();
  if (f.cluster.trim()) params.cluster = f.cluster.trim();
  if (f.error_type.trim()) params.error_type = f.error_type.trim();
  if (f.q.trim()) params.q = f.q.trim();
  return params;
}

function FilterBar({ filters, onChange }: { filters: Filters; onChange: (f: Filters) => void }) {
  const { t } = useTranslation();
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  return (
    <div className="flex flex-wrap items-end gap-2.5">
      <div className="w-32">
        <Label className="mb-1 text-xs">{t('events.filter.severity')}</Label>
        <Select value={filters.severity} onValueChange={(v) => set({ severity: v })}>
          <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>{SEVERITY_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{t(o.labelKey)}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="w-32">
        <Label className="mb-1 text-xs">{t('events.filter.status')}</Label>
        <Select value={filters.status} onValueChange={(v) => set({ status: v })}>
          <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>{STATUS_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{t(o.labelKey)}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="w-32">
        <Label className="mb-1 text-xs">{t('events.filter.timeRange')}</Label>
        <Select value={filters.time_range} onValueChange={(v) => set({ time_range: v })}>
          <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>{TIME_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{t(o.labelKey)}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="w-36">
        <Label className="mb-1 text-xs">{t('events.filter.service')}</Label>
        <Input className="h-9 text-xs" placeholder={t('events.filter.servicePlaceholder')} value={filters.service} onChange={(e) => set({ service: e.target.value })} />
      </div>
      <div className="w-36">
        <Label className="mb-1 text-xs">{t('events.filter.errorType')}</Label>
        <Input className="h-9 text-xs" placeholder={t('events.filter.errorTypePlaceholder')} value={filters.error_type} onChange={(e) => set({ error_type: e.target.value })} />
      </div>
      <div className="w-40">
        <Label className="mb-1 text-xs">{t('events.filter.search')}</Label>
        <Input className="h-9 text-xs" placeholder={t('events.filter.searchPlaceholder')} value={filters.q} onChange={(e) => set({ q: e.target.value })} />
      </div>
      <Button variant="ghost" size="sm" className="h-9 text-xs" onClick={() => onChange(DEFAULT_FILTERS)}>
        {t('events.filter.reset')}
      </Button>
    </div>
  );
}

function EventRow({ e, active, onClick }: { e: EventItem; active: boolean; onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full rounded-md border px-3 py-2.5 text-left transition-colors',
        active ? 'border-primary/60 bg-accent' : 'hover:bg-accent/60',
      )}
    >
      <div className="flex items-center gap-2">
        <SeverityBadge severity={e.severity} />
        <EventStatusBadge status={e.status} />
        <span className="ml-auto text-xs text-muted-foreground">{fmtTime(e.created_at)}</span>
      </div>
      <p className="mt-1.5 truncate text-sm font-medium">{e.service_name}</p>
      <p className="truncate text-xs text-muted-foreground">{e.error_type || t('events.unclassified')} · {e.event_id}</p>
    </button>
  );
}

function CandidatesList({ detail }: { detail: EventDetail }) {
  const { t } = useTranslation();
  if (!detail.candidates || detail.candidates.length === 0) {
    return <p className="text-xs text-muted-foreground">{t('events.noCandidates')}</p>;
  }
  return (
    <ol className="space-y-2.5">
      {detail.candidates.map((c, i) => (
        <li key={c.case_id} className="rounded-md border p-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="font-mono">#{i + 1}</Badge>
            <span className="font-mono font-medium">{c.case_id}</span>
            <span className="text-muted-foreground">{c.error_type} · {c.service_name}</span>
            <span className="ml-auto font-medium text-primary">{t('events.similarity', { score: `${(c.score * 100).toFixed(1)}%` })}</span>
            {typeof c.embedding_score === 'number' && (
              <Badge variant="outline" className="font-mono">{t('events.semantic', { score: c.embedding_score.toFixed(3) })}</Badge>
            )}
            {typeof c.feedback_score === 'number' && c.feedback_score !== 0 && (
              <Badge variant="outline" className={c.feedback_score > 0 ? 'text-teal-700' : 'text-red-600'}>
                {t('events.feedbackScore', { score: `${c.feedback_score > 0 ? '+' : ''}${c.feedback_score}` })}
              </Badge>
            )}
          </div>
          <p className="mt-1.5 leading-relaxed text-muted-foreground">{t('events.rootCauseLine', { value: c.root_cause || '—' })}</p>
          <p className="leading-relaxed text-muted-foreground">{t('events.solutionLine', { value: c.solution || '—' })}</p>
        </li>
      ))}
    </ol>
  );
}

function DiagnosisPanel({ detail, result }: { detail: EventDetail; result: DiagnosisResult | null }) {
  const { t } = useTranslation();
  const diagnosis = result?.diagnosis
    ? result.diagnosis
    : detail.ai_output
      ? { ...detail.ai_output, low_confidence: false, threshold: 0.7 }
      : null;
  // 单次质量指标：优先取本次诊断响应，历史事件回退 ai_output_json 中持久化的 quality
  const quality = result?.quality ?? detail.ai_output?.quality ?? null;

  if (!diagnosis) {
    return <p className="text-xs text-muted-foreground">{t('events.noDiagnosis')}</p>;
  }
  return (
    <div className="space-y-3 text-sm">
      {result && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <RagBadge status={result.rag.status} />
          {result.rag.score !== null && (
            <Badge variant="outline">{t('events.recallScore', { score: `${(result.rag.score * 100).toFixed(1)}%` })}</Badge>
          )}
          <Badge variant="outline">{t('events.retrievalMs', { ms: result.rag.ms })}</Badge>
          {typeof result.elapsed_ms === 'number' && (
            <Badge variant="outline">{t('events.e2eMs', { ms: result.elapsed_ms })}</Badge>
          )}
          {result.rag.rerank && (
            <Badge variant="outline" className="font-mono">
              {t('events.rerankBadge', { strategy: result.rag.rerank.strategy, count: result.rag.rerank.candidate_count })}
            </Badge>
          )}
          {result.rag.embedding?.applied && (
            <Badge variant="outline">
              {t('events.embeddingBoost', { weight: result.rag.embedding.boost_weight ?? 0.5 })}
            </Badge>
          )}
          {diagnosis.model && (
            <Badge variant="secondary" className="font-mono">{diagnosis.model}</Badge>
          )}
        </div>
      )}
      <QualityMetricsCard quality={quality} />
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">{t('events.confidenceLabel')}</span>
        <ConfidenceBadge value={diagnosis.confidence} low={diagnosis.low_confidence} />
        {diagnosis.low_confidence && (
          <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700">
            {t('events.manualReview', { threshold: (diagnosis.threshold * 100).toFixed(0) })}
          </Badge>
        )}
      </div>
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">{t('events.rootCauseTitle')}</p>
        <p className="leading-relaxed">{diagnosis.root_cause}</p>
      </div>
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">{t('events.solutionTitle')}</p>
        <p className="leading-relaxed">{diagnosis.solution}</p>
      </div>
      <div>
        <div className="mb-1 flex items-center justify-between">
          <p className="text-xs font-medium text-muted-foreground">{t('events.commandTitle')}</p>
          <CopyButton text={diagnosis.command} label={t('events.copyCommand')} size="xs" />
        </div>
        <pre className="log-block">{diagnosis.command}</pre>
      </div>
    </div>
  );
}

function FeedbackSection({ detail }: { detail: EventDetail }) {
  const { t } = useTranslation();
  const perms = usePermissions();
  const queryClient = useQueryClient();
  const [rating, setRating] = useState<'up' | 'down' | null>(null);
  const [correction, setCorrection] = useState({ root_cause: '', solution: '' });
  const [comment, setComment] = useState('');

  const mutation = useMutation({
    mutationFn: () =>
      consoleApi.feedback(detail.id, {
        rating: rating === 'up' ? 'up' : 'down',
        correction:
          correction.root_cause.trim() || correction.solution.trim()
            ? {
                ...(correction.root_cause.trim() ? { root_cause: correction.root_cause.trim() } : {}),
                ...(correction.solution.trim() ? { solution: correction.solution.trim() } : {}),
              }
            : {},
        comment,
      }),
    onSuccess: (res) => {
      toast.success(
        t('events.feedback.successToast', {
          caseId: res.case_id,
          score: res.feedback_score,
          extra: res.correction_change_set ? t('events.feedback.correctionExtra') : '',
        }),
      );
      setRating(null);
      setCorrection({ root_cause: '', solution: '' });
      setComment('');
      queryClient.invalidateQueries({ queryKey: ['event', detail.id] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
    onError: (e) => toast.error(t('events.feedback.errorToast', { error: errDetail(e) })),
  });

  const disabled =
    !perms?.can_feedback ||
    mutation.isPending ||
    rating === null ||
    (rating === 'down' && !correction.root_cause.trim() && !correction.solution.trim() && !comment.trim());

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Button
          variant={rating === 'up' ? 'default' : 'outline'}
          size="sm"
          onClick={() => setRating('up')}
        >
          <ThumbsUp className="mr-1.5 h-3.5 w-3.5" />
          {t('events.feedback.accurate')}
        </Button>
        <Button
          variant={rating === 'down' ? 'destructive' : 'outline'}
          size="sm"
          onClick={() => setRating('down')}
        >
          <ThumbsDown className="mr-1.5 h-3.5 w-3.5" />
          {t('events.feedback.inaccurate')}
        </Button>
      </div>
      {rating === 'down' && (
        <div className="space-y-2.5 rounded-md border bg-secondary/40 p-3">
          <p className="text-xs text-muted-foreground">{t('events.feedback.inaccurateHint')}</p>
          <div>
            <Label className="mb-1 text-xs">{t('events.feedback.correctedRootCause')}</Label>
            <Textarea
              className="min-h-16 text-xs"
              placeholder={t('events.feedback.rootCausePlaceholder')}
              value={correction.root_cause}
              onChange={(e) => setCorrection((s) => ({ ...s, root_cause: e.target.value }))}
            />
          </div>
          <div>
            <Label className="mb-1 text-xs">{t('events.feedback.correctedSolution')}</Label>
            <Textarea
              className="min-h-16 text-xs"
              placeholder={t('events.feedback.solutionPlaceholder')}
              value={correction.solution}
              onChange={(e) => setCorrection((s) => ({ ...s, solution: e.target.value }))}
            />
          </div>
          <div>
            <Label className="mb-1 text-xs">{t('events.feedback.comment')}</Label>
            <Input
              className="h-9 text-xs"
              placeholder={t('events.feedback.commentPlaceholder')}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          </div>
        </div>
      )}
      <Button size="sm" disabled={disabled} onClick={() => mutation.mutate()}>
        {mutation.isPending ? t('events.feedback.submitting') : t('events.feedback.submit')}
      </Button>
      {perms && !perms.can_feedback && (
        <p className="text-xs text-muted-foreground">{t('events.feedback.noPermission', { role: perms.role_label })}</p>
      )}
    </div>
  );
}

function DetailBody({ detail, result, onDiagnose, diagnosing }: {
  detail: EventDetail;
  result: DiagnosisResult | null;
  onDiagnose: () => void;
  diagnosing: boolean;
}) {
  const { t } = useTranslation();
  const perms = usePermissions();
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={detail.severity} />
        <EventStatusBadge status={detail.status} />
        <RagBadge status={detail.rag_status} />
        {perms?.can_diagnose && (
          <Button
            size="sm"
            className="ml-auto"
            onClick={onDiagnose}
            disabled={diagnosing}
          >
            <Stethoscope className="mr-1.5 h-3.5 w-3.5" />
            {diagnosing ? t('events.diagnosing') : t('events.diagnoseBtn')}
          </Button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md border bg-secondary/40 p-3 text-xs sm:grid-cols-3">
        <div><p className="text-muted-foreground">{t('events.fieldEventId')}</p><p className="mt-0.5 truncate font-mono">{detail.event_id}</p></div>
        <div><p className="text-muted-foreground">{t('events.fieldService')}</p><p className="mt-0.5 truncate font-medium">{detail.service_name}</p></div>
        <div><p className="text-muted-foreground">{t('events.fieldCluster')}</p><p className="mt-0.5 truncate">{detail.cluster || '—'}</p></div>
        <div><p className="text-muted-foreground">{t('events.fieldErrorType')}</p><p className="mt-0.5 truncate">{detail.error_type || '—'}</p></div>
        <div><p className="text-muted-foreground">{t('events.fieldRagMs')}</p><p className="mt-0.5">{detail.rag_ms !== null ? `${detail.rag_ms} ms` : '—'}</p></div>
        <div><p className="text-muted-foreground">{t('events.fieldStdMs')}</p><p className="mt-0.5">{detail.std_ms !== null ? `${detail.std_ms} ms` : '—'}</p></div>
      </div>

      {detail.degraded_reason && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700">
          {t('events.degradedPrefix')}<span className="font-mono">{detail.degraded_reason}</span>
          {t('events.degradedSuffix')}
        </div>
      )}

      <div>
        <p className="mb-1.5 text-sm font-semibold">{t('events.conclusionTitle')}</p>
        <DiagnosisPanel detail={detail} result={result} />
      </div>

      <Separator />

      <div>
        <p className="mb-1.5 text-sm font-semibold">{t('events.recallChainTitle')}</p>
        <CandidatesList detail={detail} />
      </div>

      <Separator />

      <div>
        <p className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold">
          <FileJson className="h-4 w-4" />
          {t('events.rawJsonTitle')}
        </p>
        {detail.ai_output ? (
          <JsonPre data={detail.ai_output} />
        ) : (
          <p className="text-xs text-muted-foreground">{t('events.noJson')}</p>
        )}
      </div>

      <Separator />

      <div>
        <p className="mb-1.5 text-sm font-semibold">{t('events.rawLogTitle')}</p>
        <pre className="log-block max-h-40 overflow-y-auto">{detail.raw_log || t('events.none')}</pre>
      </div>
      <div>
        <p className="mb-1.5 text-sm font-semibold">{t('events.templateTitle')}</p>
        <pre className="log-block">{detail.template || t('events.none')}</pre>
      </div>
      <div>
        <p className="mb-1.5 text-sm font-semibold">{t('events.topologyTitle')}</p>
        <pre className="log-block">{detail.topology || t('events.none')}</pre>
      </div>

      <Separator />

      {perms?.can_feedback && (
        <div>
          <p className="mb-1.5 text-sm font-semibold">{t('events.feedbackTitle')}</p>
          <FeedbackSection detail={detail} />
        </div>
      )}
    </div>
  );
}

export default function EventsPage() {
  const { t } = useTranslation();
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const queryClient = useQueryClient();

  // 筛选变化时回到第一页
  useEffect(() => setPage(0), [filters]);

  const listQuery = useQuery({
    queryKey: ['events', filters, page],
    queryFn: () => consoleApi.listEvents(filtersToParams(filters, page * PAGE_SIZE)),
  });

  const detailQuery = useQuery({
    queryKey: ['event', selectedId],
    queryFn: () => consoleApi.getEvent(selectedId!),
    enabled: selectedId !== null,
  });

  const diagnoseMutation = useMutation({
    mutationFn: (id: number) => consoleApi.diagnose(id),
    onMutate: (id) => toast(t('events.diagnoseToast', { id }), { duration: 15000 }),
    onSuccess: (res) => {
      toast.success(res.message || t('events.diagnoseSuccess'));
      queryClient.invalidateQueries({ queryKey: ['event', selectedId] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
    onError: (e) => toast.error(t('events.diagnoseError', { error: errDetail(e) }), { duration: 15000 }),
  });

  const items = useMemo(() => listQuery.data?.items ?? [], [listQuery.data]);
  const detail = detailQuery.data;
  const total = listQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const select = (id: number) => {
    setSelectedId(id);
    // 桌面端（lg 及以上）右侧详情常驻展示，仅窄屏打开抽屉，避免遮罩层拦截「AI 诊断」等操作
    if (!window.matchMedia('(min-width: 1024px)').matches) {
      setMobileDetailOpen(true);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{t('events.title')}</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {t('events.subtitle')}
        </p>
      </div>

      <FilterBar filters={filters} onChange={setFilters} />

      <div className="grid items-start gap-4 lg:grid-cols-5">
        {/* 左：事件列表 */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {t('events.listTitle')}
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {t('events.listTotal', { total })}
                {totalPages > 1 ? ` · ${t('events.listPage', { page: page + 1, pages: totalPages })}` : ''}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <StateGate
              loading={listQuery.isLoading}
              error={listQuery.isError ? errDetail(listQuery.error) : null}
              onRetry={() => listQuery.refetch()}
              isEmpty={items.length === 0}
              empty={t('events.empty')}
              emptyHint={t('events.emptyHint')}
            >
              <div className="space-y-2">
                {items.map((e) => (
                  <EventRow key={e.id} e={e} active={e.id === selectedId} onClick={() => select(e.id)} />
                ))}
              </div>
              {totalPages > 1 && (
                <div className="mt-3 flex items-center justify-between">
                  <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                    {t('events.prevPage')}
                  </Button>
                  <span className="text-xs text-muted-foreground">{page + 1} / {totalPages}</span>
                  <Button variant="outline" size="sm" disabled={page >= totalPages - 1} onClick={() => setPage((p) => p + 1)}>
                    {t('events.nextPage')}
                  </Button>
                </div>
              )}
            </StateGate>
          </CardContent>
        </Card>

        {/* 右：详情（桌面） */}
        <Card className="hidden lg:col-span-3 lg:block">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t('events.detailTitle')}</CardTitle>
          </CardHeader>
          <CardContent>
            {selectedId === null ? (
              <p className="py-16 text-center text-sm text-muted-foreground">{t('events.detailPlaceholder')}</p>
            ) : (
              <StateGate
                loading={detailQuery.isLoading}
                error={detailQuery.isError ? errDetail(detailQuery.error) : null}
                onRetry={() => detailQuery.refetch()}
              >
                {detail && (
                  <DetailBody
                    detail={detail}
                    result={diagnoseMutation.data ?? null}
                    onDiagnose={() => diagnoseMutation.mutate(detail.id)}
                    diagnosing={diagnoseMutation.isPending}
                  />
                )}
                {diagnoseMutation.isPending && <SpinnerLine text={t('events.diagnosingLine')} />}
              </StateGate>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 移动端详情抽屉 */}
      <Sheet open={mobileDetailOpen} onOpenChange={setMobileDetailOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-lg">
          <SheetTitle>{t('events.detailTitle')}</SheetTitle>
          <div className="mt-4">
            {detail && (
              <DetailBody
                detail={detail}
                result={diagnoseMutation.data ?? null}
                onDiagnose={() => diagnoseMutation.mutate(detail.id)}
                diagnosing={diagnoseMutation.isPending}
              />
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
