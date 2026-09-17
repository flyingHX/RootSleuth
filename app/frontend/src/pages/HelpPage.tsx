/** 帮助中心：控制台使用手册与常见问题；完整文档见仓库 docs/ 目录。 */
import Markdown from 'markdown-to-jsx';
import { BookOpenText, LifeBuoy } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

const USER_GUIDE = [
  '# RootSleuth 运营控制台使用手册',
  '',
  '## 1. 登录与角色权限',
  '',
  '- 点击「使用 Atoms 账号登录」完成 OIDC 登录；预览环境可使用底部演示账号快捷登录。',
  '- 角色按层级从低到高：只读审计 viewer → 值班运维 operator → SRE → 审批人 approver → 知识库管理员 kb_admin → 系统管理员 sys_admin。',
  '- 未绑定角色的用户默认为 viewer，可在「审计与配置 → 配置中心」调整 default_role 或 role_bindings_json。',
  '- 关键权限：operator 可执行 AI 诊断与反馈；SRE 可编辑知识库、发起变更/合并/晋升；approver 可审批；kb_admin 可回滚版本；sys_admin 可发布规则与管理配置。',
  '',
  '## 2. 运营总览（Dashboard）',
  '',
  '- 核心指标：总事件数、降噪率、未知率、RAG 成功率、RAG P99 / 平均耗时。',
  '- 待办卡片：待审批数量、待晋升未知模板数量。',
  '- 依赖健康卡片：向量检索、缓存、归档存储与 LLM 服务的状态摘要。',
  '',
  '## 3. 告警工作台',
  '',
  '- 按严重级别 / 服务 / 集群 / 错误类型 / 状态 / 时间范围 / 关键字筛选告警。',
  '- 点开详情可查看原始日志、拓扑与 RAG 召回案例（含评分、根因、处置方案）。',
  '- 「AI 诊断」使用 deepseek-v4-flash 模型输出根因 / 建议 / 命令；置信度低于阈值（默认 0.75）会标记低置信。',
  '- 模型超时或输出异常时自动降级并标注原因，不影响告警查看。',
  '- 对诊断结果可复制命令、点赞 / 点踩；填写人工修正会自动生成知识库变更集并进入审批。',
  '',
  '## 4. 知识库治理',
  '',
  '- 编辑案例：修改字段实时预览前后对比，提交后按审批模式进入审批流（申请人不可自审）。',
  '- 新建案例：无需填写案例 ID，系统自动生成 KB-日期-当日序号 格式的唯一 ID，创建成功提示与审批内容中均可见。',
  '- 版本回滚：kb_admin 及以上可回滚到任意历史版本；回滚会生成新版本快照，历史不会丢失。',
  '- 去重合并：扫描同错误类型 + 同服务且模板相似度 ≥80% 的案例簇（低于 80% 不展示合并建议），指定主案例后其余归档，合并走审批；已提交审批的案例组在提案处理期间不再重复统计，拒绝后恢复。',
  '',
  '## 5. 审批中心',
  '',
  '- 三个视图：待我审批 / 我发起的 / 我已处理。',
  '- 审批模式（配置中心可切换）：OFF 免审直发、SINGLE_REVIEW 单级审批、MULTI_LEVEL 双级审批（审批人 → 知识库管理员）。',
  '- 每张审批单可展开「查看审批内容与前后对比」：知识库变更展示字段级 DiffTable；合并展示主 / 冗余案例；模板晋升展示模板与目标分类。',
  '- 操作：通过、拒绝（建议填意见）、撤回（仅申请人且待审状态）。终审通过后系统自动执行发布 / 合并 / 规则晋升并写审计。',
  '',
  '## 6. 规则管理',
  '',
  '- YAML 校验：规则 id 唯一、score 介于 (0,1]、keywords 与 pattern 至少其一。',
  '- 发布新版本（sys_admin）：旧激活版本自动标记已替代，热加载立即生效。',
  '- 版本回滚：目标版本重新激活并热加载。',
  '- 未知模板队列：晋升时填写目标错误类型，终审通过后自动按 0.6 分值追加规则并发布；无需沉淀的模板可废弃。',
  '',
  '## 7. 审计与配置',
  '',
  '- 审计日志记录全部关键操作（操作人、动作、对象、前后状态），支持按动作 / 操作人 / 对象类型筛选。',
  '- 配置中心（sys_admin）：审批模式、置信度阈值、重排权重、LLM 超时、功能开关、默认角色与角色绑定；修改即时生效并写审计。',
].join('\n');

const FAQ = [
  '# 常见问题 FAQ',
  '',
  '**Q1：为什么不能审批自己发起的申请？**',
  '系统强制禁止自审，需由同层级及以上的其他角色处理，保证变更可被独立复核。',
  '',
  '**Q2：新建案例还需要自己编案例 ID 吗？**',
  '不需要。提交后由后端自动生成 KB-YYYYMMDD-NNN 格式的唯一 ID，序号按当日已占用 ID 顺延，审批在途也不会重复。',
  '',
  '**Q3：审批通过后知识什么时候生效？**',
  '终审通过即写入新版本并失效语义缓存，流水线下一次检索即使用新知识。',
  '',
  '**Q4：AI 诊断显示「降级」怎么办？**',
  '查看详情中的降级原因：llm_timeout 可在配置中心调大 llm_timeout_seconds；invalid_json 重试即可；RAG 无召回建议把该类告警沉淀为新案例。',
  '',
  '**Q5：拒绝后变更去哪了？**',
  '变更集状态置为 rejected，不会发布内容；可在知识库「变更记录」中查看其 diff 与理由。',
  '',
  '**Q6：回滚会丢历史版本吗？**',
  '不会。案例回滚与规则回滚都是生成 / 激活版本快照，全部历史可追溯。',
  '',
  '**Q7：如何切换审批模式？**',
  'sys_admin 在「审计与配置 → 配置中心」修改 approval_mode，立即生效并留审计。',
  '',
  '**Q8：待我审批列表里为什么看不到某张单？**',
  '常见原因：你是申请人、当前步骤要求的角色高于你的角色、或该步骤已被处理。',
  '',
  '**Q9：未知模板晋升出的规则分值是多少？**',
  '固定 0.6、严重级别 warning，可在规则管理中后续调整并重新发布。',
  '',
  '**Q10：预览环境的演示账号有哪些？**',
  'demo-operator（值班运维）、demo-sre（SRE）、demo-lead（审批人）、demo-admin（系统管理员），分别对应四个演示角色。',
].join('\n');

function DocArticle({ content }: { content: string }) {
  return (
    <article className="prose prose-sm max-w-none dark:prose-invert">
      <Markdown>{content}</Markdown>
    </article>
  );
}

export default function HelpPage() {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">帮助中心</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            控制台使用手册与常见问题；完整 Markdown 文档见仓库 docs/ 目录。
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1"><BookOpenText className="h-3.5 w-3.5" />使用手册</span>
          <span className="flex items-center gap-1"><LifeBuoy className="h-3.5 w-3.5" />FAQ</span>
        </div>
      </div>

      <Tabs defaultValue="guide">
        <TabsList>
          <TabsTrigger value="guide">使用手册</TabsTrigger>
          <TabsTrigger value="faq">常见问题</TabsTrigger>
        </TabsList>
        <TabsContent value="guide" className="mt-4">
          <DocArticle content={USER_GUIDE} />
        </TabsContent>
        <TabsContent value="faq" className="mt-4">
          <DocArticle content={FAQ} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
