# 验收清单

> 真机验收前先完成「环境准备」。每一步都要记录时间与结果；录屏或截图时遵守「脱敏要求」。

## 7.2 真机手工验收

### 环境准备

1. 在项目根目录使用仓库虚拟环境：

   ```bash
   .venv/bin/python -m wechat_agent
   ```

2. 复制配置模板并填写：

   ```bash
   cp .env.example .env
   ```

   `.env` 中必填三项：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。`ALLOW_FROM` 填小号 wxid（逗号分隔、精确匹配），`ILINK_BASE_URL` 保持默认。首次验收先保持 `DRY_RUN=true`。

3. 首次登录并获取小号 wxid：

   - 先运行一次 `DRY_RUN=true .venv/bin/python -m wechat_agent`，用微信扫码，看到终端输出「登录成功」「开始轮询」后按 `Ctrl-C` 停机（应看到「收到停机信号」「进程已停止」）。这一步会把凭证写入 `data/credentials.json`（0600）。
   - 用小号给机器人发一条任意消息。
   - 在项目根目录执行下面的只读脚本，它会复用已存凭证轮询一次并打印发送者标识；打印出的 `from_user_id` 就是小号 wxid（**不要截图或录入视频**）：

     ```bash
     .venv/bin/python - <<'PY'
     import asyncio

     from wechat_agent.config import load_settings
     from wechat_agent.wechat.ilink import ILinkTransport


     async def main() -> None:
         settings = load_settings()
         transport = ILinkTransport(base_url=settings.ilink_base_url, data_dir=settings.data_dir)
         try:
             await transport.login()
             result = await transport.poll(None)
             for message in result.messages:
                 print(f"from_user_id={message.from_user_id}")
         finally:
             await transport.close()


     asyncio.run(main())
     PY
     ```

   - 把打印出的 wxid 填进 `.env` 的 `ALLOW_FROM`，再进入下面的验收步骤。

4. 确认敏感文件不会被提交：

   ```bash
   git check-ignore .env data/memory/x.json
   ```

   预期两行都命中 `.gitignore`。

### 验收结果（2026-10-29 真机）

- [x] 步骤 1 DRY_RUN 预演：通过
- [x] 步骤 2 真实发送：通过
- [x] 步骤 3 图片降级：通过
- [x] 步骤 4 记忆连续性：通过
- [x] 步骤 5 白名单外静默：通过
- [ ] 附加 提示注入人工复核（5.3）：本次未执行，待单独安排
- [ ] 脱敏截图与时间戳：待补
- [ ] 演示视频：未录制，按 `docs/demo-script.md` 后续补录

> 本次确认功能结果；`tasks.md` 中 7.2 的完成勾选还需要脱敏截图与时间戳。

### 验收步骤明细

#### 步骤 1：DRY_RUN 预演

- 操作：保持 `DRY_RUN=true`，启动 `python -m wechat_agent`，扫码登录，用小号发送一条文本，例如「你好，帮我写一句周末计划」。
- 预期终端输出：

  ```
  应用已启动 dry_run=True ...
  正在扫码登录 hint=请用微信扫描终端中的二维码，登录成功前会一直等待
  登录成功
  开始轮询
  [DRY_RUN] 目标=wxid_xxx 内容=<拟发送的回复>
  ```

- 预期微信端：**不收到回复**（DRY_RUN 不调用发送接口）。
- 记录：截图终端 `[DRY_RUN]` 行时遮挡 `目标=` 后的 wxid。

#### 步骤 2：真实发送

- 操作：把 `.env` 改为 `DRY_RUN=false`，重启进程（已有凭证无需重新扫码），小号再发一条文本。
- 预期终端输出：

  ```
  应用已启动 dry_run=False ...
  登录成功
  开始轮询
  LLM 调用成功 model=<模型名> status_code=200 elapsed_ms=<毫秒> attempt=1
  ```

- 预期微信端：收到机器人回复，内容符合人设（默认人设单条不超过 200 字，不使用 Markdown）。
- 记录：手机截图只保留消息气泡，不出现昵称、头像与 wxid。

#### 步骤 3：图片降级

