# 微信 AI 自动回复 Agent

一个跑在本机的个人微信私聊自动回复机器人。它通过**官方 iLink Bot 通道**接收消息，经过白名单与过滤链、对话记忆、OpenAI 兼容 LLM，把回复发回微信；图片、语音等非文本消息只做降级提示，不下载、不解析。

项目按「可运行、可测试、可观测」的小系统来写：单进程 asyncio、有界队列、每联系人串行、数据全部落在本地。设计文档与任务拆解在 `openspec/changes/wechat-ai-agent/`。

## 能力边界

这些是通道与产品定位决定的边界，不是待修的缺陷：

- **Bot 身份，不是个人号登录态**。iLink 提供的是 Bot 通道；本项目不会登录你的个人微信号，也不能读取通讯录、朋友圈或群成员。
- **只能被动回复私聊**。对方发给这个 Bot 的消息才会被处理；不能主动发起对话，不能加好友，不能进群。
- **单实例、单 Bot**。一个进程对应一个 Bot 账号；同一 `DATA_DIR` 只允许一个实例运行（文件锁）。
- **只回复文本**。图片、语音、文件、视频与未知类型统一走降级话术，不下载、不解密、不调用 LLM。
- **个人号自动化有平台风险**。请遵守微信平台规则，使用小号、白名单、低频使用；本项目不提供任何绕过风控的手段。

## 架构概览

```
微信用户（私聊，被动触发）
        |
        v
iLink Bot 通道（长轮询 getupdates）
        |
        v
接入层 wechat/：归一化 · 去重 · 游标持久化
        |
        v
过滤链 guard/：白名单 -> 形式 -> 长度 -> 重复 -> 多模态
        |                                   |
        | 文本                              | 纯媒体
        v                                   v
记忆 memory/（短期窗口 + 长期摘要）      默认降级话术（不下载、不调 LLM）
        |
        v
LLM llm/：请求组装 -> 注入防护 -> /chat/completions -> 清理/兜底/泄露检测
        |
        v
发送 wechat/：文本分片 -> sendmessage
```

要点：

- 过滤链默认拒绝、逐步短路；非白名单消息在第一步就被丢弃，不消耗模型配额。
- 用户文本永远作为独立的 `user` 消息，不拼进 `system`；`system` 只由人设与长期摘要组成。
- 纯媒体在 LLM 之前分流；文本 + 媒体只把文本交给模型，回复末尾追加「附件暂不支持」。
- 发送成功后才会把 assistant 回复写入记忆；发送失败不会污染下一轮上下文。

## 快速启动

环境要求：**macOS**、**Python 3.11+**。其他平台未验证。

```bash
# 1. 进入项目目录
cd wechat-ai-agent

# 2. 创建虚拟环境并安装依赖
python3 --version          # 确认 >= 3.11
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. 生成配置
cp .env.example .env
```

编辑 `.env`，至少填写下面四项：

```dotenv
LLM_BASE_URL=https://<你的 OpenAI 兼容端点>/v1
LLM_API_KEY=<你的 API Key>
LLM_MODEL=<模型名>
ALLOW_FROM=<小号 wxid>
```

- `ALLOW_FROM` 是逗号分隔的精确匹配白名单；留空会拒绝全部消息（fail-closed）。首次不知道 wxid 时，可以先留空完成登录，再按 `docs/acceptance.md` 的「环境准备」用只读脚本打印发送者标识。
- 首次运行建议加 `DRY_RUN=true`：只打印拟发送内容，不真正发送。
- `ILINK_BASE_URL` 保持默认即可。

```bash
# 4. 启动（首次会提示扫码）
python -m wechat_agent
```

预期终端输出：

```
应用已启动 dry_run=True ...
正在扫码登录 hint=请用微信扫描终端中的二维码，登录成功前会一直等待
请用微信扫描二维码完成登录：
<二维码内容>
登录成功
开始轮询
```

扫码后凭据写入 `data/credentials.json`（0600）；后续启动会复用，不需要重复扫码。扫码登录成功、进入轮询后，按 `Ctrl-C` 会优雅停机，输出「收到停机信号」「进程已停止」。

