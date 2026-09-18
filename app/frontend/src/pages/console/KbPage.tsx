/** C6/C7 知识库：案例检索与编辑审批、版本回滚、变更记录 diff、去重合并。 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { GitMerge, PencilLine, Plus, ScanSearch, ScrollText, Undo2, X } from 'lucide-react';
import { toast } from 'sonner';
import {
  consoleApi,
  errDetail,
  extractContentGuardScan,
  type ChangeSet,
  type EventSample,
  type KbCase,
  type KbCaseDetail,
  type MergeGroup,
  type MergeProposal,
} from '@/lib/console-api';
import {
  ContentScanCard,
  DiffTable,
  EmptyBlock,
  SeverityBadge,
  SpinnerLine,
  StateGate,
  StatusBadge,
  diffEntries,
  fmtTime,
  type ContentScanResult,
  type DiffEntry,
} from '@/components/console/shared';
import { usePermissions } from '@/components/console/ConsoleLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

const EDITABLE_FIELDS = ['alert_template', 'root_cause', 'solution', 'cluster', 'topology_snapshot'] as const;

/** 案例关联告警实例日志列表（案例详情 / 新建案例预览共用）。 */
function EventSampleList({
  events,
  loading,
  emptyHint,
}: {
  events: EventSample[];
  loading?: boolean;
  emptyHint?: string;
}) {
  const { t } = useTranslation();
  if (loading) return <SpinnerLine text={t('kb.sampleList.loading')} />;
  if (events.length === 0) {
    return (
      <p className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
        {emptyHint ?? t('kb.sampleList.empty')}
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {events.map((ev) => (
        <li key={ev.event_id} className="rounded-md border p-2.5 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="font-mono">{ev.event_id}</Badge>
            <SeverityBadge severity={ev.severity} />
            {ev.status && <StatusBadge status={ev.status} />}
            <span className="text-muted-foreground">{ev.service_name}</span>
            {ev.error_type && <Badge variant="outline">{ev.error_type}</Badge>}
            <span className="ml-auto text-muted-foreground">{fmtTime(ev.created_at)}</span>
          </div>
          {ev.raw_log && <pre className="log-block mt-1.5 max-h-24 overflow-y-auto">{ev.raw_log}</pre>}
        </li>
      ))}
    </ul>
  );
}

// ------------------ 编辑 / 新建对话框 ------------------

/** 内容安全误报放行交互：展示拦截扫描结果并收集放行理由（写入审计日志）。 */
function OverrideBlock({ scan, idPrefix, allow, onAllow, reason, onReason }: {
  scan: ContentScanResult;
  idPrefix: string;
  allow: boolean;
  onAllow: (v: boolean) => void;
  reason: string;
  onReason: (v: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2.5">
      <ContentScanCard scan={scan} title={t('kb.override.scanTitle')} />
      <div className="rounded-md border bg-secondary/40 p-3">
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t('kb.override.description')}
        </p>
        <RadioGroup
          className="mt-2.5 flex flex-wrap gap-4"
          value={allow ? 'override' : 'blocked'}
          onValueChange={(v) => onAllow(v === 'override')}
        >
          <div className="flex items-center space-x-2">
            <RadioGroupItem value="blocked" id={`${idPrefix}-keep-blocked`} />
            <Label htmlFor={`${idPrefix}-keep-blocked`} className="text-xs">{t('kb.override.keepBlocked')}</Label>
          </div>
          <div className="flex items-center space-x-2">
            <RadioGroupItem value="override" id={`${idPrefix}-allow-override`} />
            <Label htmlFor={`${idPrefix}-allow-override`} className="text-xs">{t('kb.override.allowOverride')}</Label>
          </div>
        </RadioGroup>
        {allow && (
          <Input
            className="mt-2.5 h-9 text-xs"
            placeholder={t('kb.override.reasonPlaceholder')}
            value={reason}
            onChange={(e) => onReason(e.target.value)}
          />
        )}
      </div>
    </div>
  );
}

function EditCaseDialog({ detail, onClose }: { detail: KbCaseDetail; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const perms = usePermissions();
  const isCreate = false;
  const fieldLabel = (f: string) => t(`kb.fields.${f}`, { defaultValue: f });
  const [form, setForm] = useState<Record<string, string>>(() =>
    Object.fromEntries(EDITABLE_FIELDS.map((f) => [f, (detail.case as unknown as Record<string, string>)[f] ?? ''])),
  );
  const [reason, setReason] = useState('');
  // 内容安全拦截后的误报放行状态：扫描结果、是否放行、放行理由
  const [guardScan, setGuardScan] = useState<ContentScanResult | null>(null);
  const [allowOverride, setAllowOverride] = useState(false);
  const [overrideReason, setOverrideReason] = useState('');

  const changedEntries: DiffEntry[] = EDITABLE_FIELDS.filter((f) => form[f] !== ((detail.case as unknown as Record<string, string>)[f] ?? '')).map(
    (f) => ({
      key: fieldLabel(f),
      before: (detail.case as unknown as Record<string, string>)[f] ?? '',
      after: form[f],
    }),
  );

  const mutation = useMutation({
    mutationFn: () =>
      consoleApi.createChangeSet({
        case_id: detail.case.case_id,
        change_type: 'update',
        fields: Object.fromEntries(
          EDITABLE_FIELDS.filter((f) => form[f] !== ((detail.case as unknown as Record<string, string>)[f] ?? ''))
            .map((f) => [f, form[f]]),
        ),
        reason,
        ...(allowOverride ? { allow_override: true, override_reason: overrideReason.trim() } : {}),
      }),
    onSuccess: (res) => {
      if (res.content_scan && res.content_scan.hits.length > 0) {
        toast.info(t('kb.edit.scanHitToast', {
          count: res.content_scan.hits.length,
          level: res.content_scan.risk_level === 'high' ? t('kb.scan.overrideLevel') : t('kb.scan.lowRiskLevel'),
        }));
      }
      toast.success(
        res.auto_published
          ? t('kb.edit.publishedToast', { caseId: detail.case.case_id, version: res.change_set.version })
          : t('kb.edit.approvalToast', { id: res.approval_request_id }),
      );
      queryClient.invalidateQueries({ queryKey: ['kb-case', detail.case.case_id] });
      queryClient.invalidateQueries({ queryKey: ['kb-cases'] });
      queryClient.invalidateQueries({ queryKey: ['change-sets'] });
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      onClose();
    },
    onError: (e) => {
      const scan = extractContentGuardScan(e);
      if (scan) {
        setGuardScan(scan);
        setAllowOverride(false);
        setOverrideReason('');
        toast.error(t('kb.edit.blockedToast'), { duration: 8000 });
      } else {
        toast.error(t('kb.edit.errorToast', { error: errDetail(e) }));
      }
    },
  });

  const disabled =
    mutation.isPending ||
    !reason.trim() ||
    changedEntries.length === 0 ||
    !perms?.can_edit_kb ||
    (guardScan !== null && allowOverride && !overrideReason.trim());

  return (
    <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
      <DialogHeader>
        <DialogTitle>{isCreate ? t('kb.create.title') : t('kb.edit.title', { caseId: detail.case.case_id })}</DialogTitle>
        <DialogDescription>
          {t('kb.edit.versionLine', {
            version: detail.case.version ?? 1,
            mode: perms?.approval_mode === 'OFF'
              ? t('layout.approvalMode.OFF')
              : perms?.approval_mode === 'SINGLE_REVIEW'
                ? t('layout.approvalMode.SINGLE_REVIEW')
                : t('layout.approvalMode.MULTI_LEVEL'),
          })}
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        {EDITABLE_FIELDS.map((f) => (
          <div key={f}>
            <Label className="mb-1 text-xs">{fieldLabel(f)}</Label>
            {f === 'root_cause' || f === 'solution' || f === 'alert_template' || f === 'topology_snapshot' ? (
              <Textarea
                className="min-h-16 font-mono text-xs"
                value={form[f]}
                onChange={(e) => setForm((s) => ({ ...s, [f]: e.target.value }))}
              />
            ) : (
              <Input
                className="h-9 text-xs"
                value={form[f]}
                onChange={(e) => setForm((s) => ({ ...s, [f]: e.target.value }))}
              />
            )}
          </div>
        ))}
        <div>
          <Label className="mb-1 text-xs">{t('kb.edit.reasonLabel')}</Label>
          <Input
            className="h-9 text-xs"
            placeholder={t('kb.edit.reasonPlaceholder')}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        {changedEntries.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">{t('kb.edit.previewLabel')}</p>
            <DiffTable entries={changedEntries} />
          </div>
        )}
        {guardScan && (
          <OverrideBlock
            scan={guardScan}
            idPrefix="kb-edit-override"
            allow={allowOverride}
            onAllow={setAllowOverride}
            reason={overrideReason}
            onReason={setOverrideReason}
          />
        )}
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>{t('kb.actions.cancel')}</Button>
        <Button onClick={() => mutation.mutate()} disabled={disabled}>
          <PencilLine className="mr-1.5 h-3.5 w-3.5" />
          {mutation.isPending ? t('kb.actions.submitting') : guardScan && allowOverride ? t('kb.edit.submitOverride') : t('kb.edit.submit')}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function CreateCaseDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // 新建案例与案例库保持完整字段结构：error_type/service_name 必填，其余可留空
  const [form, setForm] = useState({
    error_type: '',
    service_name: '',
    cluster: '',
    alert_template: '',
    root_cause: '',
    solution: '',
    topology_snapshot: '',
  });
  const [reason, setReason] = useState('');
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  // 内容安全拦截后的误报放行状态：扫描结果、是否放行、放行理由
  const [guardScan, setGuardScan] = useState<ContentScanResult | null>(null);
  const [allowOverride, setAllowOverride] = useState(false);
  const [overrideReason, setOverrideReason] = useState('');
  const evidenceQuery = useQuery({
    queryKey: ['kb-related-events', form.alert_template.trim(), form.service_name.trim()],
    queryFn: () =>
      consoleApi.previewKbRelatedEvents(
        form.alert_template.trim() || undefined,
        form.service_name.trim() || undefined,
      ),
    enabled: evidenceOpen,
  });
  const mutation = useMutation({
    mutationFn: () =>
      consoleApi.createChangeSet({
        // 案例 ID 由后端自动生成：KB-YYYYMMDD-当日序号，前端无需填写
        case_id: '',
        change_type: 'create',
        fields: Object.fromEntries(
          Object.entries(form)
            .filter(([, v]) => v.trim())
            .map(([k, v]) => [k, v.trim()]),
        ),
        reason,
        ...(allowOverride ? { allow_override: true, override_reason: overrideReason.trim() } : {}),
      }),
    onSuccess: (res) => {
      const caseId = res.change_set.case_id;
      if (res.content_scan && res.content_scan.hits.length > 0) {
        toast.info(t('kb.create.scanHitToast', {
          count: res.content_scan.hits.length,
          level: res.content_scan.risk_level === 'high' ? t('kb.scan.overrideLevel') : t('kb.scan.lowRiskLevel'),
        }));
      }
      toast.success(
        res.auto_published
          ? t('kb.create.publishedToast', { caseId })
          : t('kb.create.approvalToast', { id: res.approval_request_id, caseId }),
      );
      queryClient.invalidateQueries({ queryKey: ['kb-cases'] });
      queryClient.invalidateQueries({ queryKey: ['change-sets'] });
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      onClose();
    },
    onError: (e) => {
      const scan = extractContentGuardScan(e);
      if (scan) {
        setGuardScan(scan);
        setAllowOverride(false);
        setOverrideReason('');
        toast.error(t('kb.create.blockedToast'), { duration: 8000 });
      } else {
        toast.error(t('kb.create.errorToast', { error: errDetail(e) }));
      }
    },
  });

  const valid = Boolean(
    form.error_type.trim() && form.service_name.trim() && reason.trim() &&
    (!guardScan || !allowOverride || overrideReason.trim()),
  );

  return (
    <DialogContent className="max-h-[85vh] max-w-xl overflow-y-auto">
      <DialogHeader>
        <DialogTitle>{t('kb.create.title')}</DialogTitle>
        <DialogDescription>{t('kb.create.description')}</DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        <p className="rounded-md bg-muted/50 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          {t('kb.create.idHintBefore')} <span className="font-mono">{t('kb.create.idRule')}</span> {t('kb.create.idHintAfter')}
        </p>
        <div>
          <Label className="mb-1 text-xs">{t('kb.create.errorTypeRequired')}</Label>
          <Input
            className="h-9 font-mono text-xs"
            placeholder="gateway_502"
            value={form.error_type}
            onChange={(e) => setForm((s) => ({ ...s, error_type: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">{t('kb.create.serviceRequired')}</Label>
          <Input
            className="h-9 font-mono text-xs"
            placeholder="payment-service"
            value={form.service_name}
            onChange={(e) => setForm((s) => ({ ...s, service_name: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">{t('kb.fields.cluster')}</Label>
          <Input
            className="h-9 text-xs"
            placeholder="prod-cluster-01"
            value={form.cluster}
            onChange={(e) => setForm((s) => ({ ...s, cluster: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">{t('kb.fields.alert_template')}</Label>
          <Textarea
            className="min-h-16 font-mono text-xs"
            placeholder="upstream sent too big header while reading response header from upstream"
            value={form.alert_template}
            onChange={(e) => setForm((s) => ({ ...s, alert_template: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">{t('kb.fields.root_cause')}</Label>
          <Textarea
            className="min-h-16 text-xs"
            value={form.root_cause}
            onChange={(e) => setForm((s) => ({ ...s, root_cause: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">{t('kb.fields.solution')}</Label>
          <Textarea
            className="min-h-16 text-xs"
            value={form.solution}
            onChange={(e) => setForm((s) => ({ ...s, solution: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">{t('kb.fields.topology_snapshot')}</Label>
          <Textarea
            className="min-h-16 font-mono text-xs"
            placeholder="ingress → gateway(payment) → payment-api → mysql"
            value={form.topology_snapshot}
            onChange={(e) => setForm((s) => ({ ...s, topology_snapshot: e.target.value }))}
          />
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <Label className="text-xs">{t('kb.create.evidenceLabel')}</Label>
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              disabled={!form.alert_template.trim() && !form.service_name.trim()}
              onClick={() => setEvidenceOpen(true)}
            >
              <ScrollText className="mr-1 h-3 w-3" />
              {evidenceOpen ? t('kb.create.refreshPreview') : t('kb.create.queryLogs')}
            </Button>
          </div>
          {!evidenceOpen ? (
            <p className="text-xs text-muted-foreground">
              {t('kb.create.evidenceHint')}
            </p>
          ) : (
            <EventSampleList
              loading={evidenceQuery.isFetching}
              events={evidenceQuery.data?.items ?? []}
              emptyHint={t('kb.create.evidenceEmpty')}
            />
          )}
        </div>
        <div>
          <Label className="mb-1 text-xs">{t('kb.create.reasonLabel')}</Label>
          <Input
            className="h-9 text-xs"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        {guardScan && (
          <OverrideBlock
            scan={guardScan}
            idPrefix="kb-create-override"
            allow={allowOverride}
            onAllow={setAllowOverride}
            reason={overrideReason}
            onReason={setOverrideReason}
          />
        )}
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>{t('kb.actions.cancel')}</Button>
        <Button onClick={() => mutation.mutate()} disabled={!valid || mutation.isPending}>
          {mutation.isPending ? t('kb.actions.submitting') : guardScan && allowOverride ? t('kb.create.submitOverride') : t('kb.create.submit')}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ------------------ 案例详情对话框 ------------------

function CaseDetailDialog({ caseId, onClose }: { caseId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const perms = usePermissions();
  const [editOpen, setEditOpen] = useState(false);
  const detailQuery = useQuery({
    queryKey: ['kb-case', caseId],
    queryFn: () => consoleApi.getKbCase(caseId),
  });

  const rollbackMutation = useMutation({
    mutationFn: (version: number) => consoleApi.rollbackCase(caseId, version),
    onSuccess: (res) => {
      toast.success(t('kb.detail.rollbackToast', { version: res.version }));
      queryClient.invalidateQueries({ queryKey: ['kb-case', caseId] });
      queryClient.invalidateQueries({ queryKey: ['kb-cases'] });
      queryClient.invalidateQueries({ queryKey: ['change-sets'] });
    },
    onError: (e) => toast.error(t('kb.detail.rollbackErrorToast', { error: errDetail(e) })),
  });

  const canRollback = (perms?.level ?? 0) >= 3; // kb_admin 及以上

  return (
    <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
      <DialogHeader>
        <DialogTitle className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{caseId}</span>
          <StateGate
            loading={detailQuery.isLoading}
            error={detailQuery.isError ? errDetail(detailQuery.error) : null}
          >
            <StatusBadge status={detailQuery.data?.case.status} />
            <Badge variant="outline">v{detailQuery.data?.case.version ?? '—'}</Badge>
          </StateGate>
        </DialogTitle>
        <DialogDescription>
          {detailQuery.data?.case.error_type} · {detailQuery.data?.case.service_name}
          {detailQuery.data?.case.cluster ? ` · ${detailQuery.data.case.cluster}` : ''} · {t('kb.detail.feedback', { score: detailQuery.data?.case.feedback_score ?? 0 })}
        </DialogDescription>
      </DialogHeader>

      <StateGate
        loading={detailQuery.isLoading}
        error={detailQuery.isError ? errDetail(detailQuery.error) : null}
        onRetry={() => detailQuery.refetch()}
      >
        {detailQuery.data && (
          <div className="space-y-4">
            {perms?.can_edit_kb && (
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={() => setEditOpen(true)}>
                  <PencilLine className="mr-1.5 h-3.5 w-3.5" />
                  {t('kb.detail.edit')}
                </Button>
              </div>
            )}

            <div>
              <p className="mb-1.5 text-sm font-semibold">{t('kb.detail.currentContent')}</p>
              <div className="space-y-2 text-xs">
                <div>
                  <p className="font-medium text-muted-foreground">{t('kb.fields.alert_template')}</p>
                  <pre className="log-block">{detailQuery.data.case.alert_template || t('kb.detail.none')}</pre>
                </div>
                <div>
                  <p className="font-medium text-muted-foreground">{t('kb.fields.root_cause')}</p>
                  <pre className="log-block">{detailQuery.data.case.root_cause || t('kb.detail.none')}</pre>
                </div>
                <div>
                  <p className="font-medium text-muted-foreground">{t('kb.fields.solution')}</p>
                  <pre className="log-block">{detailQuery.data.case.solution || t('kb.detail.none')}</pre>
                </div>
                {detailQuery.data.case.topology_snapshot && (
                  <div>
                    <p className="font-medium text-muted-foreground">{t('kb.fields.topology_snapshot')}</p>
                    <pre className="log-block">{detailQuery.data.case.topology_snapshot}</pre>
                  </div>
                )}
              </div>
            </div>

            <Separator />

            <div>
              <p className="mb-1.5 text-sm font-semibold">{t('kb.detail.relatedEvents', { count: detailQuery.data.related_events?.length ?? 0 })}</p>
              <EventSampleList
                events={detailQuery.data.related_events ?? []}
                emptyHint={t('kb.detail.relatedEventsEmpty')}
              />
            </div>

            <Separator />

            <div>
              <p className="mb-1.5 text-sm font-semibold">{t('kb.detail.versions', { count: detailQuery.data.versions.length })}</p>
              <ul className="space-y-1.5">
                {detailQuery.data.versions.map((v) => (
                  <li key={v.id} className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs">
                    <Badge variant={v.version === detailQuery.data?.case.version ? 'default' : 'outline'}>
                      v{v.version}
                      {v.version === detailQuery.data?.case.version ? t('kb.detail.currentBadge') : ''}
                    </Badge>
                    <span className="text-muted-foreground">{v.created_by || t('kb.detail.system')}</span>
                    <span className="text-muted-foreground">{fmtTime(v.created_at)}</span>
                    {v.version !== detailQuery.data?.case.version && canRollback && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="ml-auto h-7 px-2 text-xs"
                        onClick={() => rollbackMutation.mutate(v.version)}
                        disabled={rollbackMutation.isPending}
                      >
                        <Undo2 className="mr-1 h-3 w-3" />
                        {t('kb.detail.rollback')}
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>

            <Separator />

            <div>
              <p className="mb-1.5 text-sm font-semibold">{t('kb.detail.changeSets', { count: detailQuery.data.change_sets.length })}</p>
              <ul className="space-y-2.5">
                {detailQuery.data.change_sets.map((cs) => (
                  <li key={cs.id} className="rounded-md border p-3 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={cs.status} />
                      <span className="font-medium">{cs.change_type === 'create' ? t('kb.page.changeCreate') : t('kb.page.changeUpdate')}</span>
                      {cs.version && <Badge variant="outline">v{cs.version}</Badge>}
                      <span className="text-muted-foreground">{cs.created_by}</span>
                      <span className="text-muted-foreground">{fmtTime(cs.created_at)}</span>
                    </div>
                    {cs.reason && <p className="mt-1 text-muted-foreground">{t('kb.page.reason', { reason: cs.reason })}</p>}
                    <div className="mt-2">
                      <DiffTable entries={diffEntries(cs.diff)} />
                    </div>
                  </li>
                ))}
                {detailQuery.data.change_sets.length === 0 && (
                  <p className="text-xs text-muted-foreground">{t('kb.page.noChangeSets')}</p>
                )}
              </ul>
            </div>
          </div>
        )}
      </StateGate>

      {editOpen && detailQuery.data && (
        <Dialog open onOpenChange={(open) => !open && setEditOpen(false)}>
          <EditCaseDialog detail={detailQuery.data} onClose={() => setEditOpen(false)} />
        </Dialog>
      )}
    </DialogContent>
  );
}

// ------------------ 去重合并 ------------------

// 主案例与候选案例的模板相似度（0-1 → 百分数；无数据返回 null）
function simPct(group: MergeGroup, master: string, caseId: string): number | null {
  const v = group.similarities?.[master]?.[caseId];
  return typeof v === 'number' ? Math.round(v * 100) : null;
}

function MergeGroupCard({
  group,
  canMerge,
  onCreate,
  onCancel,
}: {
  group: MergeGroup;
  canMerge: boolean;
  onCreate: (master: string, merged: string[], reason: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [master, setMaster] = useState(group.suggested_master);
  const [reason, setReason] = useState('');
  const [confirmCancel, setConfirmCancel] = useState(false);
  // 主案例回退：状态值不在当前组时回落到建议主案例或首个案例
  const effectiveMaster = group.cases.some((c) => c.case_id === master)
    ? master
    : (group.cases.find((c) => c.case_id === group.suggested_master)?.case_id ?? group.cases[0]?.case_id ?? '');
  const merged = group.cases.filter((c) => c.case_id !== effectiveMaster).map((c) => c.case_id);

  return (
    <>
      <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-mono">{group.error_type}</span>
          <span className="font-normal text-muted-foreground">
            {group.service_name} · {t('kb.merge.similarCount', { count: group.case_ids.length })}
            {typeof group.min_pair_similarity === 'number' &&
              ` · ${t('kb.merge.minSimilarity', { pct: Math.round(group.min_pair_similarity * 100) })}`}
          </span>
          {canMerge && (
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto h-7 shrink-0 px-2 text-xs text-muted-foreground hover:text-destructive"
              title={t('kb.merge.cancelTitle')}
              onClick={() => setConfirmCancel(true)}
            >
              <X className="mr-0.5 h-3 w-3" />
              {t('kb.merge.cancelBtn')}
            </Button>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <RadioGroup value={effectiveMaster} onValueChange={setMaster} className="gap-2">
          {group.cases.map((c) => (
            <label
              key={c.case_id}
              className={cn(
                'flex cursor-pointer items-start gap-2.5 rounded-md border p-2.5 text-xs transition-colors',
                effectiveMaster === c.case_id ? 'border-primary/60 bg-accent' : 'hover:bg-accent/50',
              )}
            >
              <RadioGroupItem value={c.case_id} className="mt-0.5" />
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono font-medium">{c.case_id}</span>
                  {c.case_id === group.suggested_master && <Badge variant="secondary">{t('kb.merge.suggestedMaster')}</Badge>}
                  <span className="text-muted-foreground">{t('kb.merge.feedbackVersion', { score: c.feedback_score ?? 0, version: c.version })}</span>
                  {c.case_id !== effectiveMaster && simPct(group, effectiveMaster, c.case_id) !== null && (
                    <Badge variant="outline" className="font-normal">
                      {t('kb.merge.similarityToMaster', { pct: simPct(group, effectiveMaster, c.case_id) })}
                    </Badge>
                  )}
                </p>
                <p className="mt-1 line-clamp-2 text-muted-foreground">{c.root_cause || t('kb.merge.noRootCause')}</p>
              </div>
            </label>
          ))}
        </RadioGroup>
        {canMerge && (
          <>
            <Input
              className="h-9 text-xs"
              placeholder={t('kb.merge.reasonPlaceholder')}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <Button
              size="sm"
              disabled={!reason.trim() || merged.length === 0}
              onClick={() => onCreate(effectiveMaster, merged, reason.trim())}
            >
              <GitMerge className="mr-1.5 h-3.5 w-3.5" />
              {t('kb.merge.mergeTo', { master: effectiveMaster, count: merged.length })}
            </Button>
          </>
        )}
      </CardContent>
      </Card>
      <AlertDialog open={confirmCancel} onOpenChange={setConfirmCancel}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('kb.merge.confirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('kb.merge.confirmDescription', { errorType: group.error_type, service: group.service_name })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('kb.merge.keepMerging')}</AlertDialogCancel>
            <AlertDialogAction onClick={() => onCancel()}>{t('kb.merge.confirmCancel')}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function MergeTab() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const perms = usePermissions();
  const canMerge = !!perms?.can_edit_kb;
  const [scanEnabled, setScanEnabled] = useState(false);
  // 取消以「扫描结果组」为单位：整组退出本次合并流程，可随时恢复，不影响案例库数据
  const [cancelledGroupKeys, setCancelledGroupKeys] = useState<string[]>([]);
  const scanQuery = useQuery({
    queryKey: ['kb-duplicates'],
    queryFn: () => consoleApi.scanDuplicates(),
    enabled: scanEnabled,
  });
  const proposalsQuery = useQuery({
    queryKey: ['merge-proposals'],
    queryFn: () => consoleApi.listMergeProposals(),
  });

  const createMutation = useMutation({
    mutationFn: (body: { master_case_id: string; merged_case_ids: string[]; reason: string }) =>
      consoleApi.createMergeProposal(body),
    onSuccess: (res) => {
      toast.success(
        res.auto_merged
          ? t('kb.merge.autoMergedToast')
          : t('kb.merge.approvalToast', { id: res.approval_request_id }),
      );
      queryClient.invalidateQueries({ queryKey: ['merge-proposals'] });
      queryClient.invalidateQueries({ queryKey: ['kb-duplicates'] });
      queryClient.invalidateQueries({ queryKey: ['kb-cases'] });
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
    },
    onError: (e) => toast.error(t('kb.merge.errorToast', { error: errDetail(e) })),
  });

  const groups = scanQuery.data?.groups ?? [];
  const groupKey = (g: MergeGroup) => g.case_ids.join('|');
  const proposals = proposalsQuery.data?.items ?? [];
  // 在途（pending）合并提案覆盖的案例组即时隐藏：提交合并审批后卡片立即消失，
  // 与后端扫描排除口径一致；提案被拒绝后不再是 pending，案例组自动恢复展示。
  const pendingCovered = new Set(
    proposals
      .filter((p: MergeProposal) => p.status === 'pending')
      .flatMap((p: MergeProposal) => [p.master_case_id, ...p.merged_case_ids]),
  );
  const activeGroups = groups.filter(
    (g) => !cancelledGroupKeys.includes(groupKey(g)) && !g.case_ids.some((id) => pendingCovered.has(id)),
  );
  const cancelledGroups = groups.filter((g) => cancelledGroupKeys.includes(groupKey(g)));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" variant="outline" onClick={() => setScanEnabled(true)} disabled={scanQuery.isFetching}>
          <ScanSearch className="mr-1.5 h-3.5 w-3.5" />
          {scanQuery.isFetching ? t('kb.merge.scanning') : t('kb.merge.scanBtn')}
        </Button>
        <span className="text-xs text-muted-foreground">
          {t('kb.merge.scanHint')}
        </span>
      </div>

      {scanEnabled && (
        <StateGate
          loading={scanQuery.isLoading}
          error={scanQuery.isError ? errDetail(scanQuery.error) : null}
          onRetry={() => scanQuery.refetch()}
          isEmpty={groups.length === 0}
          empty={t('kb.merge.noClusters')}
          emptyHint={t('kb.merge.noClustersHint')}
        >
          <div className="space-y-4">
            {cancelledGroups.length > 0 && (
              <Card className="border-dashed border-amber-500/40">
                <CardContent className="space-y-2 pt-4 text-xs">
                  <p className="font-medium text-amber-600">
                    {t('kb.merge.cancelledTitle', { count: cancelledGroups.length })}
                  </p>
                  <ul className="space-y-1.5">
                    {cancelledGroups.map((g) => (
                      <li key={groupKey(g)} className="flex flex-wrap items-center gap-2 text-muted-foreground">
                        <span className="font-mono text-foreground">{g.error_type}</span>
                        <span>
                          {g.service_name} · {t('kb.merge.similarCount', { count: g.case_ids.length })}
                        </span>
                        {canMerge && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 px-2 text-xs"
                            onClick={() => setCancelledGroupKeys((s) => s.filter((k) => k !== groupKey(g)))}
                          >
                            <Undo2 className="mr-0.5 h-3 w-3" />
                            {t('kb.merge.restore')}
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}
            <div className="grid items-start gap-4 lg:grid-cols-2">
              {activeGroups.map((g) => (
                <MergeGroupCard
                  key={groupKey(g)}
                  group={g}
                  canMerge={canMerge}
                  onCancel={() => setCancelledGroupKeys((s) => (s.includes(groupKey(g)) ? s : [...s, groupKey(g)]))}
                  onCreate={(master, merged, reason) =>
                    createMutation.mutate({ master_case_id: master, merged_case_ids: merged, reason })
                  }
                />
              ))}
            </div>
          </div>
        </StateGate>
      )}

      <div>
        <h3 className="mb-2 text-sm font-semibold">{t('kb.merge.proposalsTitle')}</h3>
        <StateGate
          loading={proposalsQuery.isLoading}
          error={proposalsQuery.isError ? errDetail(proposalsQuery.error) : null}
          onRetry={() => proposalsQuery.refetch()}
          isEmpty={proposals.length === 0}
          empty={t('kb.merge.noProposals')}
        >
          <ul className="space-y-2">
            {proposals.map((p: MergeProposal) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2.5 text-xs">
                <StatusBadge status={p.status} />
                <span className="font-mono font-medium">{p.master_case_id}</span>
                <span className="text-muted-foreground">{t('kb.merge.mergedFrom', { list: p.merged_case_ids.join(i18n.language?.startsWith('zh') ? '、' : ', ') })}</span>
                <span className="ml-auto text-muted-foreground">{p.created_by} · {fmtTime(p.created_at)}</span>
                {p.approval_request_id && <Badge variant="outline">{t('kb.merge.approvalRef', { id: p.approval_request_id })}</Badge>}
              </li>
            ))}
          </ul>
        </StateGate>
      </div>
    </div>
  );
}

// ------------------ 页面主体 ------------------

export default function KbPage() {
  const { t } = useTranslation();
  const perms = usePermissions();
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('active');
  const [selectedCase, setSelectedCase] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const casesQuery = useQuery({
    queryKey: ['kb-cases', q, status],
    queryFn: () => {
      const params: Record<string, string | number> = { limit: 50 };
      if (q.trim()) params.q = q.trim();
      if (status !== 'all') params.status = status;
      return consoleApi.listKbCases(params);
    },
  });

  const changeSetsQuery = useQuery({
    queryKey: ['change-sets'],
    queryFn: () => consoleApi.listChangeSets(),
  });

  const cases = casesQuery.data?.items ?? [];
  const changeSets = changeSetsQuery.data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">{t('kb.page.title')}</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {t('kb.page.subtitle')}
          </p>
        </div>
        {perms?.can_edit_kb && (
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            {t('kb.page.createCase')}
          </Button>
        )}
      </div>

      <Tabs defaultValue="cases">
        <TabsList>
          <TabsTrigger value="cases">{t('kb.page.tabCases')}</TabsTrigger>
          <TabsTrigger value="changes">{t('kb.page.tabChanges')}</TabsTrigger>
          <TabsTrigger value="merge">{t('kb.page.tabMerge')}</TabsTrigger>
        </TabsList>

        <TabsContent value="cases" className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2.5">
            <Input
              className="h-9 w-64 text-xs"
              placeholder={t('kb.page.searchPlaceholder')}
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="h-9 w-32 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="active">{t('kb.page.filterActive')}</SelectItem>
                <SelectItem value="archived">{t('kb.page.filterArchived')}</SelectItem>
                <SelectItem value="all">{t('kb.page.filterAll')}</SelectItem>
              </SelectContent>
            </Select>
            <span className="text-xs text-muted-foreground">{t('kb.page.totalCases', { count: casesQuery.data?.total ?? 0 })}</span>
          </div>

          <StateGate
            loading={casesQuery.isLoading}
            error={casesQuery.isError ? errDetail(casesQuery.error) : null}
            onRetry={() => casesQuery.refetch()}
            isEmpty={cases.length === 0}
            empty={t('kb.page.noCases')}
            emptyHint={t('kb.page.noCasesHint')}
          >
            <div className="grid items-start gap-3 md:grid-cols-2 xl:grid-cols-3">
              {cases.map((c: KbCase) => (
                <Card
                  key={c.id}
                  className="cursor-pointer transition-colors hover:border-primary/50"
                  onClick={() => setSelectedCase(c.case_id)}
                >
                  <CardContent className="space-y-1.5 pt-4">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="font-mono">{c.error_type}</Badge>
                      <StatusBadge status={c.status} />
                      <span className="ml-auto text-xs text-muted-foreground">v{c.version ?? '—'}</span>
                    </div>
                    <p className="font-mono text-xs text-muted-foreground">{c.case_id}</p>
                    <p className="line-clamp-2 text-sm">{c.root_cause || t('kb.merge.noRootCause')}</p>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <span>{c.service_name}</span>
                      {(c.related_event_count ?? 0) > 0 && (
                        <Badge variant="outline" className="font-normal">
                          <ScrollText className="mr-1 h-3 w-3" />
                          {t('kb.page.relatedLogs', { count: c.related_event_count })}
                        </Badge>
                      )}
                      <span className="ml-auto">{t('kb.page.feedbackScore', { score: c.feedback_score ?? 0 })}</span>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </StateGate>
        </TabsContent>

        <TabsContent value="changes" className="mt-4">
          <StateGate
            loading={changeSetsQuery.isLoading}
            error={changeSetsQuery.isError ? errDetail(changeSetsQuery.error) : null}
            onRetry={() => changeSetsQuery.refetch()}
            isEmpty={changeSets.length === 0}
            empty={t('kb.page.noChangeSets')}
          >
            <div className="space-y-2">
              {changeSets.map((cs: ChangeSet) => {
                const entries = diffEntries(cs.diff);
                const open = expandedId === cs.id;
                return (
                  <Card key={cs.id}>
                    <CardContent className="pt-4">
                      <button
                        className="flex w-full flex-wrap items-center gap-2 text-left text-xs"
                        onClick={() => setExpandedId(open ? null : cs.id)}
                      >
                        <StatusBadge status={cs.status} />
                        <span className="font-mono font-medium">{cs.case_id}</span>
                        <span>{cs.change_type === 'create' ? t('kb.page.changeCreate') : t('kb.page.changeUpdate')}</span>
                        <Badge variant="outline">{t('kb.page.fieldsCount', { count: entries.length })}</Badge>
                        <span className="ml-auto text-muted-foreground">{cs.created_by} · {fmtTime(cs.created_at)}</span>
                      </button>
                      {cs.reason && <p className="mt-1 text-xs text-muted-foreground">{t('kb.page.reason', { reason: cs.reason })}</p>}
                      {open && (
                        <div className="mt-2.5">
                          <DiffTable entries={entries} />
                        </div>
                      )}
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          </StateGate>
        </TabsContent>

        <TabsContent value="merge" className="mt-4">
          <MergeTab />
        </TabsContent>
      </Tabs>

      {selectedCase && (
        <Dialog open onOpenChange={(open) => !open && setSelectedCase(null)}>
          <CaseDetailDialog caseId={selectedCase} onClose={() => setSelectedCase(null)} />
        </Dialog>
      )}
      {createOpen && (
        <Dialog open onOpenChange={(open) => !open && setCreateOpen(false)}>
          <CreateCaseDialog onClose={() => setCreateOpen(false)} />
        </Dialog>
      )}
    </div>
  );
}
