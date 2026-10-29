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
- [x] 1.4 单实例锁、信号处理与 DRY_RUN 发送开关：`fcntl.flock` 锁 `data/.lock`；SIGINT/SIGTERM 触发停机回调；发送层支持 `DryRunSender`。验证：单元测试覆盖「第二个实例拿锁失败」「信号触发停机回调」「DRY_RUN 下不调用真实发送」。
- [x] 1.5 本地质量门：`Makefile` 提供 `make lint`、`make test`、`make check`（ruff check + ruff format --check + pytest）。验证：在干净虚拟环境执行 `make check` 全部通过。备注：`ruff format --check` 只作用于 `src/` 与 `tests/`，不得用 `.` 扫全仓库；ruff 0.17.x 的 format 会连 Markdown 里的 Python 代码块一并扫描，`docs/` 与 `openspec/` 的示例片段不应被 formatter 管理。

## 2. 微信接入层（iLink）

- [x] 2.1 归一化模型与 Transport 接口：定义 `NormalizedMessage`、`MessagePart`（text / image / voice / file / video / unknown）、`TransportError` / `SessionExpired` 等错误类型，以及 `WeChatReceiver`（login / poll / close）与 `WeChatSender`（send_text）；实现测试替身 `FakeTransport`（实现 `WeChatReceiver`）。验证：`tests/test_models.py` 断言未知类型落入 `unknown`，缺失可选字段时不抛异常。备注：2.1 最初定义的是合并的 `WeChatTransport`，在 3.x 前的独立清理中拆成两个端口；`ILinkTransport` 实现 `WeChatReceiver`，`ILinkSender` 实现 `WeChatSender`。
- [x] 2.2 iLink HTTP 客户端：封装 `ILINK_BASE_URL`、认证头（`AuthorizationType`、`Authorization`、`X-WECHAT-UIN`）、`base_info`、连接与读取超时、错误码映射（`-14` → `SessionExpired`）。验证：`respx` 单测断言请求头与请求体、超时生效、`-14` 映射正确；另用真实 `get_bot_qrcode` 请求确认返回字段名与 `design.md` 的契约表一致，如有差异记录到 `docs/acceptance.md`。备注：真实 `get_bot_qrcode` 契约核对随 7.2 真机验收执行；`httpx` 运行时依赖已随 2.3 补进 `pyproject.toml`。
- [x] 2.3 二维码登录与会话建立：获取二维码 → 展示可扫描内容 → 轮询状态 → `confirmed` 后原子写 `data/credentials.json`（0600）；`expired` 自动刷新最多 3 次；凭证损坏视为无凭证。验证：`respx` 覆盖「确认后落盘且日志无 token」「连续 3 次过期后退出」「损坏凭证重新登录」三条路径。备注：另覆盖「`wait`/`scaned` → `confirmed`」的状态转换与已有凭证跳过扫码；凭证写入为临时文件 + `fsync` + `os.replace`。
- [x] 2.4 长轮询接收与游标持久化：调用 `getupdates`，本地读超时视为空轮询并立即用相同游标重试；游标原子写 `data/cursor.json` 并在重启时恢复；连续网络失败按 2s → 30s 指数退避。验证：`respx` + 假时钟测试覆盖「超时不计失败」「重启后使用已存游标」「退避序列 2/4/8…30」。备注：游标在 `getupdates` 成功后先落盘再返回消息（崩溃最多重复最后一批）；`poll(None)` 自动读取已存游标；退避由 `ILinkTransport.poll_with_backoff` 实现并注入 `sleep` 便于假时钟测试；`msgs` 归一化为 `NormalizedMessage`（`item_list` 类型 1–5 映射 text/image/voice/file/video，其余归 `unknown`）。
- [x] 2.5 去重与自身消息忽略：基于消息 id 的 LRU（内存）+ 近期 id 持久化；`message_type = 2` 的自身消息直接忽略。验证：单测断言重复消息只触发一次处理、自身消息不进入后续管线。备注：去重对象是归一化后的 `msg_id`（`SeenIdStore.is_duplicate`），内存与 `data/seen_ids.json` 均按 LRU 保留最近 1000 条，复用 `cursor.py` 的原子写；自身消息在 `_normalize_message` 过滤，debug 日志仅含 `msg_id` 与原因。
- [x] 2.6 发送端与文本分片：实现 `sendmessage`（回填 `context_token`、`message_type=2`、`message_state=2`）；超过 2000 字符按段落/句子边界分片并按序发送；某片失败时停止后续；缺少 `context_token` 时跳过并告警。验证：`tests/test_sender.py` 覆盖「4500 字拆 3 条且顺序一致」「缺 token 不发送」「中途失败停止」。备注：`split_text` 以原文连续切片方式按段落 → 句子 → 硬切选切点，拼回等于原文；日志只含 `msg_id`、分片序号、总片数、联系人哈希，不含正文与原始 wxid。
- [x] 2.7 接入层集成测试：用 `FakeTransport` + `respx` 串起「扫码登录 → 长轮询 → 归一化 → 去重 → `-14` 重登 → 退避重试」。验证：`pytest tests/integration/` 全绿，且测试过程无真实网络请求。备注：路径 1/2/3/5/6 用 respx 严格 mock 的 `ILinkTransport`（扫码、凭证、游标、归一化、退避都在适配器内），路径 4 注入 `FakeTransport` 验证端口可替换；未 patch 适配器内部方法。顺带移除 `test_ilink_poll.py` 中对 `propagate` 的 monkeypatch，消除测试顺序依赖。

