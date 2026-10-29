# Tasks

> 每个任务按「半天以内可完成」拆分；勾选完成前必须跑通该任务声明的验证方式。

## 提交约定

每个任务完成即提交一次，格式为 Conventional Commits：`type(scope): subject`。

- type 取值：`feat` / `fix` / `refactor` / `test` / `docs` / `chore` / `build` / `ci`
- scope 取值：`gateway` / `guard` / `memory` / `llm` / `multimodal` / `runtime` / `config` / `docs`
- subject 用中文动宾短语，单条不超过 50 字；纯仓库维护类提交可省略 scope。
- 测试与所测代码放进同一提交；`tasks.md` 的勾选更新跟随所属任务提交，不单独提交进度。
- 完整规范与示例见 `docs/commit-convention.md`（任务 0.1 产出）。

## 0. 规范前置

- [x] 0.1 **提交规范文档（chore(docs)）**：编写 `docs/commit-convention.md`，明确 type（`feat` / `fix` / `refactor` / `test` / `docs` / `chore` / `build` / `ci`）、scope（`gateway` / `guard` / `memory` / `llm` / `multimodal` / `runtime` / `config` / `docs`）、subject 写法与示例，并规定「一个任务一次提交」及提交前自查（`make check` + `openspec validate` + 敏感文件扫描）。验证：文档中的 type / scope 与本文件「提交约定」完全一致；对照文档可逐条判定现有 `git log --oneline` 是否合规。

## 1. 工程骨架与运行时配置

- [x] 1.1a 初始化项目骨架：`pyproject.toml`（src layout，包名 `wechat_agent`，含最小包占位 `src/wechat_agent/__init__.py`）与 `.gitignore`（`.env`、`data/`、`.venv/`、缓存；已提交则核对覆盖项）。验证：`pip install -e .` 成功。
- [x] 1.1b 配置 ruff 与 dev 依赖：在 `pyproject.toml` 中加入 pytest / pytest-asyncio / respx / ruff，并落地 ruff 的 lint 与 format 配置。验证：`pip install -e ".[dev]"` 成功，`ruff check .` 在空项目上通过。
- [x] 1.2 **配置与 .env 解耦（独立任务）**：实现 `Settings`（pydantic-settings），覆盖设计文档中的配置表；生成 `.env.example`（仅键名、说明与安全默认值，不含真实密钥）；启动校验必填项与类型，缺失时只输出键名并非零退出；实现密钥脱敏工具。验证：`tests/test_config.py` 覆盖「缺 `LLM_API_KEY` 时报错只含键名」「`ALLOW_FROM` 允许空值」「非法数值类型报错」「脱敏输出不含完整值」四类用例；`pytest -k config` 通过；`grep -rE "sk-|Bearer [A-Za-z0-9]{20,}" src/` 无命中。备注：本任务落地首个测试文件时，在 `pyproject.toml` 加 `[tool.pytest.ini_options]` 并设 `asyncio_mode = "auto"`；本项目异步测试密集，strict 模式下每个测试都要标 `@pytest.mark.asyncio`，噪音大且易漏。
- [x] 1.3 结构化日志与运行目录：JSON 行日志写 `data/logs/app.log`、stdout 输出人类可读摘要、10MB × 5 滚动、统一脱敏过滤器；创建 `data/`（0700）与 `data/logs/`。验证：`tests/test_logging.py` 断言含 `api_key` / `authorization` 字段的日志被脱敏，且滚动在超过阈值后产生备份文件。备注：1.2 已设 `asyncio_mode = "auto"` 与 `asyncio_default_fixture_loop_scope = "function"`；后者保证 per-message / per-contact 状态的异步测试隔离——传输层、LLM 客户端与记忆存储都持有实例状态，function 级 loop 避免用例间串扰。
- [ ] 1.4 单实例锁、信号处理与 DRY_RUN 发送开关：`fcntl.flock` 锁 `data/.lock`；SIGINT/SIGTERM 触发停机回调；发送层支持 `DryRunSender`。验证：单元测试覆盖「第二个实例拿锁失败」「信号触发停机回调」「DRY_RUN 下不调用真实发送」。
- [ ] 1.5 本地质量门：`Makefile` 提供 `make lint`、`make test`、`make check`（ruff check + ruff format --check + pytest）。验证：在干净虚拟环境执行 `make check` 全部通过。备注：`ruff format --check` 只作用于 `src/` 与 `tests/`，不得用 `.` 扫全仓库；ruff 0.17.x 的 format 会连 Markdown 里的 Python 代码块一并扫描，`docs/` 与 `openspec/` 的示例片段不应被 formatter 管理。

## 2. 微信接入层（iLink）

