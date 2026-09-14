import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { client } from '../lib/api';

interface User {
  id: string;
  email?: string | null;
  name?: string | null;
}

type AuthStatus = 'loading' | 'authenticated' | 'anonymous';

interface AuthContextType {
  status: AuthStatus;
  user: User | null;
  login: () => void;
  logout: () => Promise<void>;
  refresh: (silent?: boolean) => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

/** 解码 JWT payload（base64url + UTF-8），仅用于 URL 令牌的乐观首帧渲染，服务端校验仍由 /auth/me 完成。 */
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return null;
  }
}

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<User | null>(null);

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setStatus('loading');
    try {
      const res = await client.auth.me();
      if (res?.data) {
        setUser(res.data as User);
        setStatus('authenticated');
      } else {
        setUser(null);
        setStatus('anonymous');
      }
    } catch {
      setUser(null);
      setStatus('anonymous');
    }
  }, []);

  useEffect(() => {
    // URL 携带演示令牌（?token= 或 #token=）：写入 Web SDK 约定的 localStorage 键并乐观恢复登录态，
    // 供验收/分享链接直达已登录工作台；随后仍向服务端 /auth/me 校验，失败则回落登录页。
    const params = new URLSearchParams(window.location.search);
    let urlToken = params.get('token') || '';
    if (!urlToken && window.location.hash.startsWith('#token=')) {
      urlToken = decodeURIComponent(window.location.hash.slice('#token='.length));
    }
    if (urlToken) {
      localStorage.setItem('token', urlToken);
      localStorage.setItem('isLougOutManual', 'false');
      params.delete('token');
      const qs = params.toString();
      const nextHash = window.location.hash.startsWith('#token=') ? '' : window.location.hash;
      window.history.replaceState(
        null,
        '',
        window.location.pathname + (qs ? `?${qs}` : '') + nextHash,
      );
      const payload = decodeJwtPayload(urlToken);
      if (payload) {
        setUser({
          id: String(payload.sub ?? payload.email ?? 'demo'),
          email: typeof payload.email === 'string' ? payload.email : null,
          name: typeof payload.name === 'string' ? payload.name : null,
        });
        setStatus('authenticated');
      }
      void refresh(true);
    } else {
      void refresh();
    }
  }, [refresh]);

  const login = () => {
    client.auth.toLogin();
  };

  const logout = async () => {
    try {
      await client.auth.logout();
    } finally {
      // 记录手动登出，避免预览环境自动演示登录立即把用户重新拉回登录态
      try {
        sessionStorage.setItem('manual_logout', '1');
      } catch {
        /* sessionStorage 不可用时忽略 */
      }
      setUser(null);
      setStatus('anonymous');
    }
  };

  const value: AuthContextType = { status, user, login, logout, refresh };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