## 3. 消息准入与管线编排

- [x] 3.1 白名单解析与匹配：逗号分隔、trim、去重、空名单全拒绝、精确匹配（无通配符）。验证：`tests/test_guard_whitelist.py` 覆盖「命中放行」「未命中静默丢弃」「空名单全拒绝」「`id_1` 不匹配 `id_12`」。备注：`wechat_agent/guard/whitelist.py` 的 `Whitelist` 在构造时解析一次，`allows()` 只做 `frozenset` 成员判断；`None`、空字符串、纯空白与纯逗号都解析为空集并全部拒绝。顺带删除 `FakeTransport.send` 及其两条单测（2.1 拆分遗留，发送属于 `WeChatSender`）。
- [x] 3.2 形式校验、长度截断与重复抑制：空白/纯表情/纯标点丢弃；超过 2000 字符截断并标记；30 秒滑动窗口内重复文本只放行首次。验证：单测覆盖四个分支，并断言窗口过期（31 秒后）相同文本可再次放行。备注：`wechat_agent/guard/validate.py` 提供 `has_meaningful_content`（逐字符 `str.isalnum()`，汉字天然为 True，不依赖 `\w` 以免误判全角标点）、`truncate_text`（默认 2000，返回 `(文本, truncated)`，超长继续处理不丢弃）与 `DuplicateSuppressor`（键为 `(contact_id, sha256(归一化文本))`，归一化为 `" ".join(text.split())`；窗口从最近放行时刻起算，重复命中不续期，时钟可注入）。测试用假时钟，23 条用例 0.11s。
- [x] 3.3 过滤链短路与丢弃日志：固定顺序「白名单 → 形式 → 长度 → 重复 → 多模态 → LLM」，每步短路；丢弃日志只含消息 id、发送者标识、阶段、原因。验证：单测断言非白名单图片不触发多模态处理器；`caplog` 断言日志不含消息正文。备注：`wechat_agent/guard/chain.py` 的 `FilterChain.steps` 显式固定为 `WhitelistStep → FormStep → LengthStep → DuplicateStep → MultimodalStep`，全部通过后 `stage=llm`；每步返回 `(keep, reason)`，短路时 `ChainResult.stage/reason` 指出拦截位置。`AdmissionContext` 携带文本、`truncated` 与媒体类型，截断先于去重，截断后的文本参与 hash；纯媒体调用注入的 `MediaHandler.handle` 后不进 LLM，文本+媒体只取文本继续，非白名单媒体在白名单步骤短路、处理器零调用。丢弃日志复用 2.6 `wechat.sender.contact_hash`，只含 `msg_id` / `contact_hash` / `stage` / `reason`；媒体成功分流不算丢弃、不写丢弃日志。13 条用例。
- [x] 3.4 管线编排：有界队列（默认 100）+ 单 worker + 每联系人串行；队列满时拒绝新消息并告警；`DRY_RUN` 贯穿全链路。验证：`tests/test_pipeline.py` 断言同一联系人三条消息按 1→2→3 顺序处理、队列满时新消息被拒且进程不崩。备注：`wechat_agent/pipeline.py` 的 `Pipeline` 用 `asyncio.Queue(maxsize=100)` + 单 dispatcher；dispatcher 按入队顺序为每条消息创建任务，任务在 `dict[from_user_id, asyncio.Lock]` 上串行，不同联系人可并发。`submit()` 用 `put_nowait` 捕获 `QueueFull`，记 `queue_full` 告警并返回 False，不阻塞轮询；`stop()` 取消 dispatcher、拒绝新消息、等待在途任务（默认 5 秒，超时取消），可直接作为 `ShutdownController` 的回调，无需改动 1.4 或 3.3。管线只依赖注入的 `MessageSender` 与 `ReplyGenerator`，不构造 `ILinkSender`；`DRY_RUN` 由 1.4 的 `build_sender` 在组合层选择 `DryRunSender`。5 条用例覆盖同联系人顺序、跨联系人并发、队列满、DRY_RUN 零调用与停机排空；全量 147 passed。

