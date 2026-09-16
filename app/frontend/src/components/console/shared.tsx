/** 控制台共享小组件：徽标、状态块、复制按钮、diff 与 JSON 展示。 */
import { useEffect, useState, type ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { AlertTriangle, Check, Copy, Inbox, Loader2, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';

const SEVERITY_CLASS: Record<string, string> = {
  critical: 'border-red-500/40 bg-red-500/10 text-red-600',
  warning: 'border-amber-500/40 bg-amber-500/10 text-amber-700',
  info: 'border-sky-500/40 bg-sky-500/10 text-sky-700',
};

const SEVERITY_LABEL: Record<string, string> = {
  critical: '严重',
  warning: '警告',
  info: '提示',
};

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  if (!severity) return <span className="text-xs text-muted-foreground">—</span>;
  return (
    <Badge variant="outline" className={cn('font-medium', SEVERITY_CLASS[severity] ?? '')}>
      {SEVERITY_LABEL[severity] ?? severity}
    </Badge>
  );
}

const EVENT_STATUS_LABEL: Record<string, { label: string; className: string }> = {
  pending: { label: '待处理', className: 'border-muted-foreground/30 bg-muted text-muted-foreground' },
  diagnosed: { label: '已诊断', className: 'border-teal-600/40 bg-teal-600/10 text-teal-700' },
  unknown: { label: '未知', className: 'border-violet-500/40 bg-violet-500/10 text-violet-700' },
};

export function EventStatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-xs text-muted-foreground">—</span>;
  const meta = EVENT_STATUS_LABEL[status] ?? { label: status, className: '' };
  return (
    <Badge variant="outline" className={meta.className}>
      {meta.label}
    </Badge>
  );
}

const RAG_STATUS_LABEL: Record<string, { label: string; className: string }> = {
  success: { label: 'RAG 成功', className: 'border-teal-600/40 bg-teal-600/10 text-teal-700' },
  degraded: { label: 'RAG 降级', className: 'border-amber-500/40 bg-amber-500/10 text-amber-700' },
  unknown: { label: '无召回', className: 'border-violet-500/40 bg-violet-500/10 text-violet-700' },
};

export function RagBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-xs text-muted-foreground">未检索</span>;
  const meta = RAG_STATUS_LABEL[status] ?? { label: status, className: '' };
  return (
    <Badge variant="outline" className={meta.className}>
      {meta.label}
    </Badge>
  );
}

const GENERIC_STATUS_LABEL: Record<string, string> = {
  pending: '待处理',
  approved: '已通过',
  rejected: '已拒绝',
  withdrawn: '已撤回',
  published: '已发布',
  merged: '已合并',
  active: '启用',
  superseded: '已替代',
  archived: '已归档',
  promoted: '已晋升',
  discarded: '已废弃',
  auto: '自动',
  low: '低',
  medium: '中',
  high: '高',
};

export function StatusBadge({ status, className }: { status: string | null | undefined; className?: string }) {
  if (!status) return <span className="text-xs text-muted-foreground">—</span>;
  const tone =
    status === 'approved' || status === 'published' || status === 'merged' || status === 'active'
      ? 'border-teal-600/40 bg-teal-600/10 text-teal-700'
      : status === 'rejected' || status === 'discarded'
        ? 'border-red-500/40 bg-red-500/10 text-red-600'
        : status === 'withdrawn' || status === 'archived' || status === 'superseded'
          ? 'border-muted-foreground/30 bg-muted text-muted-foreground'
          : 'border-amber-500/40 bg-amber-500/10 text-amber-700';
  return (
    <Badge variant="outline" className={cn(tone, className)}>
      {GENERIC_STATUS_LABEL[status] ?? status}
    </Badge>
  );
}

export function ConfidenceBadge({ value, low }: { value: number | null | undefined; low?: boolean }) {
  if (value === null || value === undefined) return <span className="text-xs text-muted-foreground">—</span>;
  return (
    <span className={cn('inline-flex items-center gap-1 text-xs font-medium', low ? 'text-amber-700' : 'text-teal-700')}>
      {(value * 100).toFixed(0)}%
      {low && <span className="text-muted-foreground">（低于阈值）</span>}
    </span>
  );
}

