# 安全策略（SECURITY）

## 支持的版本

| 版本 | 支持状态 |
|------|----------|
| 1.0.x | ✅ 接收安全修复 |

## 如何报告漏洞

**请勿通过公开 Issue、Pull Request 或讨论区报告安全漏洞。**

请使用 GitHub 仓库的 **Private vulnerability reporting（私密漏洞上报）** 功能提交报告，或私下联系仓库维护者。报告请尽量包含：

- 漏洞类型与影响范围
- 复现步骤或概念验证（PoC）
- 受影响的版本 / 提交号
- 修复建议（如有）

我们承诺：**48 小时内确认收到，7 天内给出评估结论**；修复发布后会同步披露安全公告（Advisory）并致谢报告者（如无异议）。

## 上报范围说明

以下情况**不视为安全漏洞**，请勿上报：

- `docs/DEMO_ACCOUNTS.md` 中的演示账号与中间件默认凭证——该文档明确声明仅用于本地/演示环境，生产环境必须全部修改。
- RAG 链路的 fail-open 降级语义（依赖不可用时放行告警而非拦截）——这是告警可用性优先的显式设计决策，见项目 README。
- `/readyz` 恒返回 200 并以 `checks` 字段暴露依赖状态——同上，属于设计契约。

## 部署安全要点

自建部署（非托管平台）时，以下配置**必须**在上线前修改，详见各 `.env.example`：

- 控制台后端：`DATABASE_URL`（含数据库口令）、`JWT_SECRET_KEY`（随机 64+ 字符）、`MASK_KEY`、`CONSOLE_SECRET_KEY`。
- RAG 流水线：`LLM_API_KEY` 等所有密钥仅写入 `.env`（已被 `.gitignore` 排除），禁止提交。

### 已知设计注意事项

- `app/backend/core/mask_crypto.py` 用于静态加密控制台内配置的 LLM 密钥。当环境变量 `MASK_KEY` 未设置时，代码会回退到一个内置的默认密钥。**生产环境必须显式设置 `MASK_KEY` 并纳入密钥轮换流程**（轮换前需先用旧密钥解密存量数据）。开源发布后的版本计划移除内置回退，改为强制要求环境变量。
- `docs/DEMO_ACCOUNTS.md` 的演示账号仅适用于演示环境；面向生产部署时请禁用全部演示账号并启用独立实名账号。

## 入库红线

以下内容永远不允许进入仓库与 Git 历史（已在根目录 `.gitignore` 声明，发布前建议用 `gitleaks`/`trufflehog` 复扫）：

- 任何 `.env` 实际文件、API Key、JWT、数据库口令、私有证书
- 演示令牌文件（如 `.demo_admin_token.tmp`）、运行日志（`*.log`、`logs/`）
- 安装包（`aiops-suite-*.tar.gz`，通过 GitHub Release 附件分发）
- 平台上传暂存区 `uploads/` 与临时验证脚本 `*_tmp.py`
