/** C6/C7 知识库：案例检索与编辑审批、版本回滚、变更记录 diff、去重合并。 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
const FIELD_LABELS: Record<string, string> = {
  alert_template: '告警模板',
  root_cause: '根因',
  solution: '处置方案',
  cluster: '集群',
  topology_snapshot: '拓扑快照',
  error_type: '错误类型',
  service_name: '服务名',
};

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
  if (loading) return <SpinnerLine text="加载实例日志…" />;
  if (events.length === 0) {
    return (
      <p className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
        {emptyHint ?? '暂无关联实例日志'}
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
  return (
    <div className="space-y-2.5">
      <ContentScanCard scan={scan} title="内容安全扫描（已拦截）" />
      <div className="rounded-md border bg-secondary/40 p-3">
        <p className="text-xs leading-relaxed text-muted-foreground">
          高风险命中（密钥、私钥、危险命令、注入等）会阻止发布。若确认属于误报（例如演练样本、脱敏演示数据），
          请填写放行理由后重新提交；放行操作与理由将写入审计日志供事后追溯。
        </p>
        <RadioGroup
          className="mt-2.5 flex flex-wrap gap-4"
          value={allow ? 'override' : 'blocked'}
          onValueChange={(v) => onAllow(v === 'override')}
        >
          <div className="flex items-center space-x-2">
            <RadioGroupItem value="blocked" id={`${idPrefix}-keep-blocked`} />
            <Label htmlFor={`${idPrefix}-keep-blocked`} className="text-xs">保持拦截，稍后修改内容</Label>
          </div>
          <div className="flex items-center space-x-2">
            <RadioGroupItem value="override" id={`${idPrefix}-allow-override`} />
            <Label htmlFor={`${idPrefix}-allow-override`} className="text-xs">确认为误报，放行提交</Label>
          </div>
        </RadioGroup>
        {allow && (
          <Input
            className="mt-2.5 h-9 text-xs"
            placeholder="放行理由（必填，写入审计），例如：根因中的 Token 为演练样本"
            value={reason}
            onChange={(e) => onReason(e.target.value)}
          />
        )}
      </div>
    </div>
  );
}

function EditCaseDialog({ detail, onClose }: { detail: KbCaseDetail; onClose: () => void }) {
  const queryClient = useQueryClient();
  const perms = usePermissions();
  const isCreate = false;
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
      key: FIELD_LABELS[f],
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
        toast.info(`变更已提交，内容安全扫描命中 ${res.content_scan.hits.length} 处（${res.content_scan.risk_level === 'high' ? '已误报放行' : '低风险留痕'}），明细见审批单`);
      }
      toast.success(
        res.auto_published
          ? `变更已发布，案例 ${detail.case.case_id} 更新到 v${res.change_set.version}`
          : `变更集已创建并提交审批（单号 #${res.approval_request_id}），通过后自动发布`,
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
        toast.error('内容安全扫描已拦截本次变更，请确认是否误报放行', { duration: 8000 });
      } else {
        toast.error(`提交变更失败：${errDetail(e)}`);
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
        <DialogTitle>{isCreate ? '新建知识案例' : `编辑案例：${detail.case.case_id}`}</DialogTitle>
        <DialogDescription>
          当前版本 v{detail.case.version ?? 1}。变更按审批模式「{perms?.approval_mode === 'OFF' ? '免审直发' : perms?.approval_mode === 'SINGLE_REVIEW' ? '单级审批' : '多级审批'}」处理，全部操作写入审计日志。
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        {EDITABLE_FIELDS.map((f) => (
          <div key={f}>
            <Label className="mb-1 text-xs">{FIELD_LABELS[f]}</Label>
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
          <Label className="mb-1 text-xs">变更理由（必填）</Label>
          <Input
            className="h-9 text-xs"
            placeholder="例如：新增网关升级后的根因与处置步骤"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
        {changedEntries.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">变更预览</p>
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
        <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>取消</Button>
        <Button onClick={() => mutation.mutate()} disabled={disabled}>
          <PencilLine className="mr-1.5 h-3.5 w-3.5" />
          {mutation.isPending ? '提交中…' : guardScan && allowOverride ? '放行并提交' : '提交变更'}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function CreateCaseDialog({ onClose }: { onClose: () => void }) {
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
        toast.info(`内容安全扫描命中 ${res.content_scan.hits.length} 处（${res.content_scan.risk_level === 'high' ? '已误报放行' : '低风险留痕'}），明细见审批单`);
      }
      toast.success(
        res.auto_published
          ? `案例 ${caseId} 已创建并发布`
          : `新建变更集已提交审批（单号 #${res.approval_request_id}），案例 ID 自动生成：${caseId}`,
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
        toast.error('内容安全扫描已拦截本次创建，请确认是否误报放行', { duration: 8000 });
      } else {
        toast.error(`创建失败：${errDetail(e)}`);
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
        <DialogTitle>新建知识案例</DialogTitle>
        <DialogDescription>创建后按审批模式进入审批流或直接发布，写入版本与审计。</DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        <p className="rounded-md bg-muted/50 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          案例 ID 无需手工填写：提交后由系统按 <span className="font-mono">KB-日期-当日序号</span> 规则自动生成并保证唯一；
          审批中心将展示该新建案例的案例库完整字段视图（未填写字段标注「未填写」）与关联日志实例证据。
        </p>
        <div>
          <Label className="mb-1 text-xs">错误类型（必填）</Label>
          <Input
            className="h-9 font-mono text-xs"
            placeholder="gateway_502"
            value={form.error_type}
            onChange={(e) => setForm((s) => ({ ...s, error_type: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">服务名（必填）</Label>
          <Input
            className="h-9 font-mono text-xs"
            placeholder="payment-service"
            value={form.service_name}
            onChange={(e) => setForm((s) => ({ ...s, service_name: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">集群</Label>
          <Input
            className="h-9 text-xs"
            placeholder="prod-cluster-01"
            value={form.cluster}
            onChange={(e) => setForm((s) => ({ ...s, cluster: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">告警模板</Label>
          <Textarea
            className="min-h-16 font-mono text-xs"
            placeholder="upstream sent too big header while reading response header from upstream"
            value={form.alert_template}
            onChange={(e) => setForm((s) => ({ ...s, alert_template: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">根因</Label>
          <Textarea
            className="min-h-16 text-xs"
            value={form.root_cause}
            onChange={(e) => setForm((s) => ({ ...s, root_cause: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">处置方案</Label>
          <Textarea
            className="min-h-16 text-xs"
            value={form.solution}
            onChange={(e) => setForm((s) => ({ ...s, solution: e.target.value }))}
          />
        </div>
        <div>
          <Label className="mb-1 text-xs">拓扑快照</Label>
          <Textarea
            className="min-h-16 font-mono text-xs"
            placeholder="ingress → gateway(payment) → payment-api → mysql"
            value={form.topology_snapshot}
            onChange={(e) => setForm((s) => ({ ...s, topology_snapshot: e.target.value }))}
          />
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <Label className="text-xs">关联实例日志（证据参考，不入库）</Label>
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              disabled={!form.alert_template.trim() && !form.service_name.trim()}
              onClick={() => setEvidenceOpen(true)}
            >
              <ScrollText className="mr-1 h-3 w-3" />
              {evidenceOpen ? '刷新预览' : '查询关联日志'}
            </Button>
          </div>
          {!evidenceOpen ? (
            <p className="text-xs text-muted-foreground">
              填写告警模板或服务名后，可查询告警库中匹配的实例日志作为证据参考。
            </p>
          ) : (
            <EventSampleList
              loading={evidenceQuery.isFetching}
              events={evidenceQuery.data?.items ?? []}
              emptyHint="未找到匹配的实例日志（按告警模板精确匹配优先、服务名兜底）"
            />
          )}
        </div>
        <div>
          <Label className="mb-1 text-xs">变更理由（必填）</Label>
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
        <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>取消</Button>
        <Button onClick={() => mutation.mutate()} disabled={!valid || mutation.isPending}>
          {mutation.isPending ? '提交中…' : guardScan && allowOverride ? '放行并创建' : '创建案例'}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ------------------ 案例详情对话框 ------------------

function CaseDetailDialog({ caseId, onClose }: { caseId: string; onClose: () => void }) {
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
      toast.success(`案例已回滚，生成新版本 v${res.version}`);
      queryClient.invalidateQueries({ queryKey: ['kb-case', caseId] });
      queryClient.invalidateQueries({ queryKey: ['kb-cases'] });
      queryClient.invalidateQueries({ queryKey: ['change-sets'] });
    },
    onError: (e) => toast.error(`回滚失败：${errDetail(e)}`),
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
          {detailQuery.data?.case.cluster ? ` · ${detailQuery.data.case.cluster}` : ''} · 反馈分 {detailQuery.data?.case.feedback_score ?? 0}
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
                  编辑案例
                </Button>
              </div>
            )}

            <div>
              <p className="mb-1.5 text-sm font-semibold">当前内容</p>
              <div className="space-y-2 text-xs">
                <div>
                  <p className="font-medium text-muted-foreground">告警模板</p>
                  <pre className="log-block">{detailQuery.data.case.alert_template || '（无）'}</pre>
                </div>
                <div>
                  <p className="font-medium text-muted-foreground">根因</p>
                  <pre className="log-block">{detailQuery.data.case.root_cause || '（无）'}</pre>
                </div>
                <div>
                  <p className="font-medium text-muted-foreground">处置方案</p>
                  <pre className="log-block">{detailQuery.data.case.solution || '（无）'}</pre>
                </div>
                {detailQuery.data.case.topology_snapshot && (
                  <div>
                    <p className="font-medium text-muted-foreground">拓扑快照</p>
                    <pre className="log-block">{detailQuery.data.case.topology_snapshot}</pre>
                  </div>
                )}
              </div>
            </div>

            <Separator />

            <div>
              <p className="mb-1.5 text-sm font-semibold">关联实例日志（{detailQuery.data.related_events?.length ?? 0}）</p>
              <EventSampleList
                events={detailQuery.data.related_events ?? []}
                emptyHint="暂无与该案例告警模板 / 服务名匹配的实例日志"
              />
            </div>

            <Separator />

            <div>
              <p className="mb-1.5 text-sm font-semibold">版本历史（{detailQuery.data.versions.length}）</p>
              <ul className="space-y-1.5">
                {detailQuery.data.versions.map((v) => (
                  <li key={v.id} className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs">
                    <Badge variant={v.version === detailQuery.data?.case.version ? 'default' : 'outline'}>
                      v{v.version}
                      {v.version === detailQuery.data?.case.version ? '（当前）' : ''}
                    </Badge>
                    <span className="text-muted-foreground">{v.created_by || '系统'}</span>
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
                        回滚到此版本
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>

            <Separator />

            <div>
              <p className="mb-1.5 text-sm font-semibold">变更记录（{detailQuery.data.change_sets.length}）</p>
              <ul className="space-y-2.5">
                {detailQuery.data.change_sets.map((cs) => (
                  <li key={cs.id} className="rounded-md border p-3 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={cs.status} />
                      <span className="font-medium">{cs.change_type === 'create' ? '新建' : '更新'}</span>
                      {cs.version && <Badge variant="outline">v{cs.version}</Badge>}
                      <span className="text-muted-foreground">{cs.created_by}</span>
                      <span className="text-muted-foreground">{fmtTime(cs.created_at)}</span>
                    </div>
                    {cs.reason && <p className="mt-1 text-muted-foreground">理由：{cs.reason}</p>}
                    <div className="mt-2">
                      <DiffTable entries={diffEntries(cs.diff)} />
                    </div>
                  </li>
                ))}
                {detailQuery.data.change_sets.length === 0 && (
                  <p className="text-xs text-muted-foreground">暂无变更记录</p>
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
            {group.service_name} · {group.case_ids.length} 个相似案例
            {typeof group.min_pair_similarity === 'number' &&
              ` · 组内最低相似度 ${Math.round(group.min_pair_similarity * 100)}%`}
          </span>
          {canMerge && (
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto h-7 shrink-0 px-2 text-xs text-muted-foreground hover:text-destructive"
              title="取消本组扫描结果的合并（案例库数据不受影响，可随时恢复）"
              onClick={() => setConfirmCancel(true)}
            >
              <X className="mr-0.5 h-3 w-3" />
              取消本次合并
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
                  {c.case_id === group.suggested_master && <Badge variant="secondary">建议主案例（反馈分最高）</Badge>}
                  <span className="text-muted-foreground">反馈分 {c.feedback_score ?? 0} · v{c.version}</span>
                  {c.case_id !== effectiveMaster && simPct(group, effectiveMaster, c.case_id) !== null && (
                    <Badge variant="outline" className="font-normal">
                      与主案例相似度 {simPct(group, effectiveMaster, c.case_id)}%
                    </Badge>
                  )}
                </p>
                <p className="mt-1 line-clamp-2 text-muted-foreground">{c.root_cause || '（无根因）'}</p>
              </div>
            </label>
          ))}
        </RadioGroup>
        {canMerge && (
          <>
            <Input
              className="h-9 text-xs"
              placeholder="合并理由（必填）：例如同一故障两次入库，合并保留反馈分最高的案例"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <Button
              size="sm"
              disabled={!reason.trim() || merged.length === 0}
              onClick={() => onCreate(effectiveMaster, merged, reason.trim())}
            >
              <GitMerge className="mr-1.5 h-3.5 w-3.5" />
              合并到 {effectiveMaster}（归档 {merged.length} 个冗余案例）
            </Button>
          </>
        )}
      </CardContent>
      </Card>
      <AlertDialog open={confirmCancel} onOpenChange={setConfirmCancel}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>取消本次合并？</AlertDialogTitle>
            <AlertDialogDescription>
              将取消 {group.error_type}（{group.service_name}）这组相似案例的合并：取消后本组不参与本次合并，案例库数据不受影响，可随时在「已取消的扫描结果」区恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续合并</AlertDialogCancel>
            <AlertDialogAction onClick={() => onCancel()}>取消本次合并</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function MergeTab() {
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
          ? '合并已自动执行（免审模式），冗余案例已归档'
          : `合并提案已提交审批（单号 #${res.approval_request_id}）`,
      );
      queryClient.invalidateQueries({ queryKey: ['merge-proposals'] });
      queryClient.invalidateQueries({ queryKey: ['kb-duplicates'] });
      queryClient.invalidateQueries({ queryKey: ['kb-cases'] });
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
    },
    onError: (e) => toast.error(`创建合并提案失败：${errDetail(e)}`),
  });

  const groups = scanQuery.data?.groups ?? [];
  const groupKey = (g: MergeGroup) => g.case_ids.join('|');
  const activeGroups = groups.filter((g) => !cancelledGroupKeys.includes(groupKey(g)));
  const cancelledGroups = groups.filter((g) => cancelledGroupKeys.includes(groupKey(g)));
  const proposals = proposalsQuery.data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <Button size="sm" variant="outline" onClick={() => setScanEnabled(true)} disabled={scanQuery.isFetching}>
          <ScanSearch className="mr-1.5 h-3.5 w-3.5" />
          {scanQuery.isFetching ? '扫描中…' : '扫描相似案例'}
        </Button>
        <span className="text-xs text-muted-foreground">
          按同错误类型 + 同服务且模板相似度 ≥80% 聚类，仅展示相似度达标的合并建议；合并后冗余案例归档，全部写入审计。
        </span>
      </div>

      {scanEnabled && (
        <StateGate
          loading={scanQuery.isLoading}
          error={scanQuery.isError ? errDetail(scanQuery.error) : null}
          onRetry={() => scanQuery.refetch()}
          isEmpty={groups.length === 0}
          empty="未发现相似案例簇"
          emptyHint="知识库当前没有满足聚类条件的重复案例"
        >
          <div className="space-y-4">
            {cancelledGroups.length > 0 && (
              <Card className="border-dashed border-amber-500/40">
                <CardContent className="space-y-2 pt-4 text-xs">
                  <p className="font-medium text-amber-600">
                    已取消本次合并的扫描结果（{cancelledGroups.length} 组）——案例库数据不受影响，可恢复后重新参与合并
                  </p>
                  <ul className="space-y-1.5">
                    {cancelledGroups.map((g) => (
                      <li key={groupKey(g)} className="flex flex-wrap items-center gap-2 text-muted-foreground">
                        <span className="font-mono text-foreground">{g.error_type}</span>
                        <span>
                          {g.service_name} · {g.case_ids.length} 个相似案例
                        </span>
                        {canMerge && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 px-2 text-xs"
                            onClick={() => setCancelledGroupKeys((s) => s.filter((k) => k !== groupKey(g)))}
                          >
                            <Undo2 className="mr-0.5 h-3 w-3" />
                            恢复
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
        <h3 className="mb-2 text-sm font-semibold">合并提案记录</h3>
        <StateGate
          loading={proposalsQuery.isLoading}
          error={proposalsQuery.isError ? errDetail(proposalsQuery.error) : null}
          onRetry={() => proposalsQuery.refetch()}
          isEmpty={proposals.length === 0}
          empty="还没有合并提案"
        >
          <ul className="space-y-2">
            {proposals.map((p: MergeProposal) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2.5 text-xs">
                <StatusBadge status={p.status} />
                <span className="font-mono font-medium">{p.master_case_id}</span>
                <span className="text-muted-foreground">← 合并 {p.merged_case_ids.join('、')}</span>
                <span className="ml-auto text-muted-foreground">{p.created_by} · {fmtTime(p.created_at)}</span>
                {p.approval_request_id && <Badge variant="outline">审批 #{p.approval_request_id}</Badge>}
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
          <h1 className="text-lg font-semibold tracking-tight">知识库治理</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            案例编辑走审批流（禁止自审）、版本可回滚、相似案例去重合并，全程写审计日志。
          </p>
        </div>
        {perms?.can_edit_kb && (
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            新建案例
          </Button>
        )}
      </div>

      <Tabs defaultValue="cases">
        <TabsList>
          <TabsTrigger value="cases">案例库</TabsTrigger>
          <TabsTrigger value="changes">变更记录</TabsTrigger>
          <TabsTrigger value="merge">去重合并</TabsTrigger>
        </TabsList>

        <TabsContent value="cases" className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2.5">
            <Input
              className="h-9 w-64 text-xs"
              placeholder="搜索案例 ID / 模板 / 根因"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="h-9 w-32 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="active">活跃案例</SelectItem>
                <SelectItem value="archived">已归档</SelectItem>
                <SelectItem value="all">全部状态</SelectItem>
              </SelectContent>
            </Select>
            <span className="text-xs text-muted-foreground">共 {casesQuery.data?.total ?? 0} 个案例</span>
          </div>

          <StateGate
            loading={casesQuery.isLoading}
            error={casesQuery.isError ? errDetail(casesQuery.error) : null}
            onRetry={() => casesQuery.refetch()}
            isEmpty={cases.length === 0}
            empty="没有符合条件的知识案例"
            emptyHint="尝试切换状态筛选或清空搜索关键词"
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
                    <p className="line-clamp-2 text-sm">{c.root_cause || '（无根因）'}</p>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <span>{c.service_name}</span>
                      {(c.related_event_count ?? 0) > 0 && (
                        <Badge variant="outline" className="font-normal">
                          <ScrollText className="mr-1 h-3 w-3" />
                          关联日志 {c.related_event_count}
                        </Badge>
                      )}
                      <span className="ml-auto">反馈分 {c.feedback_score ?? 0}</span>
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
            empty="暂无变更记录"
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
                        <span>{cs.change_type === 'create' ? '新建' : '更新'}</span>
                        <Badge variant="outline">{entries.length} 个字段</Badge>
                        <span className="ml-auto text-muted-foreground">{cs.created_by} · {fmtTime(cs.created_at)}</span>
                      </button>
                      {cs.reason && <p className="mt-1 text-xs text-muted-foreground">理由：{cs.reason}</p>}
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