/** 时间格式化：兼容 Python str(datetime) 输出。 */
export function fmtTime(value: string | null | undefined, withDate = true): string {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const opts: Intl.DateTimeFormatOptions = { hour12: false, hour: '2-digit', minute: '2-digit' };
  if (withDate) {
    opts.month = '2-digit';
    opts.day = '2-digit';
  }
  return d.toLocaleString('zh-CN', opts);
}

export function fmtPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

export function CopyButton({ text, label = '复制', size = 'sm' }: { text: string; label?: string; size?: 'sm' | 'xs' }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);
  return (
    <Button
      variant="outline"
      size={size === 'xs' ? 'sm' : size}
      className={size === 'xs' ? 'h-7 px-2 text-xs' : 'h-8 px-2.5 text-xs'}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
        } catch {
          // 剪贴板不可用时静默失败
        }
      }}
    >
      {copied ? <Check className="mr-1 h-3.5 w-3.5" /> : <Copy className="mr-1 h-3.5 w-3.5" />}
      {copied ? '已复制' : label}
    </Button>
  );
}

export function LoadingBlock({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 py-2">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

export function SpinnerLine({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" />
      {text}
    </div>
  );
}

export function ErrorBlock({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-6 py-10 text-center">
      <AlertTriangle className="h-6 w-6 text-destructive" />
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          重新加载
        </Button>
      )}
    </div>
  );
}

