/** C9 规则管理：YAML 校验、版本发布/回滚；未知告警模板晋升/废弃。 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUpCircle, FileCheck2, Upload, XCircle } from 'lucide-react';
import { toast } from 'sonner';
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
      toast.success(`YAML 校验通过，共 ${res.rule_count} 条规则`);
    },
    onError: (e) => {
      setValidateResult(null);
      toast.error(`${errDetail(e)}`);
    },
  });

  const publishMutation = useMutation({
    mutationFn: () => consoleApi.publishRules(content, changeNote),
    onSuccess: (res) => {
      toast.success(`规则 v${res.version} 已发布并热加载生效`);
      setChangeNote('');
      setValidateResult(null);
      refresh();
    },
    onError: (e) => toast.error(`发布失败：${errDetail(e)}`),
  });

  const rollbackMutation = useMutation({
    mutationFn: (versionId: number) => consoleApi.rollbackRules(versionId),
    onSuccess: (res) => {
      toast.success(`已回滚，规则 v${res.version} 重新激活`);
      refresh();
    },
    onError: (e) => toast.error(`回滚失败：${errDetail(e)}`),
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
            规则 YAML 编辑器
            {active && <Badge variant="secondary" className="ml-2">当前 v{active.version} · {rulesQuery.data?.rule_count ?? 0} 条规则</Badge>}
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
                {validateMutation.isPending ? '校验中…' : '校验 YAML'}
              </Button>
            )}
            {canManage && (
              <>
                <Input
                  className="h-9 w-56 text-xs"
                  placeholder="变更说明（发布必填）"
                  value={changeNote}
                  onChange={(e) => setChangeNote(e.target.value)}
                />
                <Button
                  size="sm"
                  onClick={() => publishMutation.mutate()}
                  disabled={publishMutation.isPending || !content.trim() || !changeNote.trim()}
                >
                  <Upload className="mr-1.5 h-3.5 w-3.5" />
                  {publishMutation.isPending ? '发布中…' : '发布新版本'}
                </Button>
              </>
            )}
          </div>
          {!canEdit ? (
            <p className="text-xs text-muted-foreground">
              当前角色（{perms?.role_label}）仅可查看规则 YAML 与版本历史；编辑、校验需要知识库编辑权限，发布需要系统管理员权限。
            </p>
          ) : (
            !canManage && (
              <p className="text-xs text-muted-foreground">
                当前角色（{perms?.role_label}）可编辑与校验，发布需要系统管理员权限。
              </p>
            )
          )}
          {validateResult && (
            <p className="rounded-md border border-teal-600/40 bg-teal-600/10 px-3 py-2 text-xs text-teal-700">
              校验通过：{validateResult.rule_count} 条规则 · ID：{validateResult.rule_ids.join('、')}
            </p>
          )}
        </CardContent>
      </Card>

      {/* 版本历史 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">版本历史</CardTitle>
        </CardHeader>
        <CardContent>
          <StateGate
            loading={rulesQuery.isLoading}
            error={rulesQuery.isError ? errDetail(rulesQuery.error) : null}
            onRetry={() => rulesQuery.refetch()}
            isEmpty={(rulesQuery.data?.versions ?? []).length === 0}
            empty="还没有规则版本"
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
                    <span>{v.created_by || '系统'}</span>
                    {canManage && v.status !== 'active' && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => rollbackMutation.mutate(v.id)}
                        disabled={rollbackMutation.isPending}
                      >
                        回滚到此版本
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
  const queryClient = useQueryClient();
  const [errorType, setErrorType] = useState(template.suggested_error_type ?? '');
  const mutation = useMutation({
    mutationFn: () => consoleApi.promoteTemplate(template.id, errorType.trim()),
    onSuccess: (res) => {
      toast.success(`晋升申请已提交（审批单 #${res.approval_request_id}），终审通过后规则自动生成`);
      queryClient.invalidateQueries({ queryKey: ['unknown-templates'] });
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      queryClient.invalidateQueries({ queryKey: ['rules'] });
      onClose();
    },
    onError: (e) => toast.error(`提交晋升失败：${errDetail(e)}`),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>未知模板晋升为分类规则</DialogTitle>
        <DialogDescription>晋升将生成新的 error_type 规则条目（score 0.6），经审批终审后自动发布生效。</DialogDescription>
      </DialogHeader>
      <div>
        <p className="log-block mb-3 max-h-28 overflow-y-auto">{template.template}</p>
        <Label className="mb-1 text-xs">目标 error_type（必填）</Label>
        <Input
          className="h-9 font-mono text-xs"
          placeholder="如 upstream_timeout"
          value={errorType}
          onChange={(e) => setErrorType(e.target.value)}
        />
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>取消</Button>
        <Button onClick={() => mutation.mutate()} disabled={!errorType.trim() || mutation.isPending}>
          {mutation.isPending ? '提交中…' : '提交晋升审批'}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function UnknownTab() {
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
      toast.success('模板已标记废弃');
      queryClient.invalidateQueries({ queryKey: ['unknown-templates'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    },
    onError: (e) => toast.error(`废弃失败：${errDetail(e)}`),
  });

  const canOperate = !!perms?.can_edit_kb;
  const items = query.data?.items ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="h-9 w-36 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="pending">待处理</SelectItem>
            <SelectItem value="promoted">已晋升</SelectItem>
            <SelectItem value="discarded">已废弃</SelectItem>
            <SelectItem value="all">全部</SelectItem>
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground">
          无相似案例召回的告警模板会进入此队列；晋升后生成分类规则，减少未知率。
        </span>
      </div>

      <StateGate
        loading={query.isLoading}
        error={query.isError ? errDetail(query.error) : null}
        onRetry={() => query.refetch()}
        isEmpty={items.length === 0}
        empty="队列为空"
        emptyHint="当前筛选状态下没有未知告警模板"
      >
        <div className="space-y-2">
          {items.map((t: UnknownTemplate) => (
            <Card key={t.id}>
              <CardContent className="space-y-2 pt-4">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <StatusBadge status={t.status} />
                  <span className="text-muted-foreground">
                    出现 {t.sample_count ?? 0} 次 · 最近服务 {t.last_seen_service || '—'}
                  </span>
                  {t.suggested_error_type && (
                    <Badge variant="outline" className="font-mono">建议类型：{t.suggested_error_type}</Badge>
                  )}
                  <span className="ml-auto text-muted-foreground">{fmtTime(t.created_at)}</span>
                </div>
                <pre className="log-block max-h-24 overflow-y-auto">{t.template}</pre>
                {t.status === 'pending' && canOperate && (
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      onClick={() => setPromoteTarget(t)}
                    >
                      <ArrowUpCircle className="mr-1.5 h-3.5 w-3.5" />
                      晋升为规则
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => discardMutation.mutate(t.id)}
                      disabled={discardMutation.isPending}
                    >
                      <XCircle className="mr-1.5 h-3.5 w-3.5" />
                      标记废弃
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
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">规则管理</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          分类规则 YAML 校验、版本发布与热加载回滚；未知告警模板晋升与废弃闭环。
        </p>
      </div>
      <Tabs defaultValue="rules">
        <TabsList>
          <TabsTrigger value="rules">分类规则</TabsTrigger>
          <TabsTrigger value="unknown">未知告警队列</TabsTrigger>
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
