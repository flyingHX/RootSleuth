/**
 * 控制台布局外壳：登录门控 + 权限上下文 + 侧边导航 + 顶栏。
 * 未登录时展示登录引导（client.auth.toLogin），不自动跳转，避免回调死循环。
 * 全部用户可见文案经 i18n 双语（zh-CN / en-US）渲染。
 */
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { NavLink, Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Activity,
  BellRing,
  BookOpenText,
  Bot,
  ClipboardCheck,
  LayoutDashboard,
  LogOut,
  Menu,
  ScrollText,
  Settings2,
  Users,
} from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { client } from '@/lib/api';
import { consoleApi, errDetail, type Permissions } from '@/lib/console-api';
import { applyLang } from '@/i18n';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { ErrorBlock } from './shared';
import { cn } from '@/lib/utils';

const PermissionsContext = createContext<Permissions | null>(null);

/** 当前用户权限（角色、can_* 能力位、审批模式）。 */
export const usePermissions = (): Permissions | null => useContext(PermissionsContext);

const NAV_ITEMS = [
  { to: '/', labelKey: 'nav.overview', icon: LayoutDashboard },
  { to: '/events', labelKey: 'nav.events', icon: BellRing },
  { to: '/agents', labelKey: 'nav.agents', icon: Bot },
  { to: '/kb', labelKey: 'nav.kb', icon: BookOpenText },
  { to: '/approvals', labelKey: 'nav.approvals', icon: ClipboardCheck },
  { to: '/rules', labelKey: 'nav.rules', icon: ScrollText },
  { to: '/ops', labelKey: 'nav.ops', icon: Settings2 },
  { to: '/users', labelKey: 'nav.users', icon: Users },
  { to: '/help', labelKey: 'nav.help', icon: BookOpenText },
];

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  const perms = usePermissions();
  // 无权限的功能入口不显示：用户与角色、审计与配置仅系统管理员可见
  const items = NAV_ITEMS.filter((item) => {
    if (item.to === '/users') return !!perms?.can_manage_users;
    if (item.to === '/ops') return !!perms?.can_manage_config;
    return true;
  });
  return (
    <nav className="flex flex-col gap-1">
      {items.map(({ to, labelKey, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              isActive
                ? 'bg-sidebar-primary text-sidebar-primary-foreground'
                : 'text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground',
            )
          }
        >
          <Icon className="h-4 w-4" />
          {t(labelKey)}
        </NavLink>
      ))}
    </nav>
  );
}

/** 中 / EN 语言切换器：登录页与顶栏共用，切换后持久化到 localStorage。 */
function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const isZh = i18n.language.startsWith('zh');
  return (
    <div className="flex items-center rounded-md border p-0.5" role="group" aria-label={t('lang.ariaLabel')}>
      <Button
        variant="ghost"
        size="sm"
        className={cn(
          'h-7 px-2.5 text-xs font-medium',
          isZh ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground',
        )}
        onClick={() => applyLang('zh-CN')}
      >
        中
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className={cn(
          'h-7 px-2.5 text-xs font-medium',
          !isZh ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground',
        )}
        onClick={() => applyLang('en-US')}
      >
        EN
      </Button>
    </div>
  );
}

/** 预览环境演示账号：走后端 /api/v1/auth/demo-login，复用 Atoms JWT 签发链路。 */
const DEMO_ACCOUNTS = [
  { email: 'demo-admin@atoms.dev', roleKey: 'admin' },
  { email: 'demo-lead@atoms.dev', roleKey: 'lead' },
  { email: 'demo-sre@atoms.dev', roleKey: 'sre' },
  { email: 'demo-operator@atoms.dev', roleKey: 'operator' },
] as const;

