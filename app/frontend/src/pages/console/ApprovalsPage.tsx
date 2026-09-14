/** C8 审批中心：待我审批 / 我发起的 / 已处理，通过、拒绝、撤回与步骤时间线。 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ChevronDown, ChevronRight, CircleDot, Clock, Undo2, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import {
  consoleApi,
  errDetail,
  type ApprovalEventSample,
  type ApprovalRequest,
  type ChangeSet,
  type KbCaseFullView,
  type MergeProposal,
  type UnknownTemplate,
} from '@/lib/console-api';
import {
  DiffTable,
  EmptyBlock,
  ErrorBlock,
  JsonPre,
  LoadingBlock,
  SeverityBadge,
  SpinnerLine,
  StatusBadge,
  diffEntries,
  fmtTime,
} from '@/components/console/shared';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/components/console/ConsoleLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

const BIZ_TYPE_LABEL: Record<string, string> = {
  kb_edit: '知识库变更',
  kb_rollback: '知识库回滚',
  kb_merge: '知识合并',
  merge: '知识合并',
  rule_publish: '规则发布',
  rule_promote: '模板晋升',
  template_promote: '模板晋升',
};

const STEP_ACTION_META: Record<string, { label: string; icon: typeof CircleDot; className: string }> = {
  pending: { label: '待审批', icon: CircleDot, className: 'text-muted-foreground' },
  approved: { label: '已通过', icon: CheckCircle2, className: 'text-teal-600' },
  rejected: { label: '已拒绝', icon: XCircle, className: 'text-destructive' },
  withdrawn: { label: '已撤回', icon: Undo2, className: 'text-muted-foreground' },
  skipped: { label: '已跳过', icon: Undo2, className: 'text-muted-foreground' },
};

function StepTimeline({ request }: { request: ApprovalRequest }) {
  return (
    <ol className="mt-3 space-y-2.5 border-l pl-4">
      {request.steps.map((s) => {
        const meta = STEP_ACTION_META[s.action] ?? STEP_ACTION_META.pending;
        const Icon = meta.icon;
        return (
          <li key={s.id} className="relative">
            <Icon className={cn('absolute -left-[21.5px] h-4 w-4 bg-card', meta.className)} />
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-medium">步骤 {s.step_no} · {s.approver_role_label}</span>
              <Badge variant={s.action === 'approved' ? 'default' : s.action === 'rejected' ? 'destructive' : 'outline'}>
                {meta.label}
              </Badge>
              {s.approver && <span className="text-muted-foreground">{s.approver}</span>}
              {s.acted_at && <span className="text-muted-foreground">{fmtTime(s.acted_at, false)}</span>}
            </div>
            {s.comment && <p className="mt-1 text-xs text-muted-foreground">备注：{s.comment}</p>}
          </li>
        );
      })}
    </ol>
  );
}

/** 案例库全部字段的展示顺序与中文标签（审批内容完整展示用）。 */
const CASE_FIELD_LABELS: Record<string, string> = {
  case_id: '案例 ID',
  error_type: '错误类型',
  service_name: '服务名',
  cluster: '集群',
  alert_template: '告警模板',
  root_cause: '根因',
  solution: '处置方案',
  topology_snapshot: '拓扑快照',
  status: '状态',
  version: '版本',
  feedback_score: '反馈分',
};