## 4. 对话记忆

- [x] 4.1 记忆数据模型与原子存储：每联系人一个 JSON（`data/memory/<contact>.json`），临时文件 + `fsync` + `os.replace` 写入；损坏文件重命名 `.corrupt` 后以空记忆启动；目录 0700、文件 0600。验证：`tests/test_memory_store.py` 覆盖「模拟写入中断后旧文件仍可解析」「损坏恢复」「权限位正确」。备注：`wechat_agent/memory/store.py` 的 `MemoryStore` 复用 2.6 的 `wechat.sender.contact_hash`（sha256 前 12 位）命名 `data/memory/<hash>.json`，原始 wxid 不落盘；`MemoryRecord` 含 `short_term`（`MemoryEntry{role, content, ts}` 列表，容量语义留给 4.2）、`summary`、`summary_updated_at`、`summarized_upto`、`pending_since`，缺失字段按空值补齐、类型非法视为损坏。`save()` 复用 `wechat.cursor` 的 `ensure_private_dir`（显式 chmod 0700）与 `atomic_write_text`（临时文件 + fsync + os.replace，0600），未改动 cursor.py。`load()` 对文件缺失返回空记忆；半截 JSON、非法 UTF-8、顶层或字段类型非法时把文件改名为 `<file>.corrupt`（覆盖旧现场）后返回空记忆，隔离失败只告警不抛异常；告警只含联系人哈希与原因，不含正文。9 条用例覆盖读写往返、联系人隔离、模拟 `os.replace` 中断后旧文件仍可解析、半截 JSON 与非法结构两类恢复、非法编码、旧 `.corrupt` 覆盖、目录 0755→0700 与文件 0600。
- [x] 4.2 短期窗口：`deque(maxlen=20)`，超出淘汰最旧；按联系人隔离；收到用户消息先写 user，发送成功后写 assistant。验证：单测断言写入第 21 条后容量仍为 20 且最旧被淘汰；发送失败时不写 assistant。备注：`wechat_agent/memory/short_term.py` 的 `ShortTermMemory` 用 `dict[contact_hash, deque[MemoryEntry]]` 维护每联系人窗口，`max_entries` 默认 20（对应 `SHORT_MEMORY_SIZE`），deque 的 `maxlen` 直接淘汰最旧；首次访问经 `MemoryStore` 惰性加载，追加后「读-改-写」只替换 `short_term`，摘要等字段原样保留并原子落盘，重启可恢复。`append_user` 在收到消息时立即写；`append_assistant(..., sent=...)` 只有 `sent=True` 才写入，发送失败返回 `None` 且不触碰存储。时间戳由可注入时钟生成，统一转为 UTC 后输出 aware ISO 字符串（`+00:00`），未使用 `time.time()`。6 条用例覆盖 21 条写入后容量 20 且第 1 条淘汰、发送失败仅保留 user、发送成功 user→assistant 顺序、联系人隔离、时间戳 UTC 转换、摘要字段保留与重启恢复。
- [x] 4.3 长期摘要：新增 ≥10 条或空闲 ≥10 分钟触发；合并旧摘要并保留关键事实；失败时保留 `pending` 并退避，不阻塞回复。验证：mock LLM 单测覆盖「合并后旧要点仍在」「摘要失败时短期记录不丢」「下一次触发会重试」。备注：`wechat_agent/memory/summary.py` 的 `SummaryEngine` 通过构造函数注入 `MemoryStore`、`ShortTermMemory` 与 `SummaryClient`（`async complete(*, system, user)`），默认阈值 10 条 / 10 分钟、失败退避 60 秒，两个触发条件为 OR。新增条目以 `summary_updated_at` 为边界按 `ts` 判定，避免 20 条窗口淘汰后 `summarized_upto` 边界错位，未改动 4.1 `store.py` 与 4.2 `short_term.py`；`summarized_upto` 按累计已摘要条数递增。prompt 由 system 指令（人物关系/偏好/约定/待办四类要点、保留旧事实、300 字内）与 user 段（旧摘要 + 新增对话）组成。成功时重读记录后更新 `summary`、`summary_updated_at`、`summarized_upto` 并清空 `pending_since`；失败时首次写入 `pending_since`、后续保留，捕获异常只记联系人与错误类型，不抛给管线、不改短期记录，退避窗口过后再次触发即重试。4 条用例覆盖只满足条数触发、只满足空闲触发、失败保留 pending + 退避重试 + 短期记录不丢、合并后旧要点仍在且四字段状态正确。
- [x] 4.4 上下文注入与预算裁剪：摘要进入 system 段；短期记录按时间序；字符预算 8000 超出时由旧到新裁剪，当前消息与 system 永不裁剪。验证：`tests/test_prompt_budget.py` 断言裁剪顺序与保留项。备注：`wechat_agent/memory/prompt.py` 的 `build_messages` 接收 `persona`、`summary`、短期快照与当前 `user_text`，输出 OpenAI 兼容的 `list[ChatMessage]`：system 在首位，历史保持输入时间序，当前 user 在末位。system 内容为「人设 + 空行 + 以下是你记得的长期信息： + 摘要」，摘要为空或纯空白时只保留人设，不拼占位。预算按各消息 `len(content)` 求和（默认 8000，不引入 tokenizer），从最新历史往回装，装不下即停止，等价于丢弃最旧记录，结果保持原时间序；system 与当前 user 不计入裁剪，预算小于二者之和时仍保留，历史全部丢弃。未改动 4.2 `short_term.py` 与 4.3 `summary.py`。实际用例在 `tests/test_memory_prompt.py`，5 条覆盖不超预算全保留、超预算只留最新后缀、极端预算只留 system+当前消息、裁剪后顺序、空摘要无占位。
- [x] 4.5 记忆清空入口与隐私检查：提供按联系人清空命令；日志不含记忆正文；`data/` 在 `.gitignore` 内。验证：执行清空后联系人 A 文件消失、B 文件不变；`git check-ignore data/memory/x.json` 命中。备注：`wechat_agent/memory/clear.py` 的 `clear_memory(contact_id, *, store, short_term) -> bool` 只做单联系人粒度，不实现批量清空：删除主文件与 `<hash>.json.corrupt`，任一文件删除即返回 True；随后调用 `ShortTermMemory.forget()` 丢弃内存窗口（为此给 4.2 补了该公共方法），避免下一次追加把旧记录写回，摘要随主文件一并消失。日志只含 `contact_hash` 与 `file_removed`，不读取正文。`memory/__init__.py` 导出 `clear_memory`。3 条用例覆盖 A 文件消失/B 文件字节不变、`.corrupt` 删除与返回值、日志不含敏感词与原始 wxid；隐私检查 grep `data/logs/app.log` 敏感词 0 命中，`git check-ignore` 命中 `.gitignore:43:data/`。

