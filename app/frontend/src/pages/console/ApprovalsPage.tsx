/** C8 审批中心：待我审批 / 我发起的 / 已处理，通过、拒绝、撤回与步骤时间线。 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ChevronDown, ChevronRight, CircleDot, Clock, Undo2, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
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
  ContentScanCard,
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

const STEP_ACTION_META: Record<string, { icon: typeof CircleDot; className: string }> = {
  pending: { icon: CircleDot, className: 'text-muted-foreground' },
  approved: { icon: CheckCircle2, className: 'text-teal-600' },
  rejected: { icon: XCircle, className: 'text-destructive' },
  withdrawn: { icon: Undo2, className: 'text-muted-foreground' },
  skipped: { icon: Undo2, className: 'text-muted-foreground' },
};

function StepTimeline({ request }: { request: ApprovalRequest }) {
  const { t } = useTranslation();
  return (
    <ol className="mt-3 space-y-2.5 border-l pl-4">
      {request.steps.map((s) => {
        const meta = STEP_ACTION_META[s.action] ?? STEP_ACTION_META.pending;
        const Icon = meta.icon;
        return (
          <li key={s.id} className="relative">
            <Icon className={cn('absolute -left-[21.5px] h-4 w-4 bg-card', meta.className)} />
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-medium">{t('approvals.stepLabel', { no: s.step_no, role: s.approver_role_label })}</span>
              <Badge variant={s.action === 'approved' ? 'default' : s.action === 'rejected' ? 'destructive' : 'outline'}>
                {t(`approvals.stepAction.${s.action}`, { defaultValue: s.action })}
              </Badge>
              {s.approver && <span className="text-muted-foreground">{s.approver}</span>}
              {s.acted_at && <span className="text-muted-foreground">{fmtTime(s.acted_at, false)}</span>}
            </div>
            {s.comment && <p className="mt-1 text-xs text-muted-foreground">{t('approvals.remark', { comment: s.comment })}</p>}
          </li>
        );
      })}
    </ol>
  );
}

/** 案例库全部字段的展示顺序（审批内容完整展示用，标签文案经 i18n 提供）。 */
const CASE_FIELD_ORDER = [
  'case_id',
  'error_type',
  'service_name',
  'cluster',
  'alert_template',
  'root_cause',
  'solution',
  'topology_snapshot',
  'status',
  'version',
  'feedback_score',
];