- 操作：小号发送一张图片（可附一句文字，但本步骤只发图片）。
- 预期终端输出：

  ```
  媒体类型未注册处理器，回落默认降级 media_type=image media_size=<字节数>
  媒体消息使用默认降级话术 media_type=image media_size=<字节数>
  ```

  且该消息**不出现** `LLM 调用成功`。
- 预期微信端：收到降级话术，默认是「我暂时看不了图片、语音和文件，发文字给我吧。」；若配置了 `UNSUPPORTED_MEDIA_REPLY`，以配置为准。

#### 步骤 4：记忆连续性

- 操作：两轮对话。
  1. 小号发送「我叫小林，喜欢喝美式」。
  2. 小号发送「你还记得我叫什么、喜欢喝什么吗？」。
- 预期终端输出：两条消息各出现一次 `LLM 调用成功`，无异常日志。
- 预期微信端：第二轮回复能提到「小林」和「美式」。
- 预期磁盘：`data/memory/` 下出现该联系人的 `<hash>.json`，其中 `short_term` 依次包含 user / assistant 记录（**不要在视频里打开该文件**，里面有对话正文）。

#### 步骤 5：白名单外静默

- 操作：用另一个**不在** `ALLOW_FROM` 里的微信账号给机器人发一条文本。
- 预期终端输出：

  ```
  消息被过滤 msg_id=<id> contact_hash=<哈希> stage=whitelist reason=not_in_whitelist
  ```

- 预期微信端：**不收到任何回复**，也没有兜底话术。
- 说明：日志只出现联系人哈希，不出现原始 wxid 与消息正文。

#### 附加：提示注入人工复核（5.3）

- 操作：用 `wechat_agent.llm.guard.INJECTION_SAMPLES` 的五类样本（直接覆盖、角色伪装、分隔符注入、编码绕过、多语言）逐条发送。
- 预期：回复不执行注入指令、不复述人设或长期摘要；若检测到人设片段泄露，应返回兜底话术。
- 记录：每条样本的发送时间、回复摘要与复核结论。

### 脱敏要求

- 二维码：扫描前打码或让二维码只露出一角，任何一帧都不出现完整二维码。
- 手机画面：遮挡联系人昵称、头像、微信号、手机号；只保留消息气泡。
- 终端画面：不打开 `.env`、`data/credentials.json`、`data/memory/*.json`、`data/logs/app.log`；`[DRY_RUN] 目标=wxid_xxx` 行含 wxid，需遮挡。
- API Key：终端与日志不应出现；确认 `grep -r "sk-" data/logs/app.log` 无命中。
- 所有截图与视频文件名带日期，存放位置见 `docs/demo-script.md`。

### 验收记录

| 日期 | 操作人 | 步骤 | 结果 | 备注（脱敏截图路径 / 异常） |
| --- | --- | --- | --- | --- |
| 2026-10-29 |  | 环境准备 | 通过 | 真机确认 |
| 2026-10-29 |  | 步骤 1 DRY_RUN | 通过 | 真机确认；脱敏截图待补 |
| 2026-10-29 |  | 步骤 2 真实发送 | 通过 | 真机确认；脱敏截图待补 |
| 2026-10-29 |  | 步骤 3 图片降级 | 通过 | 真机确认；脱敏截图待补 |
| 2026-10-29 |  | 步骤 4 记忆连续性 | 通过 | 真机确认；脱敏截图待补 |
| 2026-10-29 |  | 步骤 5 白名单外静默 | 通过 | 真机确认；脱敏截图待补 |
|  |  | 附加 注入复核 | 未执行 | 待单独安排 |
|  |  | 演示视频 | 未录制 | 按 `docs/demo-script.md` 后续补录 |

## 提示注入防护（5.3）

- [ ] **注入样本人工复核**：真机运行时用 `wechat_agent.llm.guard.INJECTION_SAMPLES` 的五类样本（直接覆盖、角色伪装、分隔符注入、编码绕过、多语言）逐条发送，确认回复未执行注入指令、未复述人设或长期摘要，拒绝方式自然；记录复核人、时间与脱敏截图。
