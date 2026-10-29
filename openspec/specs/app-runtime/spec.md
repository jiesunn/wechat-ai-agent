# app-runtime Specification

## Purpose
保证服务在 macOS 上可重复启动、配置错误早失败、运行状态可观测、敏感信息不进入代码与日志，并提供安全的联调与停机手段。

## Requirements

### Requirement: 配置只从环境变量与 .env 读取并在启动时校验

系统 SHALL 仅从进程环境变量与 `.env` 读取配置，启动时校验必填项与类型；必填项缺失或类型非法时 MUST 以非零退出码停止，并只输出缺失或非法的键名，不回显键值。

#### Scenario: 缺少必填密钥时启动失败

- **GIVEN** `.env` 未提供 `LLM_API_KEY`
- **WHEN** 服务启动
- **THEN** 进程以非零退出码结束，错误信息只包含键名 `LLM_API_KEY`，不包含任何值

#### Scenario: 配置合法时启动成功

- **GIVEN** 必填配置齐全且类型合法
- **WHEN** 服务启动
- **THEN** 启动成功并输出脱敏后的关键配置摘要

#### Scenario: 白名单显式为空时仍可启动但拒绝全部消息

- **GIVEN** `ALLOW_FROM` 键存在但值为空
- **WHEN** 服务启动并收到消息
- **THEN** 启动成功，但所有消息均被丢弃，不产生回复

### Requirement: 敏感信息不进入仓库与日志

系统 SHALL 保证 `.env` 与运行态数据目录被版本控制忽略，并提供只含键名与说明的示例配置；日志、异常栈与终端输出 MUST NOT 出现 API Key 或 `bot_token` 明文。

#### Scenario: 异常信息中的密钥被脱敏

- **GIVEN** 一次调用过程中抛出包含 API Key 的异常
- **WHEN** 系统写入日志
- **THEN** 日志中的密钥被替换为脱敏形式，完整值不可见

#### Scenario: 仓库不跟踪敏感文件

- **GIVEN** 仓库已完成初始提交
- **WHEN** 检查版本控制状态
- **THEN** `.env` 与 `data/` 均未被跟踪，示例配置只包含键名与说明

### Requirement: 结构化日志与滚动写入

系统 SHALL 输出包含时间、级别、模块、事件与消息标识的结构化日志，默认 `info` 级别，同时写入标准输出与按大小滚动的日志文件；日志 MUST NOT 包含消息正文与密钥。

#### Scenario: 单条消息处理产生可追踪事件

- **GIVEN** 服务收到一条白名单内的文本消息并完成回复
- **WHEN** 查看日志
- **THEN** 至少可看到接收、过滤与回复对应的事件记录，且能通过消息标识串起同一条处理链

#### Scenario: 日志文件按大小滚动

- **GIVEN** 日志文件达到配置的大小上限
- **WHEN** 继续写入新日志
- **THEN** 旧日志被滚动保留，当前文件继续写入，磁盘占用不无限增长

### Requirement: 单实例运行与优雅停机

系统 SHALL 通过数据目录文件锁保证同一数据目录只有一个实例；收到 SIGINT 或 SIGTERM 时 MUST 停止接收新消息、在限时（默认 10 秒）内完成在途处理、落盘游标与记忆后退出。

#### Scenario: 重复启动被拒绝

- **GIVEN** 已有实例持有数据目录锁
- **WHEN** 启动第二个实例
- **THEN** 第二个实例提示已有实例运行并以非零退出码结束

#### Scenario: 收到停机信号后完成在途处理

- **GIVEN** 服务正在处理一条消息
- **WHEN** 收到 SIGTERM
- **THEN** 停止拉取新消息，等待当前消息处理完成，落盘游标与记忆后以退出码 0 结束

#### Scenario: 停机超时后强制结束

- **GIVEN** 在途处理超过 10 秒仍未完成
- **WHEN** 停机限时到达
- **THEN** 强制结束进程并记录未完成事件，已落盘的数据保持完整

### Requirement: DRY_RUN 联调模式

系统 SHALL 提供 `DRY_RUN` 开关：开启时完整执行接收、过滤、记忆与 LLM 流程，但 MUST NOT 调用发送接口，仅以日志记录拟发送内容。

#### Scenario: 开启 DRY_RUN 时不产生真实消息

- **GIVEN** `DRY_RUN=true`
- **WHEN** 白名单内发送者发来一条可回复的消息
- **THEN** 不调用发送接口，日志中出现拟发送内容与目标联系人

#### Scenario: 关闭 DRY_RUN 时正常发送

- **GIVEN** `DRY_RUN=false`
- **WHEN** 白名单内发送者发来一条可回复的消息
- **THEN** 回复通过发送接口真实发出

### Requirement: macOS 运行约束与启动指引

系统 SHALL 仅依赖 macOS 可用的跨平台库，MUST NOT 引入仅 Windows 可用的依赖；README MUST 说明 Python 版本、依赖安装、二维码登录与防休眠建议。

#### Scenario: 依赖中不含 Windows 专用库

- **GIVEN** 项目依赖清单已完成
- **WHEN** 检查依赖项
- **THEN** 不存在 `wxauto`、`pywin32` 等仅 Windows 可用的库

#### Scenario: 按 README 在 macOS 上完成首次启动

- **GIVEN** 一台满足 Python 3.11+ 的 macOS 机器
- **WHEN** 按 README 步骤安装依赖并启动服务
- **THEN** 服务进入二维码登录流程，无缺失依赖或权限错误