- [ ] 2.1 归一化模型与 Transport 接口：定义 `NormalizedMessage`、`MessagePart`（text / image / voice / file / video / unknown）、`TransportError` / `SessionExpired` 等错误类型，以及 `WeChatTransport`（login / poll / send / close）；实现测试替身 `FakeTransport`。验证：`tests/test_models.py` 断言未知类型落入 `unknown`，缺失可选字段时不抛异常。
- [ ] 2.2 iLink HTTP 客户端：封装 `ILINK_BASE_URL`、认证头（`AuthorizationType`、`Authorization`、`X-WECHAT-UIN`）、`base_info`、连接与读取超时、错误码映射（`-14` → `SessionExpired`）。验证：`respx` 单测断言请求头与请求体、超时生效、`-14` 映射正确；另用真实 `get_bot_qrcode` 请求确认返回字段名与 `design.md` 的契约表一致，如有差异记录到 `docs/acceptance.md`。
- [ ] 2.3 二维码登录与会话建立：获取二维码 → 展示可扫描内容 → 轮询状态 → `confirmed` 后原子写 `data/credentials.json`（0600）；`expired` 自动刷新最多 3 次；凭证损坏视为无凭证。验证：`respx` 覆盖「确认后落盘且日志无 token」「连续 3 次过期后退出」「损坏凭证重新登录」三条路径。
- [ ] 2.4 长轮询接收与游标持久化：调用 `getupdates`，本地读超时视为空轮询并立即用相同游标重试；游标原子写 `data/cursor.json` 并在重启时恢复；连续网络失败按 2s → 30s 指数退避。验证：`respx` + 假时钟测试覆盖「超时不计失败」「重启后使用已存游标」「退避序列 2/4/8…30」。
- [ ] 2.5 去重与自身消息忽略：基于消息 id 的 LRU（内存）+ 近期 id 持久化；`message_type = 2` 的自身消息直接忽略。验证：单测断言重复消息只触发一次处理、自身消息不进入后续管线。
- [ ] 2.6 发送端与文本分片：实现 `sendmessage`（回填 `context_token`、`message_type=2`、`message_state=2`）；超过 2000 字符按段落/句子边界分片并按序发送；某片失败时停止后续；缺少 `context_token` 时跳过并告警。验证：`tests/test_sender.py` 覆盖「4500 字拆 3 条且顺序一致」「缺 token 不发送」「中途失败停止」。
- [ ] 2.7 接入层集成测试：用 `FakeTransport` + `respx` 串起「扫码登录 → 长轮询 → 归一化 → 去重 → `-14` 重登 → 退避重试」。验证：`pytest tests/gateway/` 全绿，且测试过程无真实网络请求。

## 3. 消息准入与管线编排

- [ ] 3.1 白名单解析与匹配：逗号分隔、trim、去重、空名单全拒绝、精确匹配（无通配符）。验证：`tests/test_guard_whitelist.py` 覆盖「命中放行」「未命中静默丢弃」「空名单全拒绝」「`id_1` 不匹配 `id_12`」。
- [ ] 3.2 形式校验、长度截断与重复抑制：空白/纯表情/纯标点丢弃；超过 2000 字符截断并标记；30 秒滑动窗口内重复文本只放行首次。验证：单测覆盖四个分支，并断言窗口过期（31 秒后）相同文本可再次放行。
- [ ] 3.3 过滤链短路与丢弃日志：固定顺序「白名单 → 形式 → 长度 → 重复 → 多模态 → LLM」，每步短路；丢弃日志只含消息 id、发送者标识、阶段、原因。验证：单测断言非白名单图片不触发多模态处理器；`caplog` 断言日志不含消息正文。
- [ ] 3.4 管线编排：有界队列（默认 100）+ 单 worker + 每联系人串行；队列满时拒绝新消息并告警；`DRY_RUN` 贯穿全链路。验证：`tests/test_pipeline.py` 断言同一联系人三条消息按 1→2→3 顺序处理、队列满时新消息被拒且进程不崩。

## 4. 对话记忆

- [ ] 4.1 记忆数据模型与原子存储：每联系人一个 JSON（`data/memory/<contact>.json`），临时文件 + `fsync` + `os.replace` 写入；损坏文件重命名 `.corrupt` 后以空记忆启动；目录 0700、文件 0600。验证：`tests/test_memory_store.py` 覆盖「模拟写入中断后旧文件仍可解析」「损坏恢复」「权限位正确」。
- [ ] 4.2 短期窗口：`deque(maxlen=20)`，超出淘汰最旧；按联系人隔离；收到用户消息先写 user，发送成功后写 assistant。验证：单测断言写入第 21 条后容量仍为 20 且最旧被淘汰；发送失败时不写 assistant。
- [ ] 4.3 长期摘要：新增 ≥10 条或空闲 ≥10 分钟触发；合并旧摘要并保留关键事实；失败时保留 `pending` 并退避，不阻塞回复。验证：mock LLM 单测覆盖「合并后旧要点仍在」「摘要失败时短期记录不丢」「下一次触发会重试」。
- [ ] 4.4 上下文注入与预算裁剪：摘要进入 system 段；短期记录按时间序；字符预算 8000 超出时由旧到新裁剪，当前消息与 system 永不裁剪。验证：`tests/test_prompt_budget.py` 断言裁剪顺序与保留项。
- [ ] 4.5 记忆清空入口与隐私检查：提供按联系人清空命令；日志不含记忆正文；`data/` 在 `.gitignore` 内。验证：执行清空后联系人 A 文件消失、B 文件不变；`git check-ignore data/memory/x.json` 命中。