## 5. 回复生成

- [x] 5.1 OpenAI 兼容客户端：调用 `{LLM_BASE_URL}/chat/completions`；连接 10 秒、读取 30 秒超时；429/5xx/网络错误重试 1 次，4xx 不重试；错误分类为可重试/不可重试。验证：`respx` 单测覆盖超时、429 重试成功、400 不重试、重试次数上限。备注：`wechat_agent/llm/client.py` 的 `LLMClient(base_url, api_key, model)` 由构造函数注入凭据与模型，不读环境变量；`httpx.Timeout` 四维显式设置（connect/write/pool=10、read=30），未用单参数简写。重试只针对 429 / 5xx / `httpx.HTTPError`（含各类超时与连接错误），默认 `max_retries=1`（共 2 次尝试），退避为 `0.5 * 2^(n-1)`（上限 8 秒）× [1, 1.5] jitter，`sleep` 可注入便于测试；其余 4xx 与 3xx 直接失败。`RetryableLLMError` 继承 `LLMError` 并携带 `status_code` 与 `attempts`，重试耗尽时抛前者。响应解析宽松：`content` 缺失或 `None` 返回空字符串交由 5.4 判定空输出，`choices`/`message` 结构缺失、`content` 非字符串、响应非 JSON 一律 `LLMError`，不会冒泡 `KeyError`；多 choice 取第一个。日志只含 `model` / `status_code` / `elapsed_ms` / `attempt` / `retryable`，不含 api_key 与请求正文。17 条用例覆盖四维超时、请求契约、读超时重试、429 重试成功、400 不重试、503 重试上限、四类宽松解析、非法 JSON、空 messages 与日志脱敏。
- [x] 5.2 人设与请求组装：system = 人设 + 长期摘要；随后按序放历史 user/assistant；当前消息作为独立 user。自定义 `SYSTEM_PROMPT` 生效，未配置时使用默认人设。验证：单测断言「用户文本不出现在 system 字段」「自定义人设出现在请求中」「缺省人设可用」。备注：`wechat_agent/llm/prompt.py` 的 `build_reply_messages` 只做两件事——解析人设回退，把 `user_text` / `summary` / `history` / `max_chars` 原样交给 4.4 的 `build_messages`；未改动 `memory/prompt.py`，其 `persona` 参数已满足自定义人设需求。默认人设复用 `config.DEFAULT_SYSTEM_PROMPT` 作为代码内单一来源（非空、简洁中文人设），`None`、空串、纯空白三类缺省都回落，避免出现第二份默认人设；自定义人设原样传入。system 段仍由 4.4 拼「人设 + 空行 + `以下是你记得的长期信息：` + 摘要」，`summary` 为空或纯空白时不拼占位、只保留人设；用户文本只出现在末位 user，`history` 保持输入时间序，`max_chars` 透传后依旧只裁历史、system 与当前 user 不裁。10 条用例覆盖用户文本不入 system、自定义人设生效、三种缺省回落、摘要拼接、空摘要无占位、预算委托与 `resolve_persona` 回退。
- [x] 5.3 提示注入防护：用户文本中的「忽略以上指令」「系统标签」等内容不得改写 system 结构；回复不泄露人设提示。验证：单测断言请求体结构与 system 内容不变；补充一条需要人工复核的验收用例到 `docs/acceptance.md`。备注：`wechat_agent/llm/guard.py` 不做任何用户输入关键词过滤或正则，防护靠结构：用户文本原样进入独立 user 消息。`INJECTION_SAMPLES` 作为回归样本覆盖五类共 6 条——直接覆盖、角色伪装、分隔符注入、编码绕过（base64 与全角字符两条）、多语言；`INJECTION_CATEGORIES` 供测试断言类别齐全。`verify_structure(messages)` 校验 system 唯一且在首位、角色合法、末条为 user，并对 ≥20 字符的用户文本检查是否出现在任一非 user 消息中（短文本只做整条重复检查），阈值避免「助手」「在吗」这类短句与人设常用词重合误报；5.2 的 `build_reply_messages` 未改动，6 条样本均原样落入末位 user、system 不变。`sanitize_reply(reply, system_prompt, *, fallback=DEFAULT_FALLBACK_REPLY_TEXT)` 用 20 字符滑窗判断回复是否含 system_prompt 的连续片段，命中整条替换为兜底话术（默认复用 `config` 常量，可覆盖），未命中原样返回，19 字符重合不误伤。21 条用例覆盖五类样本结构不变、类别覆盖、结构反例（system 重复/非首位、末条非 user、非法角色与类型、用户文本被拼入 system 或 assistant）、泄露替换、阈值边界、正常回复与自定义兜底。`docs/acceptance.md` 新建，加入注入样本真机人工复核清单项。
- [x] 5.4 降级与输出后处理：超时/失败/空输出 → 兜底话术或静默；输出 strip 与控制字符清理；超过 2000 字符交给分片；处理后为空按失败处理。验证：单测覆盖「超时发兜底」「空输出视失败」「关闭兜底则静默」「超长输出触发分片」四个分支。备注：`wechat_agent/llm/postprocess.py` 提供 `clean_reply` 与 `generate_reply`。`clean_reply` 逐字符剔除零宽字符（`\u200b`-`\u200d`、`\ufeff`）与 `\x00`-`\x1f` 控制字符（保留 `\n`、`\t`），再 `strip`；不做按类别的字符白名单，中文标点与 emoji 原样保留。`generate_reply(complete, *, fallback_enabled=True, fallback_text=DEFAULT_FALLBACK_REPLY_TEXT)` 接收 `client.complete(messages)` 的闭包，只捕获 `LLMError`（`RetryableLLMError` 是其子类；超时与状态码分类在 5.1 已完成，这里不重复判断）；成功时清理后非空才返回文本，失败与清理后为空都走 `_fallback_or_silent`：开关关闭返回 `None`（与 3.4 管线「None 表示静默」一致）并记 `reason` / `silent` 告警，开启时返回 config 的兜底常量；兜底文案被配成空白时同样静默，杜绝空消息。超长分片由 2.6 的 `split_text` 在发送层完成，5.4 只保证不截断；补一条 4000 字用例断言后处理原样返回且 `split_text` 切成多片。日志只含 `reason` / `error_type` / `status_code` / `attempts`，不含回复正文。11 条用例覆盖超时（`RetryableLLMError`）、不可重试 `LLMError`、空与纯空白输出、兜底关闭时失败/空输出两条路径均静默、控制字符清理且标点与 emoji 保留、清理后为空、正常回复、自定义兜底、空白兜底、超长输出交分片、日志不含正文。

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
