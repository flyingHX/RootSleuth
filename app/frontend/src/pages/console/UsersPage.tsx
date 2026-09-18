/** C11 用户与角色管理：创建用户档案、分配控制台角色、启用/禁用（仅系统管理员）。 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
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
  { value: 'viewer', labelKey: 'roleViewer' },
  { value: 'operator', labelKey: 'roleOperator' },
  { value: 'sre', labelKey: 'roleSre' },
  { value: 'approver', labelKey: 'roleApprover' },
  { value: 'kb_admin', labelKey: 'roleKbAdmin' },
  { value: 'sys_admin', labelKey: 'roleSysAdmin' },
];

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  return status === 'disabled' ? (
    <Badge variant="destructive">{t('users.statusDisabled')}</Badge>
  ) : (
    <Badge variant="secondary">{t('users.statusActive')}</Badge>
  );
}

/** 创建用户：邮箱预建档，成员首次通过 Atoms 账号登录时自动关联。 */
function CreateUserDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState('viewer');
  const [status, setStatus] = useState('active');

  const createMut = useMutation({
    mutationFn: () =>
      consoleApi.createUser({ email: email.trim(), name: name.trim() || undefined, role, status }),
    onSuccess: (item) => {
      toast.success(t('users.toastCreated', { email: item.email }));
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
      toast.error(t('users.toastInvalidEmail'));
      return;
    }
    createMut.mutate();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('users.createTitle')}</DialogTitle>
          <DialogDescription>{t('users.createDesc')}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-1">
          <div className="grid gap-1.5">
            <Label htmlFor="u-email">{t('users.emailLabel')}</Label>
            <Input
              id="u-email"
              placeholder="member@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="u-name">{t('users.nameLabel')}</Label>
            <Input
              id="u-name"
              placeholder={t('users.namePlaceholder')}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label>{t('users.roleLabel')}</Label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {t(`users.${opt.labelKey}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label>{t('users.statusLabel')}</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active">{t('users.statusActive')}</SelectItem>
                <SelectItem value="disabled">{t('users.statusDisabled')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={createMut.isPending}>
            {t('users.cancel')}
          </Button>
          <Button onClick={submit} disabled={createMut.isPending}>
            {createMut.isPending ? t('users.creating') : t('users.submitCreate')}
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
  const { t } = useTranslation();
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
      toast.success(t('users.toastUpdated', { email: item.email }));
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
          <DialogTitle>{t('users.editTitle')}</DialogTitle>
          <DialogDescription>
            {t('users.editDesc')}
            {isSelf && t('users.selfEditNote')}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-1">
          <div className="grid gap-1.5">
            <Label>{t('users.emailLabel').replace(' *', '')}</Label>
            <Input value={user.email} disabled />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="e-name">{t('users.nameLabel')}</Label>
            <Input id="e-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label>{t('users.roleLabel')}</Label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {t(`users.${opt.labelKey}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label>{t('users.statusLabel')}</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active">{t('users.statusActive')}</SelectItem>
                <SelectItem value="disabled" disabled={isSelf}>
                  {t('users.statusDisabled')}
                  {isSelf && t('users.selfDisabledNote')}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={updateMut.isPending}>
            {t('users.cancel')}
          </Button>
          <Button onClick={() => updateMut.mutate()} disabled={updateMut.isPending}>
            {updateMut.isPending ? t('users.saving') : t('users.submitSave')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function UsersPage() {
  const { t } = useTranslation();
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
          <h1 className="text-xl font-semibold tracking-tight">{t('users.title')}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('users.subtitle')}</p>
        </div>
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-12 text-center">
            <Users className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm font-medium">{t('users.noPermissionTitle')}</p>
            <p className="text-xs text-muted-foreground">{t('users.noPermissionDesc')}</p>
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
          <h1 className="text-xl font-semibold tracking-tight">{t('users.title')}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('users.subtitleFull')}</p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <UserPlus className="mr-2 h-4 w-4" />
          {t('users.createBtn')}
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        <Input
          className="h-9 w-56 text-xs"
          placeholder={t('users.searchPlaceholder')}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="h-9 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('users.allStatus')}</SelectItem>
            <SelectItem value="active">{t('users.statusActive')}</SelectItem>
            <SelectItem value="disabled">{t('users.statusDisabled')}</SelectItem>
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground">
          {t('users.totalUsers', { count: query.data?.total ?? 0 })}
        </span>
      </div>

      <StateGate
        loading={query.isLoading}
        error={query.isError ? errDetail(query.error) : null}
        onRetry={() => query.refetch()}
        isEmpty={items.length === 0}
        empty={t('users.empty')}
        emptyHint={t('users.emptyHint')}
      >
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('users.colUser')}</TableHead>
                  <TableHead>{t('users.colRole')}</TableHead>
                  <TableHead>{t('users.colStatus')}</TableHead>
                  <TableHead>{t('users.colCreatedAt')}</TableHead>
                  <TableHead>{t('users.colLastLogin')}</TableHead>
                  <TableHead className="text-right">{t('users.colActions')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell>
                      <p className="text-sm font-medium">{item.name || t('users.unnamed')}</p>
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
                      {item.last_login ? fmtTime(item.last_login) : t('users.neverLogin')}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" onClick={() => setEditing(item)}>
                        {t('users.editBtn')}
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