/** 完整案例字段卡片：按案例库字段顺序展示全部业务字段，未填写字段显式标注。 */
function CaseFieldsCard({ label, fields, missing }: { label: string; fields: Record<string, unknown>; missing?: string[] }) {
  const { t } = useTranslation();
  const fieldLabel = (f: string) => t(`approvals.caseFields.${f}`, { defaultValue: f });
  const missingSet = new Set(missing ?? []);
  return (
    <div className="rounded-md border bg-background p-2.5">
      <p className="mb-1.5 text-xs font-medium">{label}</p>
      <div className="space-y-1.5">
        {CASE_FIELD_ORDER.map((key) => {
          const v = fields[key];
          const empty = v === null || v === undefined || v === '';
          return (
            <div key={key} className="grid grid-cols-[84px_1fr] gap-x-2 text-xs">
              <span className="pt-0.5 text-muted-foreground">{fieldLabel(key)}</span>
              {empty ? (
                <span className="italic text-muted-foreground/70">{missingSet.has(key) ? t('approvals.notFilled') : '—'}</span>
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
  const { t } = useTranslation();
  return (
    <div className="rounded-md border bg-background p-2.5">
      <p className="mb-1.5 text-xs font-medium">
        {title}
        <span className="ml-1.5 font-normal text-muted-foreground">{t('approvals.sampleCount', { count: events.length })}</span>
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
  const { t, i18n } = useTranslation();
  const zh = i18n.language?.startsWith('zh');
  const query = useQuery({
    queryKey: ['approval-content', requestId],
    queryFn: () => consoleApi.getApprovalContent(requestId),
  });

  if (query.isLoading) return <SpinnerLine text={t('approvals.loadingContent')} />;
  if (query.isError) {
    return <ErrorBlock message={t('approvals.loadFailed', { error: errDetail(query.error) })} onRetry={() => query.refetch()} />;
  }

  const data = query.data;
  if (!data || !data.content) {
    return (
      <div className="rounded-md border border-dashed px-3 py-3 text-xs text-muted-foreground">
        {t('approvals.noContent')}
      </div>
    );
  }

  if (data.biz_type === 'kb_edit') {
    const cs = data.content as ChangeSet;
    const entries = diffEntries(cs.diff);
    return (
      <div className="space-y-2 rounded-md border bg-muted/30 p-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge variant="outline">{t('approvals.changeSetBadge', { id: cs.id })}</Badge>
          <span className="font-mono font-medium">{cs.case_id}</span>
          <span>{cs.change_type === 'create' ? t('approvals.changeCreate') : t('approvals.changeUpdate')}</span>
          {cs.version !== null && cs.version !== undefined && <Badge variant="outline">{t('approvals.targetVersion', { version: cs.version })}</Badge>}
          <StatusBadge status={cs.status} />
          <span className="text-muted-foreground">{cs.created_by}</span>
        </div>
        {cs.reason && <p className="text-xs text-muted-foreground">{t('approvals.changeReason', { reason: cs.reason })}</p>}
        {cs.content_scan && (
          <ContentScanCard
            scan={cs.content_scan}
            title={cs.content_scan.override ? t('approvals.scanTitleOverride') : t('approvals.scanTitle')}
          />
        )}
        {entries.length > 0 ? (
          <DiffTable entries={entries} />
        ) : (
          <p className="text-xs text-muted-foreground">{t('approvals.noDiff')}</p>
        )}
        {cs.change_type === 'create' && cs.full_case && (
          <CaseFieldsCard
            label={t('approvals.fullCaseCreateLabel')}
            fields={cs.full_case as unknown as Record<string, unknown>}
            missing={cs.full_case.missing_fields}
          />
        )}
        {cs.related_events && cs.related_events.length > 0 && (
          <EventSamplesCard title={t('approvals.relatedEventsTitle')} events={cs.related_events} />
        )}
      </div>
    );
  }

  if (data.biz_type === 'merge') {
    const p = data.content as MergeProposal;
    const listJoiner = zh ? '、' : ', ';
    return (
      <div className="space-y-2 rounded-md border bg-muted/30 p-3 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{t('approvals.mergeProposalBadge', { id: p.id })}</Badge>
          <span className="font-mono font-medium">{p.master_case_id}</span>
          <span className="text-muted-foreground">{t('approvals.masterCaseHint')}</span>
          <StatusBadge status={p.status} />
        </div>
        {p.master_case ? (
          <CaseFieldsCard
            label={t('approvals.masterCaseLabel', { id: p.master_case.case_id })}
            fields={p.master_case as unknown as Record<string, unknown>}
          />
        ) : (
          <p className="rounded-md border border-dashed px-2.5 py-1.5 text-muted-foreground">
            {t('approvals.masterMissing', { id: p.master_case_id })}
          </p>
        )}
        <p className="text-muted-foreground">
          {t('approvals.mergedCasesLine', { list: p.merged_case_ids.length > 0 ? p.merged_case_ids.join(listJoiner) : t('approvals.noneLabel') })}
        </p>
        {p.merged_cases && p.merged_cases.length > 0 && (
          <div className="space-y-2">
            {p.merged_cases.map((mc) =>
              'missing' in mc ? (
                <p key={mc.case_id} className="rounded-md border border-dashed px-2.5 py-1.5 text-muted-foreground">
                  {t('approvals.mergedCaseMissing', { id: mc.case_id })}
                </p>
              ) : (
                <CaseFieldsCard
                  key={mc.case_id}
                  label={t('approvals.mergedCaseLabel', { id: mc.case_id })}
                  fields={mc as unknown as Record<string, unknown>}
                />
              ),
            )}
          </div>
        )}
        {p.merge_strategy && (
          <div className="rounded-md border bg-background p-2.5">
            <p className="mb-1.5 font-medium">{t('approvals.mergeStrategyTitle')}</p>
            <JsonPre data={p.merge_strategy} />
          </div>
        )}
        {p.reason && <p className="text-muted-foreground">{t('approvals.mergeReason', { reason: p.reason })}</p>}
        <p className="text-muted-foreground">
          {t('approvals.initiatorLine', { by: p.created_by ?? '—', approval: p.approval_request_id ?? '—' })}
        </p>
      </div>
    );
  }

  const tpl = data.content as UnknownTemplate;
  return (
    <div className="space-y-2 rounded-md border bg-muted/30 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{t('approvals.unknownTemplateBadge', { id: tpl.id })}</Badge>
        <span>{t('approvals.sampleCountShort', { count: tpl.sample_count ?? 0 })}</span>
        <span className="text-muted-foreground">{t('approvals.lastSeenService', { service: tpl.last_seen_service || '—' })}</span>
        <StatusBadge status={tpl.status} />
      </div>
      <pre className="log-block">{tpl.template}</pre>
      <p>
        {t('approvals.promoteTargetLabel')}
        <span className="font-medium">{tpl.suggested_error_type || 'unknown'}</span>
        {t('approvals.promoteHint')}
      </p>
      {tpl.proposed_rule_entry && (
        <div className="rounded-md border bg-background p-2.5">
          <p className="mb-1.5 font-medium">{t('approvals.ruleEntryPreview')}</p>
          <JsonPre data={tpl.proposed_rule_entry} />
        </div>
      )}
      {tpl.related_events && tpl.related_events.length > 0 ? (
        <EventSamplesCard title={t('approvals.relatedEventsTitle')} events={tpl.related_events} />
      ) : (
        <p className="text-muted-foreground">{t('approvals.noRelatedEvents')}</p>
      )}
    </div>
  );
}

function DecisionDialog({ request, action, onClose }: {
  request: ApprovalRequest;
  action: 'approve' | 'reject';
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [comment, setComment] = useState('');
  const mutation = useMutation({
    mutationFn: () => consoleApi.decideApproval(request.id, action, comment),
    onSuccess: (res) => {
      toast.success(action === 'approve' ? t('approvals.toastApproved') : t('approvals.toastRejected'));
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
      queryClient.invalidateQueries({ queryKey: ['kb'] });
      onClose();
      void res;
    },
    onError: (e) => toast.error(t('approvals.toastActionFailed', { error: errDetail(e) })),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{action === 'approve' ? t('approvals.dialogTitleApprove') : t('approvals.dialogTitleReject')}</DialogTitle>
        <DialogDescription>
          {t('approvals.dialogTarget', { title: request.title, id: request.id })}
          {action === 'approve' && ` ${t('approvals.descApprove')}`}
          {action === 'reject' && ` ${t('approvals.descReject')}`}
        </DialogDescription>
      </DialogHeader>
      <div>
        <Label className="mb-1 text-xs">
          {t('approvals.commentLabel')}
          {action === 'reject' ? t('approvals.commentHintReject') : t('approvals.commentHintOptional')}
        </Label>
        <Textarea
          className="min-h-20 text-xs"
          placeholder={t('approvals.commentPlaceholder')}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>{t('approvals.cancel')}</Button>
        <Button
          variant={action === 'approve' ? 'default' : 'destructive'}
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
        >
          {mutation.isPending ? t('approvals.submitting') : action === 'approve' ? t('approvals.confirmApprove') : t('approvals.confirmReject')}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function ApprovalCard({ request, canDecide, myEmail }: { request: ApprovalRequest; canDecide: boolean; myEmail: string }) {
  const { t } = useTranslation();
  const [decision, setDecision] = useState<'approve' | 'reject' | null>(null);
  const [contentOpen, setContentOpen] = useState(false);
  const queryClient = useQueryClient();
  const withdrawMutation = useMutation({
    mutationFn: () => consoleApi.decideApproval(request.id, 'withdraw', ''),
    onSuccess: () => {
      toast.success(t('approvals.toastWithdrawn'));
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
    },
    onError: (e) => toast.error(t('approvals.toastWithdrawFailed', { error: errDetail(e) })),
  });

  const isApplicant = request.applicant === myEmail;
  const canWithdraw = isApplicant && request.status === 'pending';
  const canAct = canDecide && request.status === 'pending' && !isApplicant;

  return (
    <Card>
      <CardContent className="space-y-2 pt-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary">
            {t(`approvals.bizType.${request.biz_type}`, { defaultValue: request.biz_type })}
          </Badge>
          <span className="text-sm font-semibold">{request.title}</span>
          <StatusBadge status={request.status} className="ml-auto" />
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span>{t('approvals.applicant', { applicant: request.applicant, role: request.applicant_role_label })}</span>
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {fmtTime(request.created_at)}
          </span>
          {request.risk_level && <span>{t('approvals.riskLevel', { level: request.risk_level })}</span>}
          <span>{t('approvals.progress', { current: request.current_step, total: request.total_steps })}</span>
          <span>{t('approvals.requestId', { id: request.id })}</span>
        </div>
        {request.reason && <p className="text-xs text-muted-foreground">{t('approvals.requestReason', { reason: request.reason })}</p>}

        <div>
          <Button
            variant="ghost"
            size="sm"
            className="-ml-2 h-7 px-2 text-xs text-muted-foreground"
            onClick={() => setContentOpen((v) => !v)}
          >
            {contentOpen ? <ChevronDown className="mr-1 h-3.5 w-3.5" /> : <ChevronRight className="mr-1 h-3.5 w-3.5" />}
            {contentOpen ? t('approvals.collapseContent') : t('approvals.expandContent')}
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
                  {t('approvals.approveBtn')}
                </Button>
                <Button size="sm" variant="destructive" onClick={() => setDecision('reject')}>
                  <XCircle className="mr-1.5 h-3.5 w-3.5" />
                  {t('approvals.rejectBtn')}
                </Button>
              </>
            )}
            {canWithdraw && (
              <Button size="sm" variant="outline" onClick={() => withdrawMutation.mutate()} disabled={withdrawMutation.isPending}>
                <Undo2 className="mr-1.5 h-3.5 w-3.5" />
                {withdrawMutation.isPending ? t('approvals.withdrawing') : t('approvals.withdrawBtn')}
              </Button>
            )}
          </div>
        )}
        {isApplicant && request.status === 'pending' && (
          <p className="text-xs text-muted-foreground">{t('approvals.selfApprovalHint')}</p>
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
  const { t } = useTranslation();
  const { user } = useAuth();
  const myEmail = user?.email || user?.id || '';
  const query = useQuery({
    queryKey: ['approvals', box],
    queryFn: () => consoleApi.listApprovals(box),
  });

  if (query.isLoading) return <LoadingBlock rows={4} />;
  if (query.isError) {
    return <ErrorBlock message={t('approvals.listLoadFailed', { error: errDetail(query.error) })} onRetry={() => query.refetch()} />;
  }
  const items = query.data?.items ?? [];
  if (items.length === 0) {
    return (
      <EmptyBlock
        title={box === 'pending' ? t('approvals.emptyPending') : box === 'created' ? t('approvals.emptyCreated') : t('approvals.emptyDone')}
        hint={box === 'pending' ? t('approvals.emptyPendingHint') : undefined}
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
  const { t } = useTranslation();
  const perms = usePermissions();
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{t('approvals.title')}</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {t('approvals.subtitle')}
        </p>
      </div>
      <Tabs defaultValue="pending">
        <TabsList>
          <TabsTrigger value="pending">{t('approvals.tabPending')}</TabsTrigger>
          <TabsTrigger value="created">{t('approvals.tabCreated')}</TabsTrigger>
          <TabsTrigger value="done">{t('approvals.tabDone')}</TabsTrigger>
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
