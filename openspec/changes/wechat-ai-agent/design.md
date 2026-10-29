# Design

## Context

见 `proposal.md — Why`。当前仓库是全新项目（无提交、无既有代码），因此设计需要从零确定技术选型、进程模型与数据布局。

影响设计的客观约束：

- **平台**：目标是在 macOS（Apple Silicon / Intel 均可）上常驻运行，不允许依赖 Windows 专属能力。
- **通道**：个人微信号，官方 iLink Bot API（ClawBot）通道。它是 HTTPS/JSON + 长轮询，不驱动微信客户端，也不需要公网 IP 或回调地址。
- **能力边界**：iLink 只能对「用户先发来的消息」做出回复，发送时必须回填入站消息携带的 `context_token`；没有主动发起会话的接口。
- **技术栈**：Python 3.11+、OpenAI 兼容 LLM 接口、`.env` 配置、短期 `deque` + 长期 LLM 摘要。
- **首版范围**：只做文本闭环；语音 / 图片 / 文件 / 视频只做降级占位。

iLink 侧关键契约：

| 能力 | 接口 | 要点 |
| --- | --- | --- |
| 获取登录二维码 | `GET /ilink/bot/get_bot_qrcode?bot_type=3` | 返回 `qrcode` 与二维码内容 |
| 轮询扫码状态 | `GET /ilink/bot/get_qrcode_status?qrcode=<token>` | `wait` / `scaned` / `confirmed` / `expired`；确认后返回 `bot_token`、`ilink_bot_id`、`ilink_user_id` |
| 接收消息 | `POST /ilink/bot/getupdates` | 请求体带 `get_updates_buf` 游标；服务端最长挂起约 35 秒；响应返回新游标与 `msgs` |
| 发送消息 | `POST /ilink/bot/sendmessage` | 必须带 `context_token`、`message_type=2`（BOT）、`message_state=2`（FINISH）；`item_list` 类型 1=TEXT、2=IMAGE、3=VOICE、4=FILE、5=VIDEO |
| 认证头 | 所有 POST | `AuthorizationType: ilink_bot_token`、`Authorization: Bearer <bot_token>`、`X-WECHAT-UIN: base64(uint32)` |
| 会话过期 | 任意响应 | `errcode = -14` 表示凭据失效，需要重新扫码 |

以上字段来自社区公开实现（如 `zhayujie/CowAgent`、`Liu-zhongxian/wechat-bridge`、`NousResearch/hermes-agent`）与抓包整理，**实现阶段以真实响应为准，全部差异收敛在适配层内**。

## Goals / Non-Goals

**Goals:**

- 打通**文本消息闭环**：接收 → 准入 → 记忆 → LLM → 分片发送，全链路可在本地用假服务端回归。
- 接入层做成**可替换的 Transport**：iLink 的 HTTP 细节（字段、游标、错误码）不泄漏到业务管线，未来可换 Wechaty 或其他通道。
- 每一个外部依赖（iLink、LLM、磁盘）都有**超时、重试与降级**，单点失败不终止主循环。
- 敏感信息只从 `.env` 与运行态凭证文件读取；日志默认脱敏，仓库默认安全。
- 首版即可在 macOS 上稳定跑通并演示，而不是"能登录但回不了消息"。

**Non-Goals:**

- 不做媒体下载、解密、ASR、OCR、文件解析。
- 不做群聊、主动推送、定时任务、多账号、多实例。
- 不引入数据库或向量检索；记忆以本地 JSON 文件为准。
- 不做 Web 管理界面、桌面 App 打包、launchd 守护。
- 不承诺"防封号"，只通过白名单、低频与风险声明控制影响面。

## Decisions

### D1. 微信接入方案：选择官方 iLink Bot API

候选方案与 macOS 可行性对比：