function LoginScreen() {
  const { t } = useTranslation();
  const { login, refresh } = useAuth();
  const [busyEmail, setBusyEmail] = useState<string | null>(null);
  const [demoError, setDemoError] = useState('');

  const demoLogin = async (email: string) => {
    setBusyEmail(email);
    setDemoError('');
    try {
      const res = await client.apiCall.invoke({
        url: '/api/v1/auth/demo-login',
        method: 'POST',
        data: { email },
      });
      const token = (res?.data as { token?: string })?.token;
      if (!token) throw new Error(t('login.errorNoToken'));
      // 写入 Web SDK 约定的 localStorage 键，后续请求由 SDK 拦截器自动附加 Bearer
      localStorage.setItem('token', token);
      localStorage.setItem('isLougOutManual', 'false');
      await refresh();
    } catch (e) {
      setDemoError(errDetail(e));
    } finally {
      setBusyEmail(null);
    }
  };

  // URL ?auto=1&demo=<账号> 自动演示登录（供验收/分享直达控制台），登录后立即清理参数避免重复触发
  const autoLoginDone = useRef(false);
  useEffect(() => {
    if (autoLoginDone.current) return;
    autoLoginDone.current = true;
    const params = new URLSearchParams(window.location.search);
    // 手动登出后的本次会话不再自动登录，保留登录页供用户选择
    let manualLogout = false;
    try {
      manualLogout = sessionStorage.getItem('manual_logout') === '1';
    } catch {
      /* ignore */
    }
    const key = (params.get('demo') || 'admin').replace(/^demo-/, '').split('@')[0];
    const account =
      DEMO_ACCOUNTS.find(({ email }) => email.startsWith(`demo-${key}@`)) ??
      DEMO_ACCOUNTS[0];
    if (params.get('auto') === '1') {
      window.history.replaceState(null, '', window.location.pathname);
      void demoLogin(account.email);
      return;
    }
    // 预览环境无参数访问时自动演示登录（后端 ENABLE_DEMO_LOGIN=false 时请求失败，自然回落登录页）
    if (!manualLogout) void demoLogin(account.email);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-xl border bg-card p-8 text-center shadow-sm">
        <div className="flex justify-center">
          <LanguageSwitcher />
        </div>
        <div className="mx-auto mt-4 mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
          <Activity className="h-6 w-6" />
        </div>
        <h1 className="text-xl font-semibold tracking-tight">{t('brand.consoleTitle')}</h1>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{t('login.subtitle')}</p>
        <Button className="mt-6 w-full" onClick={login}>
          {t('login.loginWithAtoms')}
        </Button>

        <div className="mt-6 border-t pt-4">
          <p className="text-xs font-medium text-muted-foreground">{t('login.demoTitle')}</p>
          <div className="mt-3 grid gap-2">
            {DEMO_ACCOUNTS.map(({ email, roleKey }) => (
              <Button
                key={email}
                variant="outline"
                size="sm"
                className="w-full justify-between"
                disabled={busyEmail !== null}
                onClick={() => demoLogin(email)}
              >
                <span>{t(`login.demoRoles.${roleKey}`)}</span>
                <span className="text-xs text-muted-foreground">
                  {busyEmail === email ? t('login.loggingIn') : email.split('@')[0].replace('demo-', '')}
                </span>
              </Button>
            ))}
          </div>
          {demoError && <p className="mt-3 text-xs text-destructive">{demoError}</p>}
        </div>
      </div>
    </div>
  );
}

function FullSpinner() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
    </div>
  );
}

export default function ConsoleLayout() {
  const { t } = useTranslation();
  const { status, user, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const permsQuery = useQuery({
    queryKey: ['permissions'],
    queryFn: () => consoleApi.getPermissions(),
    enabled: status === 'authenticated',
  });

  if (status === 'loading') return <FullSpinner />;
  if (status === 'anonymous') return <LoginScreen />;
  if (permsQuery.isLoading) return <FullSpinner />;
  if (permsQuery.isError || !permsQuery.data) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <div className="w-full max-w-md">
          <ErrorBlock
            message={`${t('login.permissionLoadFailed')}：${errDetail(permsQuery.error)}`}
            onRetry={() => permsQuery.refetch()}
          />
        </div>
      </div>
    );
  }

  const perms = permsQuery.data;

  return (
    <PermissionsContext.Provider value={perms}>
      <div className="flex min-h-screen">
        {/* 桌面侧边栏 */}
        <aside className="hidden md:flex w-56 shrink-0 flex-col border-r bg-sidebar-background">
          <div className="flex items-center gap-2.5 px-4 py-4">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Activity className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-semibold leading-tight">{t('brand.name')}</p>
              <p className="text-xs text-muted-foreground">{t('brand.tagline')}</p>
            </div>
          </div>
          <div className="px-3">
            <NavLinks />
          </div>
          <div className="mt-auto px-4 py-4 text-xs text-muted-foreground">
            {t('layout.approvalModeLabel')}
            <Badge variant="outline" className="ml-1">
              {t(`layout.approvalMode.${perms.approval_mode}`, { defaultValue: perms.approval_mode })}
            </Badge>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          {/* 顶栏 */}
          <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-card px-4 md:px-6">
            {/* 移动端菜单 */}
            <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
              <SheetTrigger asChild>
                <Button variant="ghost" size="icon" className="md:hidden">
                  <Menu className="h-5 w-5" />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="w-64 p-4">
                <SheetTitle className="mb-3 text-left">{t('brand.consoleTitle')}</SheetTitle>
                <NavLinks onNavigate={() => setMobileOpen(false)} />
              </SheetContent>
            </Sheet>

            <div className="ml-auto flex items-center gap-2.5">
              <LanguageSwitcher />
              <Badge variant="secondary" className="hidden sm:inline-flex">
                {perms.role_label}
              </Badge>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground"
                    aria-label={t('layout.userMenuAria')}
                  >
                    {(user?.name || user?.email || 'U').slice(0, 1).toUpperCase()}
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-56">
                  <DropdownMenuLabel className="font-normal">
                    <p className="truncate text-sm font-medium">{user?.name || t('layout.loggedInUser')}</p>
                    <p className="truncate text-xs text-muted-foreground">{user?.email || user?.id}</p>
                  </DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={logout} className="text-destructive focus:text-destructive">
                    <LogOut className="mr-2 h-4 w-4" />
                    {t('layout.logout')}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </header>

          <main className="mx-auto w-full max-w-screen-xl flex-1 px-4 py-6 md:px-6">
            <Outlet />
          </main>
        </div>
      </div>
    </PermissionsContext.Provider>
  );
}
