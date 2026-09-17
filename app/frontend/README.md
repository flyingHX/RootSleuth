# RootSleuth 运营控制台前端

> 项目定位：本目录是 RootSleuth 运营控制台前端（React + Vite + shadcn/ui + Tailwind CSS）。项目总览见仓库根目录 `README.md`，使用手册见 `docs/CONSOLE_USER_GUIDE.md`，帮助中心页面为 `/help`。

## 技术栈

- Vite / TypeScript / React
- shadcn/ui（组件位于 `src/components/ui`）
- Tailwind CSS

## 目录结构

```
app/frontend/
├── index.html         # HTML 入口
├── vite.config.ts     # Vite 配置
├── tailwind.config.ts # Tailwind 配置
├── package.json
└── src/
    ├── main.tsx       # 应用入口
    ├── App.tsx        # 路由外壳
    ├── pages/         # 页面（告警工作台、Agent 工作台、知识库、规则、运维、帮助等）
    ├── components/    # 业务组件与 UI 组件
    └── index.css      # 全局样式与主题变量
```

## 环境变量

```bash
cp .env.example .env   # VITE_API_BASE_URL 指向控制台后端地址
```

## 开发命令

```bash
pnpm i              # 安装依赖
pnpm run dev        # 本地开发（默认 3000 端口，自动避让）
pnpm run lint       # ESLint 检查
pnpm run build      # 生产构建
```

## 说明

- `@/` 路径别名指向 `src/` 目录。
- 页面路由集中在 `src/App.tsx`，新增页面请在 `src/pages/` 下创建并注册路由。