| 方案 | macOS 可行性 | 依赖与成本 | 能力与风险 |
| --- | --- | --- | --- |
| **A. 官方 iLink Bot（ClawBot）** | **原生可用**。纯出站 HTTPS 长轮询，不装微信客户端、不需要 Windows、不需要公网 IP | 免费；无第三方服务；仅 Python HTTP 客户端 | 官方通道；仅被动回复；接口字段与限流由腾讯侧控制 |
| B. Wechaty + `puppet-padlocal` | 可用。iPad 协议跑在云端，gRPC 连过来，与宿主系统无关 | 约 ¥200/月 token；依赖第三方服务可用性 | 能接管个人号、支持群聊；非官方协议，封号风险与供应商依赖都在 |
| C. Wechaty + `puppet-macpro` | 可用但脆弱。需要本机安装并登录 macOS 微信客户端，靠客户端协议通信 | 免费；强依赖客户端版本 | 客户端一升级就可能失效，排障成本高 |
| D. 企业微信「微信客服」/ 公众号客服 | 可用，纯服务端 API | 需企业主体与客服号；免费 | 最合规稳定，但用户面对的是"客服号"而非本人微信好友，无法接管个人号既有会话，与"个人助理"定位不符 |
| E. macOS 客户端 UI 自动化（AppleScript + Accessibility） | 勉强可用 | 需辅助功能 / 屏幕录制权限；无第三方费用 | 只能轮询窗口 UI 模拟收发，慢且极易被版本更新打断，违反客户端使用协议的风险最高 |
| F. 网页版协议（itchat / wechat4u） | 不可用 | — | 2017 年后注册的账号基本无法登录网页版，生态已停更 |
| G. Windows 专属（wxauto / wxpy4 / WeChatFerry） | **不可用** | — | 直接违反平台约束，排除 |

**决定：采用 A（官方 iLink Bot）。**

理由按优先级：

1. **唯一同时满足「官方通道 + macOS 原生 + 零客户端依赖 + 零授权成本」**的方案。B 需要付费且依赖第三方，C 依赖客户端版本，D 改变产品形态，E 太脆，F/G 不可行。
2. **能力边界与首版范围天然吻合**：只做单聊、只做被动回复，正好是 iLink 的设计用途；反过来，这也是需要坦诚说明的限制。
3. **运维面最小**：没有 Node 运行时、没有扫码以外的常驻进程、没有公开端口，macOS 上 `python -m` 即可运行。
4. **可替换**：接入层通过 `WeChatTransport` 接口隔离，若未来需要群聊或商用合规，可切到 B 或 D 而不动业务管线。

选择 A 的代价：失去群聊和主动触达；稳定性押在腾讯侧接口契约上；个人号自动化仍有风控风险。这些写入风险清单与 README。

### D2. 接入层抽象：Transport 接口 + 适配器

- 定义 `WeChatTransport` 接口：`login()`、`poll(cursor)`、`send(message_id, context_token, text)`、`close()`。
- `ILinkTransport` 是唯一实现，负责请求头、`base_info`、字段映射、错误码翻译（`-14` → `SessionExpired`）。
- 上层只处理**归一化消息**：`{msg_id, from_user_id, context_token, parts[], received_at}`。
- 错误统一用 `TransportError`，并携带可选 `errcode`（仅适配层填写；网络、超时与 HTTP 错误留空），供上层区分可重试与不可重试；不定义 `ILinkError` 子类——子类会迫使上层 `except` iLink 专有类型，把通道细节反向泄漏到业务管线。
- 好处：测试用 `FakeTransport` 注入；未来换 B/C/D 只新增适配器；iLink 字段变化收敛在一个文件内。

### D3. 进程模型：单进程 asyncio + 有界队列 + 每联系人串行

- 一个长轮询协程 + 一个有界 `asyncio.Queue`（默认容量 100）+ 单 worker 消费。
- **吞吐让位于顺序**：同一联系人的消息必须按到达顺序处理，否则上下文与回复会错位；单账号场景量小，单 worker 足够。
- 队列满时拒绝新消息并告警（不阻塞轮询、不无限堆积）；这是有意的背压策略。
- 所有外部调用使用 `asyncio.timeout` 包裹，避免协程悬挂拖死停机。

### D4. 记忆：`deque` 短期窗口 + 自然语言长期摘要

