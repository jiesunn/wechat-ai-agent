# 提交规范（Conventional Commits）

> 本文件由任务 0.1 产出，与 `openspec/changes/wechat-ai-agent/tasks.md` 的「提交约定」保持一致；如有冲突，以 `tasks.md` 为准并同步修订本文件。

## 1. 适用范围

适用于本项目从任务 0.1 起的全部提交，包括实现、测试、文档与仓库维护。既有的三条初始化提交（`9af079b`、`f71ecd0`、`208bc81`）保持不变，不回改历史。

## 2. 提交格式

```text
<type>(<scope>): <subject>

<body>

<footer>
```

- `type` 必填，取自第 3 节列表。
- `scope` 取自第 4 节列表；纯仓库维护类提交可省略。
- `subject` 必填；`body` 与 `footer` 可选。

## 3. type

type 取值：`feat` / `fix` / `refactor` / `test` / `docs` / `chore` / `build` / `ci`

| type | 用途 |
| --- | --- |
| `feat` | 新增用户可感知的能力 |
| `fix` | 修复缺陷 |
| `refactor` | 不改变外部行为的结构调整 |
| `test` | 仅新增或调整测试；与被测代码同一次提交的测试不单独拆成 `test` 提交 |
| `docs` | 产品与项目文档：README、验收记录、演示脚本等 |
| `chore` | 仓库维护：工具链与依赖、忽略规则、工作流文件、流程规范类文档 |
| `build` | 构建与打包配置：`pyproject.toml` 的构建段、`Makefile` |
| `ci` | CI 配置（当前未引入，预留） |

## 4. scope

scope 取值：`gateway` / `guard` / `memory` / `llm` / `multimodal` / `runtime` / `config` / `docs`

| scope | 覆盖范围 |
| --- | --- |
| `gateway` | iLink 适配、二维码登录、长轮询与游标、去重、发送与文本分片 |
| `guard` | 白名单、形式校验、长度处理、重复抑制、过滤链 |
| `memory` | 短期窗口、长期摘要、记忆存储与清空 |
| `llm` | OpenAI 兼容客户端、prompt 组装、降级与输出后处理 |
| `multimodal` | 媒体类型判定、处理器注册表、媒体降级话术 |
| `runtime` | 进程模型、有界队列、单实例锁、信号停机、日志与运行目录 |
| `config` | Settings、`.env` / `.env.example`、启动校验、密钥脱敏 |
| `docs` | 文档与 OpenSpec 规划产物 |

scope 选择规则：

- 只改一个模块时，使用该模块。
- 跨多个模块时，取改动量最大、最能代表本次意图的模块。
- 没有模块归属的仓库维护（依赖、忽略规则、编辑器配置）省略 scope。

## 5. subject 写法

- 中文动宾短语，说明「做了什么」，如「实现长轮询与游标持久化」。
- 不超过 50 字，不以句号结尾。
- 避免「更新」「修改」「优化」这类不说明对象的空泛表述。
- 代码标识保留原文与大小写，如 `context_token`、`ALLOW_FROM`。
- `scope` 一律小写。

## 6. body 与 footer

`body` 可选，用于解释「为什么」与关键取舍，不复述 diff。建议首行标注对应任务，便于与 `tasks.md` 对照：

```text
Task: 2.4
```

需要时在 body 补验证证据（命令与结果）。破坏性变更使用 footer：

```text
BREAKING CHANGE: <影响与迁移方式>
```

## 7. 提交粒度

- 一个 `tasks.md` 任务对应一次提交；任务内的实现、测试、文档一起提交。
- `tasks.md` 的勾选更新跟随所属任务提交，不单独提交「更新进度」。
- 纯重构单独提交，不与功能改动混在一起。
- 每次提交必须是一个可独立回滚、可独立验证的逻辑单元。

## 8. 提交前自查

1. 暂存区核对：`git diff --cached --name-only` 只包含本次任务涉及的文件，禁止 `git add -A`。
2. 质量门：`make check`（1.5 就绪后）；此前至少跑 `ruff check .` 与相关 `pytest`。
3. 规格校验：`openspec validate wechat-ai-agent` 通过。
4. 敏感文件扫描：`git status --short` 中不出现 `.env`、`data/`；`git ls-files | grep -E '^(\.env|data/|.*credentials\.json)'` 无输出。
5. 提交信息核对：符合 `type(scope): subject`。

## 9. 示例

正确：

- `feat(gateway): 实现 iLink 长轮询与游标持久化`
- `feat(guard): 增加白名单精确匹配与空名单全拒绝`
- `fix(memory): 摘要失败时保留 pending 并退避重试`
- `refactor(llm): 拆分 prompt 组装与预算裁剪`
- `test(multimodal): 覆盖处理器异常隔离`
- `docs(openspec): 细化提交约定与任务拆分`
- `chore(docs): 添加提交规范文档`
- `chore: 添加 .gitignore`
- `chore(config): 初始化 pyproject.toml 与包骨架`
- `feat(config): 支持 .env 加载与启动校验`

错误：

- `update code`：缺少 type，没有信息量。
- `fix bug`：未说明修复对象。
- `feat: 增加微信接入、过滤、记忆、LLM、多模态和文档`：多任务混合，粒度过大。
- `feat(Gateway): 实现长轮询`：scope 必须小写。
- `feat(gateway)：实现长轮询`：必须使用半角冒号。
