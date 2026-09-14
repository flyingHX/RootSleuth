/** C11 用户与角色管理：创建用户档案、分配控制台角色、启用/禁用（仅系统管理员）。 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { UserPlus, Users } from 'lucide-react';
import { toast } from 'sonner';
import { consoleApi, errDetail, type UserAdminItem } from '@/lib/console-api';
import { StateGate, fmtTime } from '@/components/console/shared';
import { usePermissions } from '@/components/console/ConsoleLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

const ROLE_OPTIONS = [
  { value: 'viewer', label: '只读审计（viewer）' },
  { value: 'operator', label: '值班运维（operator）' },
  { value: 'sre', label: 'SRE（sre）' },
  { value: 'approver', label: '审批人 / SRE Lead（approver）' },
  { value: 'kb_admin', label: '知识库管理员（kb_admin）' },
  { value: 'sys_admin', label: '系统管理员（sys_admin）' },
];

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function StatusBadge({ status }: { status: string }) {
  return status === 'disabled' ? (
    <Badge variant="destructive">已禁用</Badge>
  ) : (
    <Badge variant="secondary">启用中</Badge>
  );
}

/** 创建用户：邮箱预建档，成员首次通过 Atoms 账号登录时自动关联。 */
function CreateUserDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState('viewer');
  const [status, setStatus] = useState('active');

  const createMut = useMutation({
    mutationFn: () =>
      consoleApi.createUser({ email: email.trim(), name: name.trim() || undefined, role, status }),
    onSuccess: (item) => {
      toast.success(`用户 ${item.email} 已创建，成员首次登录时将自动关联`);
      qc.invalidateQueries({ queryKey: ['users'] });
      onOpenChange(false);
      setEmail('');
      setName('');
      setRole('viewer');
      setStatus('active');
    },
    onError: (e) => toast.error(errDetail(e)),
  });

  const submit = () => {
    if (!EMAIL_RE.test(email.trim())) {
      toast.error('请输入有效的邮箱地址');
      return;
    }
    createMut.mutate();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>创建用户</DialogTitle>
          <DialogDescription>
            以邮箱预建档案并分配角色；成员首次使用 Atoms 账号登录时按邮箱自动关联，无需单独密码。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-1">
          <div className="grid gap-1.5">
            <Label htmlFor="u-email">邮箱 *</Label>
            <Input
              id="u-email"
              placeholder="member@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="u-name">姓名</Label>
            <Input id="u-name" placeholder="张三" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label>控制台角色</Label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label>状态</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active">启用中</SelectItem>
                <SelectItem value="disabled">已禁用</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={createMut.isPending}>
            取消
          </Button>
          <Button onClick={submit} disabled={createMut.isPending}>
            {createMut.isPending ? '创建中…' : '创建用户'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 编辑用户：姓名 / 角色 / 启用禁用；防自锁保护由后端强制（不能禁用自己或降低自己角色）。 */
function EditUserDialog({
  user,
  selfEmail,
  onOpenChange,
}: {
  user: UserAdminItem;
  selfEmail: string;
  onOpenChange: (v: boolean) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(user.name ?? '');
  const [role, setRole] = useState(user.role);
  const [status, setStatus] = useState(user.status);
  const isSelf = (user.email || '').toLowerCase() === selfEmail.toLowerCase();

  const updateMut = useMutation({
    mutationFn: () =>
      consoleApi.updateUser(user.id, {
        name: name.trim(),
        role,
        status,
      }),
    onSuccess: (item) => {
      toast.success(`用户 ${item.email} 已更新，角色变更即时生效`);
      qc.invalidateQueries({ queryKey: ['users'] });
      qc.invalidateQueries({ queryKey: ['permissions'] });
      onOpenChange(false);
    },
    onError: (e) => toast.error(errDetail(e)),
  });

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>编辑用户</DialogTitle>
          <DialogDescription>
            角色与状态变更即时生效并写入审计日志。
            {isSelf && ' 当前编辑的是本人账号：不能禁用自己，也不能降低自己的角色。'}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-1">
          <div className="grid gap-1.5">
            <Label>邮箱</Label>
            <Input value={user.email} disabled />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="e-name">姓名</Label>
            <Input id="e-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label>控制台角色</Label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label>状态</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active">启用中</SelectItem>
                <SelectItem value="disabled" disabled={isSelf}>
                  已禁用{isSelf && '（本人账号不可选）'}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={updateMut.isPending}>
            取消
          </Button>
          <Button onClick={() => updateMut.mutate()} disabled={updateMut.isPending}>
            {updateMut.isPending ? '保存中…' : '保存变更'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function UsersPage() {
  const perms = usePermissions();
  const [q, setQ] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<UserAdminItem | null>(null);

  const allowed = !!perms?.can_manage_users;
  const query = useQuery({
    queryKey: ['users', q, statusFilter],
    queryFn: () =>
      consoleApi.listUsers({
        ...(q.trim() ? { q: q.trim() } : {}),
        ...(statusFilter !== 'all' ? { status: statusFilter } : {}),
        limit: 200,
      }),
    enabled: allowed,
  });

  if (!allowed) {
    return (
      <div className="space-y-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">用户与角色管理</h1>
          <p className="mt-1 text-sm text-muted-foreground">运营平台成员档案、角色分配与启用/禁用。</p>
        </div>
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-12 text-center">
            <Users className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm font-medium">权限不足</p>
            <p className="text-xs text-muted-foreground">
              用户与角色管理仅对「系统管理员」开放，如需开通请联系管理员分配 sys_admin 角色。
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const items = query.data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">用户与角色管理</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            运营平台成员档案、角色分配与启用/禁用；全部变更写入审计日志。
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <UserPlus className="mr-2 h-4 w-4" />
          创建用户
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        <Input
          className="h-9 w-56 text-xs"
          placeholder="搜索邮箱 / 姓名"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="h-9 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="active">启用中</SelectItem>
            <SelectItem value="disabled">已禁用</SelectItem>
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground">共 {query.data?.total ?? 0} 个用户</span>
      </div>

      <StateGate
        loading={query.isLoading}
        error={query.isError ? errDetail(query.error) : null}
        onRetry={() => query.refetch()}
        isEmpty={items.length === 0}
        empty="还没有匹配的用户档案"
        emptyHint="点击右上角「创建用户」添加第一位成员，或清空筛选条件"
      >
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>用户</TableHead>
                  <TableHead>角色</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>创建时间</TableHead>
                  <TableHead>最近登录</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell>
                      <p className="text-sm font-medium">{item.name || '未命名'}</p>
                      <p className="text-xs text-muted-foreground">{item.email}</p>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{item.role_label}</Badge>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={item.status} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{fmtTime(item.created_at)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {item.last_login ? fmtTime(item.last_login) : '从未登录'}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" onClick={() => setEditing(item)}>
                        编辑
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </StateGate>

      {createOpen && <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} />}
      {editing && (
        <EditUserDialog
          key={editing.id}
          user={editing}
          selfEmail={perms?.user?.email ?? ''}
          onOpenChange={(v) => {
            if (!v) setEditing(null);
          }}
        />
      )}
    </div>
  );
}