- **短期**：每联系人一个 `deque(maxlen=20)`，元素为 `{role, content, ts}`；写入 user 后生成回复，成功发送后写入 assistant。
- **长期**：每联系人一份摘要字符串，触发条件为「新增 ≥10 条」或「空闲 ≥10 分钟」。摘要 prompt 输入旧摘要 + 新增对话，要求输出结构化要点（人物关系、偏好、约定、代办），减少摘要漂移。
- **存储**：`data/memory/<contact_id_hash>.json`，字段含 `short_term`、`summary`、`summary_updated_at`、`summarized_upto`、`pending_since`。写入采用「同目录临时文件 + `fsync` + `os.replace`」，保证崩溃时旧文件仍完整。
- **失败处理**：摘要失败只更新 `pending_since` 并退避重试，不阻塞回复，也不丢短期记录。
- **隔离**：只有白名单消息会写入记忆；清空入口按联系人删除单个文件。
- **为什么不用向量库**：单联系人对话量级很小，摘要 + 最近窗口足够；引入向量检索会增加依赖、隐私面与不可解释性，收益不匹配。

### D5. LLM 调用：直接 HTTP，不用编排框架

- 使用 `httpx.AsyncClient` 调用 `{LLM_BASE_URL}/chat/completions`，不引入 LangChain / LlamaIndex：链路只有一次补全调用，框架带来的黑盒与依赖成本大于收益。
- 请求结构：`system`（人设 + 长期摘要） + 最近 N 轮 `user/assistant` + 当前 `user`。用户文本**永远作为独立 user 内容**，不拼接进 system，降低提示注入成功率。
- 预算控制：按字符数估算（中文约 1 字符 ≈ 1 token，默认预算 8000 字符），由旧到新裁剪短期历史；当前消息与 system 永不裁剪。
- 超时与重试：连接 10 秒、读取 30 秒；429 / 5xx / 网络错误重试 1 次（指数退避 + jitter），4xx 不重试。
- 降级：失败或空输出 → 发送可配置兜底话术；若关闭兜底则静默并告警。**不把异常文本发给用户**。
- 输出只作为文本发送，不解析、不执行其中任何"指令"。

### D6. 过滤链：默认拒绝 + 短路

顺序固定为：**白名单 → 形式校验 → 长度处理 → 重复抑制 → 多模态分流 → LLM**。

- 白名单精确匹配、逗号分隔、去空白去重；**不支持通配符**；空名单等于全部拒绝（fail-closed）。
- 形式校验：空白、纯表情、纯标点直接丢弃。
- 长度：超过 2000 字符截断后继续处理，而不是整条丢弃。
- 重复抑制：`(from_user_id, 归一化文本 hash)` 的 30 秒滑动窗口，内存实现，重启后清空（可接受）。
- 多模态分流在 LLM 之前：非文本消息不会消耗模型调用。
- 丢弃日志只记录 `msg_id`、发送者哈希、阶段、原因，不记正文。

### D7. 多模态占位：统一 Part 模型 + 处理器注册表

数据模型（不依赖 iLink 字段名）：

```
NormalizedMessage
  msg_id / from_user_id / context_token / received_at
  parts: [ { type: text|image|voice|file|video|unknown, text?, media_size? } ]
```

分发规则：

| 输入 | 行为 |
| --- | --- |
| 纯文本 | 正常走 LLM 回复 |
| 纯媒体 | 白名单内回复固定降级话术；白名单外静默丢弃；**不调用 LLM** |
| 文本 + 媒体 | 只取文本生成回复，末尾追加「附件暂不支持」提示 |
| 未知类型 | 归类 unknown，走默认降级处理器，记录告警，不抛异常 |

- 自身消息（`message_type = 2`）在 iLink 适配层归一化时直接过滤，debug 日志只含 `msg_id` 与原因；D7 模型不增加来源类型字段，通道判定属于接入层细节，不进入业务管线。

处理器注册表按类型注册 `MediaHandler`；未注册或处理器抛异常时一律回落到 `DefaultFallbackHandler`。首版**不下载媒体**：不请求 CDN、不做 AES 解密、不落盘，只记录类型与大小。

后续接入思路（不改上层协议）：