## 配置说明

完整键位与注释见 [`.env.example`](.env.example)。不要提交 `.env`（已在 `.gitignore` 中）。

| 变量 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `LLM_BASE_URL` | 是 | — | OpenAI 兼容端点，不含 `/chat/completions` |
| `LLM_API_KEY` | 是 | — | 只放在 `.env` |
| `LLM_MODEL` | 是 | — | 模型名 |
| `ALLOW_FROM` | 建议 | 空 | 逗号分隔、精确匹配；空表示拒绝全部 |
| `DRY_RUN` | 否 | `false` | `true` 时只打印拟发送内容 |
| `SYSTEM_PROMPT` | 否 | 内置人设 | 自定义人设；留空用内置简洁中文人设 |
| `FALLBACK_REPLY_ENABLED` | 否 | `true` | LLM 失败/空输出时是否发兜底话术 |
| `FALLBACK_REPLY_TEXT` | 否 | 内置文案 | 兜底话术 |
| `UNSUPPORTED_MEDIA_REPLY` | 否 | 内置文案 | 非文本消息的降级话术 |
| `SHORT_MEMORY_SIZE` | 否 | `20` | 每联系人短期窗口条数 |
| `SUMMARY_TRIGGER_COUNT` | 否 | `10` | 新增多少条触发长期摘要 |
| `SUMMARY_IDLE_MINUTES` | 否 | `10` | 空闲多少分钟触发长期摘要 |
| `MAX_INPUT_CHARS` | 否 | `2000` | 入站文本截断上限 |
| `REPEAT_WINDOW_SECONDS` | 否 | `30` | 重复文本抑制窗口 |
| `DATA_DIR` | 否 | `data` | 运行数据目录（0700） |
| `LOG_DIR` | 否 | `data/logs` | 日志目录 |
| `LOG_LEVEL` | 否 | `info` | `debug` / `info` / `warning` / `error` / `critical` |
| `ILINK_BASE_URL` | 否 | 官方地址 | 测试或灰度时可覆盖 |
| `ILINK_BOT_TYPE` | 否 | `3` | iLink Bot 类型 |

`MAX_OUTPUT_CHARS`、`LLM_TIMEOUT_SECONDS`、`LLM_MAX_RETRIES` 也在 `.env.example` 中。`LLM_TIMEOUT_SECONDS` 控制单次 LLM 读取超时，`LLM_MAX_RETRIES` 控制 429/5xx/网络错误的重试次数；`MAX_OUTPUT_CHARS` 目前是预留项——发送层按 2000 字分片，尚未用它做输出裁剪。

## 使用指南

### DRY_RUN 模式

首次接入或改动人设后，先用 `DRY_RUN=true` 跑一轮：

```
[DRY_RUN] 目标=wxid_xxx 内容=<拟发送的回复>
```

这句话只打印，不调用发送接口。确认内容没问题后改回 `DRY_RUN=false` 重启。注意 `目标=` 后面是 wxid，分享终端输出前要遮挡。

### 白名单

- 逗号分隔、精确匹配，不支持通配符；`ALLOW_FROM=wxid_a,wxid_b`。
- 空值等于全部拒绝，这是有意的 fail-closed 设计。
- 非白名单消息只在日志里留下 `contact_hash`，不会触发 LLM 或发送。

### 多模态降级

- 纯图片/语音/文件/视频/未知类型：回复 `UNSUPPORTED_MEDIA_REPLY`（默认「我暂时看不了图片、语音和文件，发文字给我吧。」），不调用 LLM。
- 文本 + 媒体：只用文本生成回复，末尾追加「（附件暂不支持）」。
- 所有处理器都不下载媒体、不读文件系统、不落盘；日志只记类型和大小。

### 记忆

- 每个联系人保留最近 20 条对话；发送成功才写入 assistant。
- 新增 10 条或空闲 10 分钟触发一次长期摘要；摘要失败不阻塞回复。
- 记忆存于 `data/memory/<联系人哈希>.json`（目录 0700、文件 0600），原始 wxid 不落盘。文件是本地明文 JSON，未加密，请保护机器账户。

### 日志

