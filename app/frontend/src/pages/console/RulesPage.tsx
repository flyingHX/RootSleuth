/** C9 规则管理：YAML 校验、版本发布/回滚；未知告警模板晋升/废弃。 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUpCircle, FileCheck2, Upload, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import { consoleApi, errDetail, type UnknownTemplate } from '@/lib/console-api';
import { EmptyBlock, StateGate, StatusBadge, fmtTime } from '@/components/console/shared';
import { usePermissions } from '@/components/console/ConsoleLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';

function RulesTab() {
  const { t } = useTranslation();
  const perms = usePermissions();
  const queryClient = useQueryClient();
  const rulesQuery = useQuery({ queryKey: ['rules'], queryFn: () => consoleApi.getRules() });

  const [content, setContent] = useState('');
  const [loadedFromActive, setLoadedFromActive] = useState(false);
  const [changeNote, setChangeNote] = useState('');
  const [validateResult, setValidateResult] = useState<{ valid: boolean; rule_count: number; rule_ids: string[] } | null>(null);

  // 激活版本内容默认填入编辑器
  useEffect(() => {
    const active = rulesQuery.data?.active;
    if (active && !loadedFromActive) {
      setContent(active.content);
      setLoadedFromActive(true);
    }
  }, [rulesQuery.data, loadedFromActive]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['rules'] });
    queryClient.invalidateQueries({ queryKey: ['dashboard'] });
  };

  const validateMutation = useMutation({
    mutationFn: () => consoleApi.validateRules(content),
    onSuccess: (res) => {
      setValidateResult(res);
      toast.success(t('rules.validateOk', { count: res.rule_count }));
    },
    onError: (e) => {
      setValidateResult(null);
      toast.error(`${errDetail(e)}`);
    },
  });

  const publishMutation = useMutation({
    mutationFn: () => consoleApi.publishRules(content, changeNote),
    onSuccess: (res) => {
      toast.success(t('rules.publishOk', { version: res.version }));
      setChangeNote('');
      setValidateResult(null);
      refresh();
    },
    onError: (e) => toast.error(t('rules.publishFailed', { error: errDetail(e) })),
  });

  const rollbackMutation = useMutation({
    mutationFn: (versionId: number) => consoleApi.rollbackRules(versionId),
    onSuccess: (res) => {
      toast.success(t('rules.rollbackOk', { version: res.version }));
      refresh();
    },
    onError: (e) => toast.error(t('rules.rollbackFailed', { error: errDetail(e) })),
  });

  const canManage = !!perms?.can_manage_rules;
  const canEdit = !!perms?.can_edit_kb;
  const active = rulesQuery.data?.active;

  return (
    <div className="grid items-start gap-4 lg:grid-cols-3">
      {/* 规则 YAML：所有角色可见；无编辑权限时只读展示 */}
      <Card className="lg:col-span-2">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            {t('rules.editorTitle')}
            {active && <Badge variant="secondary" className="ml-2">{t('rules.currentVersionBadge', { version: active.version, count: rulesQuery.data?.rule_count ?? 0 })}</Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            className={`min-h-80 font-mono text-xs${canEdit ? '' : ' cursor-default bg-muted/50 text-muted-foreground'}`}
            spellCheck={false}
            readOnly={!canEdit}
            placeholder={'rules:\n  - id: gateway_502\n    error_type: gateway_502\n    keywords: ["bad gateway"]\n    score: 0.8'}
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2">
            {canEdit && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => validateMutation.mutate()}
                disabled={validateMutation.isPending || !content.trim()}
              >
                <FileCheck2 className="mr-1.5 h-3.5 w-3.5" />
                {validateMutation.isPending ? t('rules.validating') : t('rules.validateBtn')}
              </Button>
            )}
            {canManage && (
              <>
                <Input
                  className="h-9 w-56 text-xs"
                  placeholder={t('rules.changeNotePlaceholder')}
                  value={changeNote}
                  onChange={(e) => setChangeNote(e.target.value)}
                />
                <Button
                  size="sm"
                  onClick={() => publishMutation.mutate()}
                  disabled={publishMutation.isPending || !content.trim() || !changeNote.trim()}
                >
                  <Upload className="mr-1.5 h-3.5 w-3.5" />
                  {publishMutation.isPending ? t('rules.publishing') : t('rules.publishBtn')}
                </Button>
              </>
            )}
          </div>
          {!canEdit ? (
            <p className="text-xs text-muted-foreground">
              {t('rules.roleViewOnly', { role: perms?.role_label })}
            </p>
          ) : (
            !canManage && (
              <p className="text-xs text-muted-foreground">
                {t('rules.roleEditOnly', { role: perms?.role_label })}
              </p>
            )
          )}
          {validateResult && (
            <p className="rounded-md border border-teal-600/40 bg-teal-600/10 px-3 py-2 text-xs text-teal-700">
              {t('rules.validateResult', { count: validateResult.rule_count, ids: validateResult.rule_ids.join(', ') })}
            </p>
          )}
        </CardContent>
      </Card>

      {/* 版本历史 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{t('rules.historyTitle')}</CardTitle>
        </CardHeader>
        <CardContent>
          <StateGate
            loading={rulesQuery.isLoading}
            error={rulesQuery.isError ? errDetail(rulesQuery.error) : null}
            onRetry={() => rulesQuery.refetch()}
            isEmpty={(rulesQuery.data?.versions ?? []).length === 0}
            empty={t('rules.noVersions')}
          >
            <ul className="space-y-2">
              {(rulesQuery.data?.versions ?? []).map((v) => (
                <li key={v.id} className="rounded-md border p-3 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={v.status === 'active' ? 'default' : 'outline'}>v{v.version}</Badge>
                    <StatusBadge status={v.status} />
                    <span className="ml-auto text-muted-foreground">{fmtTime(v.created_at)}</span>
                  </div>
                  {v.change_note && <p className="mt-1 text-muted-foreground">{v.change_note}</p>}
                  <div className="mt-1 flex items-center justify-between text-muted-foreground">
                    <span>{v.created_by || t('rules.systemLabel')}</span>
                    {canManage && v.status !== 'active' && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => rollbackMutation.mutate(v.id)}
                        disabled={rollbackMutation.isPending}
                      >
                        {t('rules.rollbackToVersion')}
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </StateGate>
        </CardContent>
      </Card>
    </div>
  );
}

function PromoteDialog({ template, onClose }: { template: UnknownTemplate; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [errorType, setErrorType] = useState(template.suggested_error_type ?? '');
  const mutation = useMutation({
    mutationFn: () => consoleApi.promoteTemplate(template.id, errorType.trim()),
    onSuccess: (res) => {
      toast.success(t('rules.promoteOk', { id: res.approval_request_id }));
      queryClient.invalidateQueries({ queryKey: ['unknown-templates'] });
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      queryClient.invalidateQueries({ queryKey: ['rules'] });
      onClose();
    },
    onError: (e) => toast.error(t('rules.promoteFailed', { error: errDetail(e) })),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{t('rules.promoteTitle')}</DialogTitle>
        <DialogDescription>{t('rules.promoteDesc')}</DialogDescription>
      </DialogHeader>
      <div>
        <p className="log-block mb-3 max-h-28 overflow-y-auto">{template.template}</p>
        <Label className="mb-1 text-xs">{t('rules.targetLabel')}</Label>
        <Input
          className="h-9 font-mono text-xs"
          placeholder={t('rules.targetPlaceholder')}
          value={errorType}
          onChange={(e) => setErrorType(e.target.value)}
        />
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>{t('rules.cancel')}</Button>
        <Button onClick={() => mutation.mutate()} disabled={!errorType.trim() || mutation.isPending}>
          {mutation.isPending ? t('rules.submitting') : t('rules.submitBtn')}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function UnknownTab() {
  const { t } = useTranslation();
  const perms = usePermissions();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState('pending');
  const [promoteTarget, setPromoteTarget] = useState<UnknownTemplate | null>(null);

  const query = useQuery({
    queryKey: ['unknown-templates', status],
    queryFn: () => consoleApi.listUnknownTemplates(status === 'all' ? undefined : status),
  });

  const discardMutation = useMutation({
    mutationFn: (id: number) => consoleApi.discardTemplate(id),
    onSuccess: () => {
      toast.success(t('rules.discardOk'));
      queryClient.invalidateQueries({ queryKey: ['unknown-templates'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    },
    onError: (e) => toast.error(t('rules.discardFailed', { error: errDetail(e) })),
  });

  const canOperate = !!perms?.can_edit_kb;
  const items = query.data?.items ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="h-9 w-36 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="pending">{t('rules.statusPending')}</SelectItem>
            <SelectItem value="promoted">{t('rules.statusPromoted')}</SelectItem>
            <SelectItem value="discarded">{t('rules.statusDiscarded')}</SelectItem>
            <SelectItem value="all">{t('rules.statusAll')}</SelectItem>
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground">
          {t('rules.queueHint')}
        </span>
      </div>

      <StateGate
        loading={query.isLoading}
        error={query.isError ? errDetail(query.error) : null}
        onRetry={() => query.refetch()}
        isEmpty={items.length === 0}
        empty={t('rules.emptyQueue')}
        emptyHint={t('rules.emptyQueueHint')}
      >
        <div className="space-y-2">
          {items.map((tpl: UnknownTemplate) => (
            <Card key={tpl.id}>
              <CardContent className="space-y-2 pt-4">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <StatusBadge status={tpl.status} />
                  <span className="text-muted-foreground">
                    {t('rules.templateMeta', { count: tpl.sample_count ?? 0, service: tpl.last_seen_service || '—' })}
                  </span>
                  {tpl.suggested_error_type && (
                    <Badge variant="outline" className="font-mono">{t('rules.suggestedType', { type: tpl.suggested_error_type })}</Badge>
                  )}
                  <span className="ml-auto text-muted-foreground">{fmtTime(tpl.created_at)}</span>
                </div>
                <pre className="log-block max-h-24 overflow-y-auto">{tpl.template}</pre>
                {tpl.status === 'pending' && canOperate && (
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      onClick={() => setPromoteTarget(tpl)}
                    >
                      <ArrowUpCircle className="mr-1.5 h-3.5 w-3.5" />
                      {t('rules.promoteAction')}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => discardMutation.mutate(tpl.id)}
                      disabled={discardMutation.isPending}
                    >
                      <XCircle className="mr-1.5 h-3.5 w-3.5" />
                      {t('rules.discardBtn')}
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      </StateGate>

      {promoteTarget && (
        <Dialog open onOpenChange={(open) => !open && setPromoteTarget(null)}>
          <PromoteDialog template={promoteTarget} onClose={() => setPromoteTarget(null)} />
        </Dialog>
      )}
    </div>
  );
}

export default function RulesPage() {
  const { t } = useTranslation();
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{t('rules.title')}</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {t('rules.subtitle')}
        </p>
      </div>
      <Tabs defaultValue="rules">
        <TabsList>
          <TabsTrigger value="rules">{t('rules.tabRules')}</TabsTrigger>
          <TabsTrigger value="unknown">{t('rules.tabUnknown')}</TabsTrigger>
        </TabsList>
        <TabsContent value="rules" className="mt-4">
          <RulesTab />
        </TabsContent>
        <TabsContent value="unknown" className="mt-4">
          <UnknownTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
