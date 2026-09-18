/**
 * i18n 初始化：i18next + react-i18next。
 * - 语言选择持久化到 localStorage（键 rootSleuth-lang）；
 * - 未持久化时按浏览器语言自动检测：en* → en-US，其余（含 zh-*）→ zh-CN；
 * - 默认/回退语言为 zh-CN（简体中文为基准语言，英文为对照翻译）。
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import zhCN from './locales/zh-CN';
import enUS from './locales/en-US';

export const LANG_STORAGE_KEY = 'rootSleuth-lang';

export const SUPPORTED_LANGS = ['zh-CN', 'en-US'] as const;
export type SupportedLang = (typeof SUPPORTED_LANGS)[number];

/** 解析初始语言：localStorage 优先，其次浏览器语言，默认 zh-CN。 */
export function detectInitialLang(): SupportedLang {
  try {
    const stored = localStorage.getItem(LANG_STORAGE_KEY);
    if (stored === 'zh-CN' || stored === 'en-US') return stored;
  } catch {
    /* localStorage 不可用时忽略 */
  }
  const navLang =
    typeof navigator !== 'undefined' ? navigator.language || '' : '';
  const lower = navLang.toLowerCase();
  if (lower.startsWith('en')) return 'en-US';
  // zh-TW / zh-HK 等中文变体统一回退简体中文
  return 'zh-CN';
}

/** 切换语言：写 localStorage + changeLanguage + 同步 <html lang>。 */
export function applyLang(lang: SupportedLang) {
  try {
    localStorage.setItem(LANG_STORAGE_KEY, lang);
  } catch {
    /* ignore */
  }
  document.documentElement.lang = lang;
  void i18n.changeLanguage(lang);
}

i18n.use(initReactI18next).init({
  resources: {
    'zh-CN': { translation: zhCN },
    'en-US': { translation: enUS },
  },
  lng: detectInitialLang(),
  fallbackLng: 'zh-CN',
  interpolation: { escapeValue: false },
});

// 首次加载同步 <html lang>
document.documentElement.lang = i18n.language || 'zh-CN';

export default i18n;