1. **图片**：CDN 下载 → AES-128-ECB 解密 → 视觉模型 / OCR → 生成文本描述，转换为 `text` part 后复用现有管线。
2. **语音**：下载 → SILK 转 WAV → ASR → 文本。
3. **文件**：校验大小与扩展名 → 沙箱解析（pdf/docx/txt）→ 截断摘要；文件名脱敏后再进 prompt。
4. **视频**：首帧抽取 + ASR（远期）。
   接入新的处理器只需要实现 `MediaHandler` 并注册，过滤链、记忆、降级逻辑均无需改动。

### D8. 配置、日志与运行保障

配置项（`.env`，全部有类型校验与默认值）：

| 键 | 说明 |
| --- | --- |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容端点、密钥、模型（必填） |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | 默认 30 / 1 |
| `ALLOW_FROM` | 白名单，逗号分隔；**键必须存在**，值为空表示全部拒绝 |
| `SYSTEM_PROMPT` | 人设；未配置时用内置简洁中文人设 |
| `FALLBACK_REPLY_ENABLED` / `FALLBACK_REPLY_TEXT` | 降级开关与话术 |
| `UNSUPPORTED_MEDIA_REPLY` | 非文本消息的降级话术 |
| `SHORT_MEMORY_SIZE` / `SUMMARY_TRIGGER_COUNT` / `SUMMARY_IDLE_MINUTES` | 记忆参数，默认 20 / 10 / 10 |
| `MAX_INPUT_CHARS` / `MAX_OUTPUT_CHARS` | 默认 2000 / 2000 |
| `REPEAT_WINDOW_SECONDS` | 默认 30 |
| `DRY_RUN` | 默认 `false`；开启时不调用发送接口 |
| `DATA_DIR` / `LOG_DIR` / `LOG_LEVEL` | 默认 `data/`、`data/logs/`、`info` |
| `ILINK_BASE_URL` / `ILINK_BOT_TYPE` | 默认官方域名 / `3`，便于测试与灰度 |

- **凭证分层**：人工提供的密钥只在 `.env`；扫码得到的 `bot_token` 是运行态凭证，写入 `data/credentials.json`（`0600`）。不把动态 token 回写 `.env`，避免与模板合并冲突和误提交。两者都必须在 `.gitignore` 内。
- **日志**：结构化 JSON 行写入 `data/logs/app.log`，同时输出人类可读摘要到 stdout；10MB × 5 滚动；统一脱敏过滤器覆盖 `key`、`token`、`authorization` 等字段。
- **单实例**：`fcntl.flock` 锁 `data/.lock`（macOS 原生支持）；拿不到锁直接提示并退出。
- **优雅停机**：SIGINT/SIGTERM → 停止拉取 → 等待在途消息（≤10 秒）→ 落盘游标与记忆 → 退出。
- **数据布局**：

```
data/
  credentials.json     # 0600，gitignore
  cursor.json          # get_updates_buf，原子写
  .lock
  logs/app.log*
  memory/<contact>.json
```

### D9. 测试策略

- 单元测试：过滤链短路顺序、文本分片、记忆窗口淘汰、摘要合并、prompt 组装与裁剪、密钥脱敏、降级分支。
- 集成测试：用 `respx` 模拟 iLink 与 LLM 两个端点，覆盖「扫码 → 轮询 → 过滤 → 回复」「-14 重登」「LLM 超时降级」「图片降级」「重启恢复游标/记忆」。CI 不访问真实网络、不扫码。
- 手工验收：真实扫码 + 真实发消息 + `DRY_RUN` 对照，录屏留档。
- 质量门：`ruff check` + `ruff format --check` + `pytest`，提交前本地跑通。

### D10. 微信版本与平台稳定性