export function EmptyBlock({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed px-6 py-10 text-center">
      <Inbox className="h-6 w-6 text-muted-foreground/60" />
      <p className="text-sm font-medium">{title}</p>
      {hint && <p className="max-w-md text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** 状态容器：统一处理 loading / error / empty 三态。 */
export function StateGate({
  loading,
  error,
  onRetry,
  empty,
  emptyHint,
  isEmpty,
  children,
}: {
  loading: boolean;
  error?: string | null;
  onRetry?: () => void;
  isEmpty?: boolean;
  empty?: string;
  emptyHint?: string;
  children: ReactNode;
}) {
  if (loading) return <LoadingBlock />;
  if (error) return <ErrorBlock message={error} onRetry={onRetry} />;
  if (isEmpty && empty) return <EmptyBlock title={empty} hint={emptyHint} />;
  return <>{children}</>;
}

export interface DiffEntry {
  key: string;
  before: unknown;
  after: unknown;
}

export function DiffTable({ entries }: { entries: DiffEntry[] }) {
  const render = (v: unknown) => {
    if (v === null || v === undefined || v === '') return <span className="text-muted-foreground">（空）</span>;
    return <span className="break-all whitespace-pre-wrap">{String(v)}</span>;
  };
  return (
    <div className="overflow-hidden rounded-md border text-xs">
      <table className="w-full table-fixed">
        <thead>
          <tr className="border-b bg-muted/60 text-left text-muted-foreground">
            <th className="w-28 px-3 py-2 font-medium">字段</th>
            <th className="px-3 py-2 font-medium">修改前</th>
            <th className="px-3 py-2 font-medium">修改后</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.key} className="border-b last:border-b-0">
              <td className="px-3 py-2 align-top font-medium">{e.key}</td>
              <td className="px-3 py-2 align-top text-muted-foreground">{render(e.before)}</td>
              <td className="px-3 py-2 align-top text-teal-700">{render(e.after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function diffEntries(diff: Record<string, { before: unknown; after: unknown }> | null | undefined): DiffEntry[] {
  if (!diff) return [];
  return Object.entries(diff).map(([key, v]) => ({ key, before: v?.before, after: v?.after }));
}

export function JsonPre({ data }: { data: unknown }) {
  return <pre className="log-block">{JSON.stringify(data, null, 2)}</pre>;
}

// ---------------- 单次诊断质量指标（Trust Index / Faithfulness 等） ----------------

/** 单次诊断质量指标（后端 quality_scan.evaluate_diagnosis_quality 口径）。 */
export interface QualityMetrics {
  faithfulness: number;
  context_coverage: number;
  answer_relevance: number;
  hallucination_rate: number;
  trust_index: number;
  num_claims?: number;
  unsupported_claims?: string[];
  quality_ok: boolean;
  gate_line: number;
}

/** 质量指标卡：逐次展示 Trust Index 与四项生成质量指标，低于质量线标红提示。 */
export function QualityMetricsCard({ quality, title = '诊断质量评估' }: { quality: QualityMetrics | null | undefined; title?: string }) {
  if (!quality || typeof quality.trust_index !== 'number') return null;
  const items = [
    { key: 'faithfulness', label: 'Faithfulness 忠实度', value: quality.faithfulness },
    { key: 'context_coverage', label: '引用覆盖率', value: quality.context_coverage },
    { key: 'answer_relevance', label: '答案相关性', value: quality.answer_relevance },
    { key: 'hallucination_rate', label: '幻觉率', value: quality.hallucination_rate },
  ];
  return (
    <div className={cn('rounded-md border p-3', quality.quality_ok ? 'bg-teal-600/5' : 'bg-red-500/5')}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs font-semibold">{title}</p>
        <Badge
          variant="outline"
          className={cn(
            'font-medium',
            quality.quality_ok
              ? 'border-teal-600/40 bg-teal-600/10 text-teal-700'
              : 'border-red-500/40 bg-red-500/10 text-red-600',
          )}
        >
          Trust Index {(quality.trust_index * 100).toFixed(1)}%
        </Badge>
        <Badge variant="outline" className={quality.quality_ok ? 'text-teal-700' : 'text-red-600'}>
          {quality.quality_ok ? `达标（线 ${(quality.gate_line * 100).toFixed(0)}%）` : `低于质量线 ${(quality.gate_line * 100).toFixed(0)}%`}
        </Badge>
        {typeof quality.num_claims === 'number' && (
          <span className="ml-auto text-[11px] text-muted-foreground">断言数 {quality.num_claims}</span>
        )}
      </div>
      <div className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        {items.map((m) => (
          <div key={m.key}>
            <p className="text-[11px] text-muted-foreground">{m.label}</p>
            <p className={cn('text-sm font-semibold', m.value < 0.5 ? 'text-amber-700' : 'text-foreground')}>
              {(m.value * 100).toFixed(1)}%
            </p>
          </div>
        ))}
      </div>
      {quality.unsupported_claims && quality.unsupported_claims.length > 0 && (
        <div className="mt-2.5 rounded-md border border-amber-500/30 bg-amber-500/5 px-2.5 py-2">
          <p className="text-[11px] font-medium text-amber-700">
            未被检索证据支撑的断言（{quality.unsupported_claims.length}），建议人工核实：
          </p>
          <ul className="mt-1 list-disc pl-4">
            {quality.unsupported_claims.map((c, i) => (
              <li key={i} className="text-[11px] leading-relaxed text-amber-700/90">{c}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ---------------- 内容安全扫描（知识发布 / 审批预检） ----------------

/** 内容安全扫描命中明细。 */
export interface ContentScanHit {
  field: string;
  category: string;
  risk: string;
  count: number;
  sample: string;
}

/** 内容安全扫描结果（后端 quality_scan.scan_case_content 口径）。 */
export interface ContentScanResult {
  rule_version: string;
  risk_level: 'none' | 'medium' | 'high' | string;
  blocked: boolean;
  can_override: boolean;
  categories: string[];
  hits: ContentScanHit[];
  quality_score: number;
  scanned_fields: string[];
  override?: { allowed: boolean; reason: string };
}

const SCAN_CATEGORY_LABEL: Record<string, string> = {
  pii_email: '邮箱地址',
  pii_phone: '手机号',
  pii_id_card: '身份证号',
  secret_aws_key: 'AWS 密钥',
  secret_github: 'GitHub Token',
  secret_slack: 'Slack Token',
  secret_assignment: '凭证赋值',
  token_jwt: 'JWT Token',
  private_key: '私钥',
  dangerous_command: '危险命令',
  prompt_injection: '提示词注入',
  malicious_script: '恶意脚本',
};

const SCAN_RISK_TONE: Record<string, string> = {
  high: 'border-red-500/40 bg-red-500/10 text-red-600',
  medium: 'border-amber-500/40 bg-amber-500/10 text-amber-700',
  none: 'border-teal-600/40 bg-teal-600/10 text-teal-700',
};

/** 内容安全扫描卡：发布/审批场景展示规则版本、风险类别、命中明细与误报放行理由。 */
export function ContentScanCard({ scan, title = '内容安全扫描' }: { scan: ContentScanResult | null | undefined; title?: string }) {
  if (!scan || typeof scan !== 'object' || !scan.rule_version) return null;
  const riskLabel = scan.risk_level === 'high' ? '高风险' : scan.risk_level === 'medium' ? '中风险（含 PII）' : '未检出风险';
  return (
    <div
      className={cn(
        'rounded-md border p-3',
        scan.risk_level === 'high' ? 'border-red-500/40 bg-red-500/5' : scan.risk_level === 'medium' ? 'border-amber-500/40 bg-amber-500/5' : 'bg-teal-600/5',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs font-semibold">{title}</p>
        <Badge variant="outline" className={cn('font-medium', SCAN_RISK_TONE[scan.risk_level] ?? '')}>
          {riskLabel}
        </Badge>
        {scan.blocked && !scan.override && (
          <Badge variant="outline" className="border-red-500/40 bg-red-500/10 text-red-600">已拦截发布</Badge>
        )}
        {scan.override && (
          <Badge variant="outline" className="border-amber-500/40 bg-amber-500/10 text-amber-700">人工误报放行</Badge>
        )}
        <Badge variant="outline">质量分 {(scan.quality_score * 100).toFixed(0)}%</Badge>
        <span className="ml-auto text-[11px] text-muted-foreground">规则版本 {scan.rule_version}</span>
      </div>
      {scan.override && (
        <p className="mt-1.5 text-[11px] leading-relaxed text-amber-700">
          放行理由：{scan.override.reason}
        </p>
      )}
      {scan.hits.length > 0 ? (
        <div className="mt-2 overflow-hidden rounded-md border text-xs">
          <table className="w-full table-fixed">
            <thead>
              <tr className="border-b bg-muted/60 text-left text-muted-foreground">
                <th className="w-24 px-2.5 py-1.5 font-medium">字段</th>
                <th className="w-28 px-2.5 py-1.5 font-medium">风险类别</th>
                <th className="w-16 px-2.5 py-1.5 font-medium">级别</th>
                <th className="w-14 px-2.5 py-1.5 font-medium">次数</th>
                <th className="px-2.5 py-1.5 font-medium">命中样本（脱敏）</th>
              </tr>
            </thead>
            <tbody>
              {scan.hits.map((h, i) => (
                <tr key={`${h.category}-${h.field}-${i}`} className="border-b last:border-b-0">
                  <td className="px-2.5 py-1.5 font-mono">{h.field}</td>
                  <td className="px-2.5 py-1.5">{SCAN_CATEGORY_LABEL[h.category] ?? h.category}</td>
                  <td className="px-2.5 py-1.5">
                    <span className={cn(h.risk === 'high' ? 'text-red-600' : 'text-amber-700')}>
                      {h.risk === 'high' ? '高' : '中'}
                    </span>
                  </td>
                  <td className="px-2.5 py-1.5 font-mono">{h.count}</td>
                  <td className="truncate px-2.5 py-1.5 font-mono text-muted-foreground" title={h.sample}>{h.sample}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-1.5 text-[11px] text-muted-foreground">全部字段扫描通过，未检出 PII、密钥、危险命令、注入或恶意脚本。</p>
      )}
    </div>
  );
}