/** 完整案例字段卡片：按案例库字段顺序展示全部业务字段，未填写字段显式标注。 */
function CaseFieldsCard({ label, fields, missing }: { label: string; fields: Record<string, unknown>; missing?: string[] }) {
  const missingSet = new Set(missing ?? []);
  return (
    <div className="rounded-md border bg-background p-2.5">
      <p className="mb-1.5 text-xs font-medium">{label}</p>
      <div className="space-y-1.5">
        {Object.entries(CASE_FIELD_LABELS).map(([key, lbl]) => {
          const v = fields[key];
          const empty = v === null || v === undefined || v === '';
          return (
            <div key={key} className="grid grid-cols-[84px_1fr] gap-x-2 text-xs">
              <span className="pt-0.5 text-muted-foreground">{lbl}</span>
              {empty ? (
                <span className="italic text-muted-foreground/70">{missingSet.has(key) ? '（未填写）' : '—'}</span>
              ) : typeof v === 'string' && v.length > 60 ? (
                <pre className="log-block">{v}</pre>
              ) : (
                <span className="break-all font-mono">{String(v)}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** 关联日志实例样本卡片：作为审批证据展示，最多 5 条。 */
function EventSamplesCard({ title, events }: { title: string; events: ApprovalEventSample[] }) {
  return (
    <div className="rounded-md border bg-background p-2.5">
      <p className="mb-1.5 text-xs font-medium">
        {title}
        <span className="ml-1.5 font-normal text-muted-foreground">共 {events.length} 条（最近样本，证据用途）</span>
      </p>
      <div className="space-y-1.5">
        {events.map((ev) => (
          <div key={ev.event_id} className="rounded-md border border-dashed p-1.5">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-mono font-medium">{ev.event_id}</span>
              <span>{ev.service_name}</span>
              <SeverityBadge severity={ev.severity} />
              {ev.error_type && <Badge variant="outline">{ev.error_type}</Badge>}
              <span className="text-muted-foreground">{fmtTime(ev.created_at, true)}</span>
            </div>
            {ev.raw_log && <pre className="log-block mt-1">{ev.raw_log}</pre>}
          </div>
        ))}
      </div>
    </div>
  );
}

/** 审批业务内容：kb_edit 展示字段级 diff（create 另附案例库完整字段与日志样本），merge 展示合并双方完整案例与合并策略，rule_promote 展示模板、规则条目预览与日志样本。 */
function ApprovalContentSection({ requestId }: { requestId: number }) {
  const query = useQuery({
    queryKey: ['approval-content', requestId],
    queryFn: () => consoleApi.getApprovalContent(requestId),
  });

  if (query.isLoading) return <SpinnerLine text="加载审批内容…" />;
  if (query.isError) {
    return <ErrorBlock message={`审批内容加载失败：${errDetail(query.error)}`} onRetry={() => query.refetch()} />;
  }

  const data = query.data;
  if (!data || !data.content) {
    return (
      <div className="rounded-md border border-dashed px-3 py-3 text-xs text-muted-foreground">
        该审批单暂无可展示的业务内容。
      </div>
    );
  }

  if (data.biz_type === 'kb_edit') {
    const cs = data.content as ChangeSet;
    const entries = diffEntries(cs.diff);
    return (
      <div className="space-y-2 rounded-md border bg-muted/30 p-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge variant="outline">变更集 #{cs.id}</Badge>
          <span className="font-mono font-medium">{cs.case_id}</span>
          <span>{cs.change_type === 'create' ? '新建案例' : '更新案例'}</span>
          {cs.version !== null && cs.version !== undefined && <Badge variant="outline">目标版本 v{cs.version}</Badge>}
          <StatusBadge status={cs.status} />
          <span className="text-muted-foreground">{cs.created_by}</span>
        </div>
        {cs.reason && <p className="text-xs text-muted-foreground">变更理由：{cs.reason}</p>}
        {entries.length > 0 ? (
          <DiffTable entries={entries} />
        ) : (
          <p className="text-xs text-muted-foreground">该变更没有字段级差异。</p>
        )}
        {cs.change_type === 'create' && cs.full_case && (
          <CaseFieldsCard
            label="新建案例完整字段（案例库全部字段）"
            fields={cs.full_case as unknown as Record<string, unknown>}
            missing={cs.full_case.missing_fields}
          />
        )}
        {cs.related_events && cs.related_events.length > 0 && (
          <EventSamplesCard title="关联日志实例" events={cs.related_events} />
        )}
      </div>
    );
  }

  if (data.biz_type === 'merge') {
    const p = data.content as MergeProposal;
    return (
      <div className="space-y-2 rounded-md border bg-muted/30 p-3 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">合并提案 #{p.id}</Badge>
          <span className="font-mono font-medium">{p.master_case_id}</span>
          <span className="text-muted-foreground">（主案例，合并后保留）</span>
          <StatusBadge status={p.status} />
        </div>
        {p.master_case ? (
          <CaseFieldsCard
            label={`主案例完整内容：${p.master_case.case_id}`}
            fields={p.master_case as unknown as Record<string, unknown>}
          />
        ) : (
          <p className="rounded-md border border-dashed px-2.5 py-1.5 text-muted-foreground">
            主案例 {p.master_case_id} 暂不存在。
          </p>
        )}
        <p className="text-muted-foreground">
          归档冗余案例：{p.merged_case_ids.length > 0 ? p.merged_case_ids.join('、') : '（无）'}
        </p>
        {p.merged_cases && p.merged_cases.length > 0 && (
          <div className="space-y-2">
            {p.merged_cases.map((mc) =>
              'missing' in mc ? (
                <p key={mc.case_id} className="rounded-md border border-dashed px-2.5 py-1.5 text-muted-foreground">
                  案例 {mc.case_id} 已不存在（合并执行时将自动跳过）。
                </p>
              ) : (
                <CaseFieldsCard
                  key={mc.case_id}
                  label={`被合并案例完整内容：${mc.case_id}`}
                  fields={mc as unknown as Record<string, unknown>}
                />
              ),
            )}
          </div>
        )}
        {p.merge_strategy && (
          <div className="rounded-md border bg-background p-2.5">
            <p className="mb-1.5 font-medium">合并策略</p>
            <JsonPre data={p.merge_strategy} />
          </div>
        )}
        {p.reason && <p className="text-muted-foreground">合并理由：{p.reason}</p>}
        <p className="text-muted-foreground">
          发起人：{p.created_by ?? '—'} · 关联审批单：#{p.approval_request_id ?? '—'}
        </p>
      </div>
    );
  }

  const t = data.content as UnknownTemplate;
  return (
    <div className="space-y-2 rounded-md border bg-muted/30 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">未知模板 #{t.id}</Badge>
        <span>样本 {t.sample_count ?? 0} 条</span>
        <span className="text-muted-foreground">最近出现服务：{t.last_seen_service || '—'}</span>
        <StatusBadge status={t.status} />
      </div>
      <pre className="log-block">{t.template}</pre>
      <p>
        晋升目标分类：<span className="font-medium">{t.suggested_error_type || 'unknown'}</span>
        （终审通过后将自动追加规则条目并发布新版本）
      </p>
      {t.proposed_rule_entry && (
        <div className="rounded-md border bg-background p-2.5">
          <p className="mb-1.5 font-medium">晋升后新增规则条目（预览即所得）</p>
          <JsonPre data={t.proposed_rule_entry} />
        </div>
      )}
      {t.related_events && t.related_events.length > 0 ? (
        <EventSamplesCard title="关联日志实例" events={t.related_events} />
      ) : (
        <p className="text-muted-foreground">暂无匹配的日志实例（按标准化模板与服务名检索）。</p>
      )}
    </div>
  );
}

function DecisionDialog({ request, action, onClose }: {
  request: ApprovalRequest;
  action: 'approve' | 'reject';
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [comment, setComment] = useState('');
  const mutation = useMutation({
    mutationFn: () => consoleApi.decideApproval(request.id, action, comment),
    onSuccess: (res) => {
      toast.success(action === 'approve' ? '已通过审批' : '已拒绝审批');
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
      queryClient.invalidateQueries({ queryKey: ['kb'] });
      onClose();
      void res;
    },
    onError: (e) => toast.error(`审批操作失败：${errDetail(e)}`),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{action === 'approve' ? '通过审批' : '拒绝审批'}</DialogTitle>
        <DialogDescription>
          {request.title}（#{request.id}）
          {action === 'approve' && ' 通过后系统将自动执行发布动作。'}
          {action === 'reject' && ' 拒绝后变更集将回退为草稿状态。'}
        </DialogDescription>
      </DialogHeader>
      <div>
        <Label className="mb-1 text-xs">审批意见{action === 'reject' ? '（建议填写）' : '（可选）'}</Label>
        <Textarea
          className="min-h-20 text-xs"
          placeholder="例如：方案可行，注意发布后观察告警趋势"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>取消</Button>
        <Button
          variant={action === 'approve' ? 'default' : 'destructive'}
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
        >
          {mutation.isPending ? '提交中…' : action === 'approve' ? '确认通过' : '确认拒绝'}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function ApprovalCard({ request, canDecide, myEmail }: { request: ApprovalRequest; canDecide: boolean; myEmail: string }) {
  const [decision, setDecision] = useState<'approve' | 'reject' | null>(null);
  const [contentOpen, setContentOpen] = useState(false);
  const queryClient = useQueryClient();
  const withdrawMutation = useMutation({
    mutationFn: () => consoleApi.decideApproval(request.id, 'withdraw', ''),
    onSuccess: () => {
      toast.success('审批单已撤回');
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
    },
    onError: (e) => toast.error(`撤回失败：${errDetail(e)}`),
  });

  const isApplicant = request.applicant === myEmail;
  const canWithdraw = isApplicant && request.status === 'pending';
  const canAct = canDecide && request.status === 'pending' && !isApplicant;

  return (
    <Card>
      <CardContent className="space-y-2 pt-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{BIZ_TYPE_LABEL[request.biz_type] ?? request.biz_type}</Badge>
          <span className="text-sm font-semibold">{request.title}</span>
          <StatusBadge status={request.status} className="ml-auto" />
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span>申请人：{request.applicant}（{request.applicant_role_label}）</span>
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {fmtTime(request.created_at)}
          </span>
          {request.risk_level && <span>风险等级：{request.risk_level}</span>}
          <span>
            进度：{request.current_step}/{request.total_steps} 步
          </span>
          <span>单号：#{request.id}</span>
        </div>
        {request.reason && <p className="text-xs text-muted-foreground">申请理由：{request.reason}</p>}

        <div>
          <Button
            variant="ghost"
            size="sm"
            className="-ml-2 h-7 px-2 text-xs text-muted-foreground"
            onClick={() => setContentOpen((v) => !v)}
          >
            {contentOpen ? <ChevronDown className="mr-1 h-3.5 w-3.5" /> : <ChevronRight className="mr-1 h-3.5 w-3.5" />}
            {contentOpen ? '收起审批内容' : '查看审批内容与前后对比'}
          </Button>
          {contentOpen && <ApprovalContentSection requestId={request.id} />}
        </div>

        <StepTimeline request={request} />

        {(canAct || canWithdraw) && (
          <div className="flex items-center gap-2 pt-1">
            {canAct && (
              <>
                <Button size="sm" onClick={() => setDecision('approve')}>
                  <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
                  通过
                </Button>
                <Button size="sm" variant="destructive" onClick={() => setDecision('reject')}>
                  <XCircle className="mr-1.5 h-3.5 w-3.5" />
                  拒绝
                </Button>
              </>
            )}
            {canWithdraw && (
              <Button size="sm" variant="outline" onClick={() => withdrawMutation.mutate()} disabled={withdrawMutation.isPending}>
                <Undo2 className="mr-1.5 h-3.5 w-3.5" />
                {withdrawMutation.isPending ? '撤回中…' : '撤回申请'}
              </Button>
            )}
          </div>
        )}
        {isApplicant && request.status === 'pending' && (
          <p className="text-xs text-muted-foreground">你不能审批自己发起的申请。</p>
        )}

        {decision && (
          <Dialog open onOpenChange={(open) => !open && setDecision(null)}>
            <DecisionDialog request={request} action={decision} onClose={() => setDecision(null)} />
          </Dialog>
        )}
      </CardContent>
    </Card>
  );
}

function ApprovalListBox({ box, canDecide }: { box: 'pending' | 'created' | 'done'; canDecide: boolean }) {
  const { user } = useAuth();
  const myEmail = user?.email || user?.id || '';
  const query = useQuery({
    queryKey: ['approvals', box],
    queryFn: () => consoleApi.listApprovals(box),
  });

  if (query.isLoading) return <LoadingBlock rows={4} />;
  if (query.isError) {
    return <ErrorBlock message={`审批列表加载失败：${errDetail(query.error)}`} onRetry={() => query.refetch()} />;
  }
  const items = query.data?.items ?? [];
  if (items.length === 0) {
    return (
      <EmptyBlock
        title={box === 'pending' ? '没有待你审批的请求' : box === 'created' ? '你还没有发起过审批' : '暂无已处理记录'}
        hint={box === 'pending' ? '新的知识库变更、合并或规则发布请求会出现在这里' : undefined}
      />
    );
  }
  return (
    <div className="space-y-3">
      {items.map((r) => (
        <ApprovalCard key={r.id} request={r} canDecide={canDecide} myEmail={myEmail} />
      ))}
    </div>
  );
}

export default function ApprovalsPage() {
  const perms = usePermissions();
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">审批中心</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          知识库变更、合并与规则发布的审批流；申请人不能审批自己的申请，通过后系统自动执行发布。
        </p>
      </div>
      <Tabs defaultValue="pending">
        <TabsList>
          <TabsTrigger value="pending">待我审批</TabsTrigger>
          <TabsTrigger value="created">我发起的</TabsTrigger>
          <TabsTrigger value="done">我已处理</TabsTrigger>
        </TabsList>
        <TabsContent value="pending" className="mt-4">
          <ApprovalListBox box="pending" canDecide={!!perms?.can_approve} />
        </TabsContent>
        <TabsContent value="created" className="mt-4">
          <ApprovalListBox box="created" canDecide={false} />
        </TabsContent>
        <TabsContent value="done" className="mt-4">
          <ApprovalListBox box="done" canDecide={false} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