- 因为本方案**不驱动微信客户端、不解析客户端本地库**，微信 macOS 客户端升级不会直接影响服务；稳定性风险从"客户端版本适配"转移到"iLink 服务端契约"。
- 缓解措施：
  1. 协议相关常量（`base_info`、路径、类型映射）集中在一个适配器文件，便于快速修正。
  2. 响应解析**宽松**：忽略未知字段，缺失可选字段使用默认值。
  3. 错误码映射表 + 结构化日志，出现未知 `errcode` 时告警而不是崩溃。
  4. 能力开关：可临时关闭自动回复（退化为只记日志），避免问题扩散。
  5. 接口变更时只改 `ILinkTransport`，业务管线与测试基线保持在归一化消息层。
- **坦诚的限制**：无法承诺 iLink 接口长期不变，也无法保证个人号不被风控限制；不提供客户端 Hook 作为兜底（与平台约束和安全立场冲突）。

### D11. 依赖选型

| 用途 | 选择 | 理由 |
| --- | --- | --- |
| HTTP | `httpx` | 原生 async、超时与重试可控、测试可用 `respx` |
| 配置 | `pydantic-settings` | 类型校验 + `.env` 加载一体，缺失项报错信息干净 |
| 日志 | 标准库 `logging` + JSON formatter | 无额外依赖，滚动与过滤可自控 |
| 测试 | `pytest` + `pytest-asyncio` + `respx` | 贴合 async 代码与 HTTP mock |
| 代码质量 | `ruff` | 单工具完成 lint + format，替代 black/isort/flake8 |

## Risks / Trade-offs

- **[只能被动回复]** → 只在收到消息时发送；不做主动推送功能；文档明确说明。
- **[`context_token` 时效 / 丢失]** → 发送必须使用当条入站消息的 token，跨消息、跨用户一律不复用；缺失时跳过发送并告警。
- **[sendmessage 返回成功但消息未送达（社区报告）]** → 无回执可依赖，用结构化发送日志、频率限制与 `DRY_RUN` 联调减少误判；把该现象写入 README 已知问题。
- **[`errcode = -14` 会话过期]** → 自动清理凭证、进入扫码流程，并输出明确人工指引。
- **[macOS 休眠中断长轮询]** → 游标持久化保证恢复；README 建议插电 + `caffeinate`；不承诺合盖常驻。
- **[个人号风控]** → 白名单、低频、建议小号；不提供绕过风控的手段。
- **[LLM 幻觉与人格漂移]** → 人设 prompt + 结构化摘要 + 低温度；对外声明回复由模型生成，不保证事实准确。
- **[提示注入]** → system/user 严格分离；输出只作文本发送；用户文本不拼接进系统提示。
- **[记忆膨胀]** → 短期窗口固定容量 + 摘要压缩 + 单文件大小上限告警。
- **[密钥泄露]** → `.env` + `.gitignore` + 日志脱敏 + 启动摘要只显示脱敏值；提交前用测试检查仓库未跟踪敏感文件。
- **[原子写失败 / 文件损坏]** → 临时文件 + `fsync` + `os.replace`；损坏文件重命名为 `.corrupt` 后以空记忆启动。
- **[队列积压]** → 有界队列 + 满时拒绝并告警，保证内存不无限增长。
- **[第三方依赖变动]** → 依赖锁定版本；适配层隔离协议差异。

## Migration Plan

本项目为全新实现，无数据迁移与兼容性负担。

- **上线步骤**：填好 `.env` → 安装依赖 → 启动 → 扫码登录 → 用 `DRY_RUN=true` 验证过滤、记忆与拟回复 → 关闭 `DRY_RUN` → 先以本人小号为唯一白名单灰度 → 再逐步加入联系人。
- **回滚方案**：停止进程，恢复 `data/` 备份；无数据库 schema，无归档数据需要清理。
- **配置回滚**：保留上一版 `.env` 副本；所有行为开关（白名单、兜底、DRY_RUN）均可即时调整而无需改代码。

## Open Questions

- iLink 是否提供发送回执或已读状态，目前未见公开接口；首版按"无回执"设计，若后续出现可增加送达校验，不影响现有结构。
- 长期摘要是否需要结构化 JSON（而非自然语言要点）以便程序化引用；首版先用自然语言，观察效果后再决定。
- 是否需要 launchd 常驻与开机自启；首版以终端运行 + `caffeinate` 说明交付，若作为长期个人服务再补。