- stdout 输出人类可读摘要；`data/logs/app.log` 是 JSON 行日志，10MB × 5 滚动。
- 日志不含 API Key、token 与消息正文；联系人只用哈希标识。

## 测试与质量门

```bash
make check                              # ruff check + ruff format --check + pytest
.venv/bin/pytest                        # 全量单元与集成测试
openspec validate wechat-ai-agent       # 校验 OpenSpec 变更（需已安装 openspec CLI）
```

- 单元测试覆盖配置、过滤链、记忆、LLM 客户端、提示注入、输出后处理、多模态路由。
- `tests/integration/` 用 `FakeTransport` 与 `respx` 串起端到端路径，测试内不访问真实网络。
- 真机验收清单见 [`docs/acceptance.md`](docs/acceptance.md)，演示录制脚本见 [`docs/demo-script.md`](docs/demo-script.md)。

## 已知限制

- **iLink 通道限制**：Bot 身份、单 Bot、仅私聊、仅被动回复；不能加群、不能主动发起。服务端可能限流，业务错误不做无限重试。
- **媒体不支持**：不下载、不解密、不理解图片/语音/文件；只回复降级话术。
- **macOS 休眠**：合盖休眠会导致进程挂起或连接中断，唤醒后可能需要一次退避；长时间运行建议 `caffeinate -dimsu -w <pid>`。游标与记忆会落盘，重启可恢复。
- **单实例**：同一 `DATA_DIR` 只允许一个进程；第二个实例会以退出码 1 结束。
- **本地明文记忆**：依赖文件权限（0700/0600）保护，未做加密；共用机器时注意账户隔离。
- **平台风险**：个人号自动化可能触发平台风控；请使用小号、白名单、低频，并自行承担合规责任。

## 技术取舍

- **不用 LangChain / LangGraph**：链路只有一次 `/chat/completions`，自建薄客户端更容易控制超时、重试、错误分类与日志，测试也能用 `respx` 精确断言；框架带来的黑盒和依赖成本大于收益。
- **不用向量库**：个人对话量级很小，20 条短期窗口加一份自然语言摘要已经够用，省掉 embedding、索引与一致性维护。
- **不用 Web 框架和数据库**：单进程 asyncio + 长轮询就够；每联系人一个小 JSON 文件，原子写、易检查、易备份，出问题可以直接看文件。
- **不做媒体下载**：首版只做降级占位，避免 CDN、解密与隐私处理的复杂度；多模态能力等真实需求出现再加。

## 目录结构

```
src/wechat_agent/
  main.py          入口与组合层：读配置、组装模块、扫码登录、轮询、优雅停机
  pipeline.py      有界队列 + 单 worker；每联系人串行，跨联系人并发
  config.py        Settings：环境变量 / .env 校验，缺失时只报键名
  logging.py       JSON 行日志、stdout 摘要、脱敏过滤器
  security.py      密钥掩码
  lock.py          单实例文件锁
  signals.py       SIGINT / SIGTERM 停机编排
  sender.py        发送端口与 DRY_RUN 实现
  wechat/          iLink 适配：HTTP 客户端、归一化模型、游标、去重、分片发送
  guard/           准入：白名单、形式校验、长度截断、重复抑制、过滤链
  memory/          记忆：每联系人 JSON 存储、短期窗口、长期摘要、上下文预算
  llm/             LLM：OpenAI 兼容客户端、请求组装、注入防护、降级与清理
  multimodal/      多模态：处理器注册表、按类型降级话术、混合消息路由
scripts/           运维演练脚本（-14、SIGTERM、重复启动），不参与业务运行
tests/             单元测试与端到端集成测试
docs/              验收清单、演示脚本、提交规范
openspec/          变更提案、设计决策与任务拆解
```

## 文档

- [`docs/acceptance.md`](docs/acceptance.md)：真机验收清单与稳定性演练记录
- [`docs/demo-script.md`](docs/demo-script.md)：演示视频录制脚本
- [`docs/commit-convention.md`](docs/commit-convention.md)：提交规范
- [`openspec/changes/wechat-ai-agent/`](openspec/changes/wechat-ai-agent/)：设计、规格与任务
