/** C4/C5 告警工作台：筛选列表 + 详情（召回链路、AI 诊断、命令复制、反馈闭环）。 */
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  { value: 'all', label: '全部级别' },
  { value: 'critical', label: '严重' },
  { value: 'warning', label: '警告' },
  { value: 'info', label: '提示' },
];
const STATUS_OPTIONS = [
  { value: 'all', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'diagnosed', label: '已诊断' },
  { value: 'unknown', label: '未知' },
];
const TIME_OPTIONS = [
  { value: '1h', label: '近 1 小时' },
  { value: '24h', label: '近 24 小时' },
  { value: '7d', label: '近 7 天' },
  { value: 'all', label: '全部时间' },
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
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  return (
    <div className="flex flex-wrap items-end gap-2.5">
      <div className="w-32">
        <Label className="mb-1 text-xs">严重度</Label>
        <Select value={filters.severity} onValueChange={(v) => set({ severity: v })}>
          <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>{SEVERITY_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="w-32">
        <Label className="mb-1 text-xs">状态</Label>
        <Select value={filters.status} onValueChange={(v) => set({ status: v })}>
          <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>{STATUS_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="w-32">
        <Label className="mb-1 text-xs">时间范围</Label>
        <Select value={filters.time_range} onValueChange={(v) => set({ time_range: v })}>
          <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>{TIME_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="w-36">
        <Label className="mb-1 text-xs">服务名</Label>
        <Input className="h-9 text-xs" placeholder="如 payment-service" value={filters.service} onChange={(e) => set({ service: e.target.value })} />
      </div>
      <div className="w-36">
        <Label className="mb-1 text-xs">错误类型</Label>
        <Input className="h-9 text-xs" placeholder="如 gateway_502" value={filters.error_type} onChange={(e) => set({ error_type: e.target.value })} />
      </div>
      <div className="w-40">
        <Label className="mb-1 text-xs">搜索</Label>
        <Input className="h-9 text-xs" placeholder="事件 ID / 模板 / 日志" value={filters.q} onChange={(e) => set({ q: e.target.value })} />
      </div>
      <Button variant="ghost" size="sm" className="h-9 text-xs" onClick={() => onChange(DEFAULT_FILTERS)}>
        重置
      </Button>
    </div>
  );
}

function EventRow({ e, active, onClick }: { e: EventItem; active: boolean; onClick: () => void }) {
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
      <p className="truncate text-xs text-muted-foreground">{e.error_type || '未分类'} · {e.event_id}</p>
    </button>
  );
}

function CandidatesList({ detail }: { detail: EventDetail }) {
  if (!detail.candidates || detail.candidates.length === 0) {
    return <p className="text-xs text-muted-foreground">无召回候选案例（unknown 模板会进入未知队列）</p>;
  }
  return (
    <ol className="space-y-2.5">
      {detail.candidates.map((c, i) => (
        <li key={c.case_id} className="rounded-md border p-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="font-mono">#{i + 1}</Badge>
            <span className="font-mono font-medium">{c.case_id}</span>
            <span className="text-muted-foreground">{c.error_type} · {c.service_name}</span>
            <span className="ml-auto font-medium text-primary">相似度 {(c.score * 100).toFixed(1)}%</span>
            {typeof c.embedding_score === 'number' && (
              <Badge variant="outline" className="font-mono">语义 {c.embedding_score.toFixed(3)}</Badge>
            )}
            {typeof c.feedback_score === 'number' && c.feedback_score !== 0 && (
              <Badge variant="outline" className={c.feedback_score > 0 ? 'text-teal-700' : 'text-red-600'}>
                反馈 {c.feedback_score > 0 ? '+' : ''}{c.feedback_score}
              </Badge>
            )}
          </div>
          <p className="mt-1.5 leading-relaxed text-muted-foreground">根因：{c.root_cause || '—'}</p>
          <p className="leading-relaxed text-muted-foreground">处置：{c.solution || '—'}</p>
        </li>
      ))}
    </ol>
  );
}

function DiagnosisPanel({ detail, result }: { detail: EventDetail; result: DiagnosisResult | null }) {
  const diagnosis = result?.diagnosis
    ? result.diagnosis
    : detail.ai_output
      ? { ...detail.ai_output, model: 'deepseek-v4-flash（历史）', low_confidence: false, threshold: 0.7 }
      : null;
  // 单次质量指标：优先取本次诊断响应，历史事件回退 ai_output_json 中持久化的 quality
  const quality = result?.quality ?? detail.ai_output?.quality ?? null;

  if (!diagnosis) {
    return <p className="text-xs text-muted-foreground">尚无 AI 诊断结果，点击右上角「AI 诊断」生成。</p>;
  }
  return (
    <div className="space-y-3 text-sm">
      {result && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <RagBadge status={result.rag.status} />
          {result.rag.score !== null && (
            <Badge variant="outline">召回分 {(result.rag.score * 100).toFixed(1)}%</Badge>
          )}
          <Badge variant="outline">检索 {result.rag.ms} ms</Badge>
          {typeof result.elapsed_ms === 'number' && (
            <Badge variant="outline">端到端 {result.elapsed_ms} ms</Badge>
          )}
          {result.rag.rerank && (
            <Badge variant="outline" className="font-mono">
              重排 {result.rag.rerank.strategy} · {result.rag.rerank.candidate_count} 候选
            </Badge>
          )}
          {result.rag.embedding?.applied && (
            <Badge variant="outline">
              Embedding 加分（权重 {result.rag.embedding.boost_weight ?? 0.5}）
            </Badge>
          )}
          <Badge variant="secondary" className="font-mono">{diagnosis.model}</Badge>
        </div>
      )}
      <QualityMetricsCard quality={quality} />
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">置信度</span>
        <ConfidenceBadge value={diagnosis.confidence} low={diagnosis.low_confidence} />
        {diagnosis.low_confidence && (
          <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700">
            建议人工复核（阈值 {(diagnosis.threshold * 100).toFixed(0)}%）
          </Badge>
        )}
      </div>
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">根因分析</p>
        <p className="leading-relaxed">{diagnosis.root_cause}</p>
      </div>
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">处置建议</p>
        <p className="leading-relaxed">{diagnosis.solution}</p>
      </div>
      <div>
        <div className="mb-1 flex items-center justify-between">
          <p className="text-xs font-medium text-muted-foreground">处置命令</p>
          <CopyButton text={diagnosis.command} label="复制命令" size="xs" />
        </div>
        <pre className="log-block">{diagnosis.command}</pre>
      </div>
    </div>
  );
}

function FeedbackSection({ detail }: { detail: EventDetail }) {
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
      toast.success(`反馈已提交，案例 ${res.case_id} 反馈分更新为 ${res.feedback_score}${res.correction_change_set ? '，并已生成人工修正变更集' : ''}`);
      setRating(null);
      setCorrection({ root_cause: '', solution: '' });
      setComment('');
      queryClient.invalidateQueries({ queryKey: ['event', detail.id] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
    onError: (e) => toast.error(`反馈提交失败：${errDetail(e)}`),
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
          诊断准确
        </Button>
        <Button
          variant={rating === 'down' ? 'destructive' : 'outline'}
          size="sm"
          onClick={() => setRating('down')}
        >
          <ThumbsDown className="mr-1.5 h-3.5 w-3.5" />
          诊断有误
        </Button>
      </div>
      {rating === 'down' && (
        <div className="space-y-2.5 rounded-md border bg-secondary/40 p-3">
          <p className="text-xs text-muted-foreground">
            标记为「有误」时请填写人工修正或说明；修正内容会自动生成知识变更集并进入审批流。
          </p>
          <div>
            <Label className="mb-1 text-xs">修正后的根因（可选）</Label>
            <Textarea
              className="min-h-16 text-xs"
              placeholder="人工确认的真实根因"
              value={correction.root_cause}
              onChange={(e) => setCorrection((s) => ({ ...s, root_cause: e.target.value }))}
            />
          </div>
          <div>
            <Label className="mb-1 text-xs">修正后的处置（可选）</Label>
            <Textarea
              className="min-h-16 text-xs"
              placeholder="人工确认的处置方案"
              value={correction.solution}
              onChange={(e) => setCorrection((s) => ({ ...s, solution: e.target.value }))}
            />
          </div>
          <div>
            <Label className="mb-1 text-xs">备注说明（可选）</Label>
            <Input
              className="h-9 text-xs"
              placeholder="例如：召回案例过旧，拓扑已变更"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          </div>
        </div>
      )}
      <Button size="sm" disabled={disabled} onClick={() => mutation.mutate()}>
        {mutation.isPending ? '提交中…' : '提交反馈'}
      </Button>
      {perms && !perms.can_feedback && (
        <p className="text-xs text-muted-foreground">当前角色（{perms.role_label}）仅可查看，无反馈权限。</p>
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
            {diagnosing ? '诊断中…' : 'AI 诊断'}
          </Button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md border bg-secondary/40 p-3 text-xs sm:grid-cols-3">
        <div><p className="text-muted-foreground">事件 ID</p><p className="mt-0.5 truncate font-mono">{detail.event_id}</p></div>
        <div><p className="text-muted-foreground">服务</p><p className="mt-0.5 truncate font-medium">{detail.service_name}</p></div>
        <div><p className="text-muted-foreground">集群</p><p className="mt-0.5 truncate">{detail.cluster || '—'}</p></div>
        <div><p className="text-muted-foreground">错误类型</p><p className="mt-0.5 truncate">{detail.error_type || '—'}</p></div>
        <div><p className="text-muted-foreground">RAG 耗时</p><p className="mt-0.5">{detail.rag_ms !== null ? `${detail.rag_ms} ms` : '—'}</p></div>
        <div><p className="text-muted-foreground">标准化耗时</p><p className="mt-0.5">{detail.std_ms !== null ? `${detail.std_ms} ms` : '—'}</p></div>
      </div>

      {detail.degraded_reason && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700">
          降级原因：<span className="font-mono">{detail.degraded_reason}</span>
          —— 本次诊断按降级策略输出，请以人工复核为准。
        </div>
      )}

      <div>
        <p className="mb-1.5 text-sm font-semibold">AI 诊断结论（deepseek-v4-flash）</p>
        <DiagnosisPanel detail={detail} result={result} />
      </div>

      <Separator />

      <div>
        <p className="mb-1.5 text-sm font-semibold">召回链路（Milvus 向量召回 → 业务重排）</p>
        <CandidatesList detail={detail} />
      </div>

      <Separator />

      <div>
        <p className="mb-1.5 flex items-center gap-1.5 text-sm font-semibold">
          <FileJson className="h-4 w-4" />
          LLM 原始 JSON 输出
        </p>
        {detail.ai_output ? (
          <JsonPre data={detail.ai_output} />
        ) : (
          <p className="text-xs text-muted-foreground">暂无 JSON 输出</p>
        )}
      </div>

      <Separator />

      <div>
        <p className="mb-1.5 text-sm font-semibold">原始日志</p>
        <pre className="log-block max-h-40 overflow-y-auto">{detail.raw_log || '（无）'}</pre>
      </div>
      <div>
        <p className="mb-1.5 text-sm font-semibold">告警模板</p>
        <pre className="log-block">{detail.template || '（无）'}</pre>
      </div>
      <div>
        <p className="mb-1.5 text-sm font-semibold">拓扑快照</p>
        <pre className="log-block">{detail.topology || '（无）'}</pre>
      </div>

      <Separator />

      {perms?.can_feedback && (
        <div>
          <p className="mb-1.5 text-sm font-semibold">人工反馈闭环</p>
          <FeedbackSection detail={detail} />
        </div>
      )}
    </div>
  );
}

export default function EventsPage() {
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
    onMutate: (id) => toast(`诊断请求已发出（事件 #${id}），正在执行向量召回与 LLM 分析…`, { duration: 15000 }),
    onSuccess: (res) => {
      toast.success(res.message || 'AI 诊断完成');
      queryClient.invalidateQueries({ queryKey: ['event', selectedId] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
    },
    onError: (e) => toast.error(`诊断失败：${errDetail(e)}`, { duration: 15000 }),
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
        <h1 className="text-lg font-semibold tracking-tight">告警工作台</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          告警流检索、AI 根因诊断（deepseek-v4-flash）与人工反馈闭环。
        </p>
      </div>

      <FilterBar filters={filters} onChange={setFilters} />

      <div className="grid items-start gap-4 lg:grid-cols-5">
        {/* 左：事件列表 */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              告警列表
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                共 {total} 条{totalPages > 1 ? ` · 第 ${page + 1}/${totalPages} 页` : ''}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <StateGate
              loading={listQuery.isLoading}
              error={listQuery.isError ? errDetail(listQuery.error) : null}
              onRetry={() => listQuery.refetch()}
              isEmpty={items.length === 0}
              empty="没有符合条件的告警"
              emptyHint="尝试调整筛选条件或时间范围"
            >
              <div className="space-y-2">
                {items.map((e) => (
                  <EventRow key={e.id} e={e} active={e.id === selectedId} onClick={() => select(e.id)} />
                ))}
              </div>
              {totalPages > 1 && (
                <div className="mt-3 flex items-center justify-between">
                  <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                    上一页
                  </Button>
                  <span className="text-xs text-muted-foreground">{page + 1} / {totalPages}</span>
                  <Button variant="outline" size="sm" disabled={page >= totalPages - 1} onClick={() => setPage((p) => p + 1)}>
                    下一页
                  </Button>
                </div>
              )}
            </StateGate>
          </CardContent>
        </Card>

        {/* 右：详情（桌面） */}
        <Card className="hidden lg:col-span-3 lg:block">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">事件详情与诊断</CardTitle>
          </CardHeader>
          <CardContent>
            {selectedId === null ? (
              <p className="py-16 text-center text-sm text-muted-foreground">从左侧选择一条告警查看详情并诊断</p>
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
                {diagnoseMutation.isPending && <SpinnerLine text="正在执行向量召回与 LLM 诊断，通常需要数秒…" />}
              </StateGate>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 移动端详情抽屉 */}
      <Sheet open={mobileDetailOpen} onOpenChange={setMobileDetailOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-lg">
          <SheetTitle>事件详情与诊断</SheetTitle>
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