## 5. 回复生成

- [ ] 5.1 OpenAI 兼容客户端：调用 `{LLM_BASE_URL}/chat/completions`；连接 10 秒、读取 30 秒超时；429/5xx/网络错误重试 1 次，4xx 不重试；错误分类为可重试/不可重试。验证：`respx` 单测覆盖超时、429 重试成功、400 不重试、重试次数上限。
- [ ] 5.2 人设与请求组装：system = 人设 + 长期摘要；随后按序放历史 user/assistant；当前消息作为独立 user。自定义 `SYSTEM_PROMPT` 生效，未配置时使用默认人设。验证：单测断言「用户文本不出现在 system 字段」「自定义人设出现在请求中」「缺省人设可用」。
- [ ] 5.3 提示注入防护：用户文本中的「忽略以上指令」「系统标签」等内容不得改写 system 结构；回复不泄露人设提示。验证：单测断言请求体结构与 system 内容不变；补充一条需要人工复核的验收用例到 `docs/acceptance.md`。
- [ ] 5.4 降级与输出后处理：超时/失败/空输出 → 兜底话术或静默；输出 strip 与控制字符清理；超过 2000 字符交给分片；处理后为空按失败处理。验证：单测覆盖「超时发兜底」「空输出视失败」「关闭兜底则静默」「超长输出触发分片」四个分支。

## 6. 多模态占位

- [ ] 6.1 多模态分发与注册表：按类型判定 image / voice / file / video / unknown；`MediaHandler` 注册接口；未注册或处理器抛异常时回落默认处理器。验证：单测覆盖「未知类型降级」「处理器抛异常被隔离且主循环继续」。
- [ ] 6.2 默认降级处理器：白名单内按类型回复默认或自定义话术，且不调用 LLM；白名单外静默（由过滤链保证）。验证：单测断言 LLM 客户端零调用、话术可配置生效、默认话术不超过单条上限。
- [ ] 6.3 混合消息与媒体不落盘：文本 + 媒体只取文本生成回复并追加「附件暂不支持」；不发起 CDN 下载/解密；日志只记录类型与大小。验证：`respx` 断言无媒体下载请求；集成测试覆盖「图文混发」与「仅媒体」两种输入。

## 7. 集成与真机验收

- [ ] 7.1 端到端集成测试：假 iLink + 假 LLM 串起「文本闭环 / 白名单外静默 / 重复抑制 / 图片降级 / 重启恢复游标与记忆 / LLM 超时兜底」六条主路径。验证：`pytest tests/integration/` 全绿，测试内无真实网络访问（respx 未匹配请求即失败）。
- [ ] 7.2 真机手工验收：真实扫码登录，用个人小号发送文本与图片；先 `DRY_RUN=true` 对照拟发送内容，再关闭并确认实际收到回复。验证：`docs/acceptance.md` 中的验收清单全部勾选，附脱敏截图与时间戳。
- [ ] 7.3 稳定性与异常演练：断网 1 分钟、模拟 `-14`、SIGTERM 停机、重复启动、macOS 休眠后恢复各执行一次。验证：演练记录写入 `docs/acceptance.md`，进程均按设计恢复或退出，且游标/记忆无丢失。

## 8. 文档、提交规范与交付

- [ ] 8.1 README 编写：包含架构图与模块职责、能力边界、macOS 安装与运行步骤、`.env` 配置表、扫码登录流程、防休眠建议（`caffeinate`）、已知限制（仅被动回复、无群聊、无媒体解析、风控风险）、故障排查。验证：在干净的 macOS 环境严格按 README 从零走到扫码登录成功；文中每条命令可原样执行。
- [ ] 8.2 演示视频录制说明：`docs/demo-script.md` 写明 5 个演示场景（文本回复、白名单外静默、图片降级、记忆连续性、DRY_RUN 对比）、录制步骤、3–5 分钟的节奏建议、脱敏要求（遮挡二维码、联系人昵称、密钥）、画面要求（必须保留 macOS 菜单栏或终端标题栏，用于证明运行环境是 macOS）、文件命名与存放位置。验证：按该脚本可完整录制一遍，视频中不出现敏感信息，且画面保留 macOS 菜单栏或终端标题栏。
- [ ] 8.3 提交规范抽查：按 `docs/commit-convention.md`（任务 0.1 产出）核对历史提交。验证：`git log --oneline` 中每个实现提交均符合 `type(scope): subject`，随机抽查 5 条人工确认，结果记录在 `docs/acceptance.md`。
- [ ] 8.4 最终交付自查：执行 `openspec validate wechat-ai-agent`、`make check`；确认 `git ls-files` 中无 `.env`、`data/`、`credentials.json`；确认 README 内链接与命令有效。验证：三项检查全部通过，结果记录在 `docs/acceptance.md`。

## Workflow follow-up

- 归档前必须确认：`openspec validate --specs` 通过、`git status` 干净、`data/` 未被 git 跟踪。
- 按项目 review 要求完成后归档该变更（`/opsx:archive`）。
- 归档后确认 `openspec/specs/` 下已生成 6 个 capability，且 `openspec validate --specs` 通过。
