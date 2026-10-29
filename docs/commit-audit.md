# 提交规范抽查与交付自查（8.3）

- 日期：2026-10-29
- 范围：`git log --all`，共 46 条提交
- 抽查方式：人工逐条查看 message 与 `git show --name-only`，判断 type/scope 是否与改动内容匹配；不是正则批量匹配
- 抽查数量：10 条，覆盖 feat / fix / docs / test / style / refactor / chore 七种 type

## 抽查明细

| # | commit | message | 改动文件 | 判定 |
| --- | --- | --- | --- | --- |
| 1 | `6f7ff5f` | `feat(llm): 实现 OpenAI 兼容客户端与重试` | `src/wechat_agent/llm/__init__.py`、`llm/client.py`、`tests/test_llm_client.py`、`tasks.md` | 通过：scope `llm` 与 `llm/` 模块一致 |
| 2 | `b1fc9f6` | `feat(memory): 实现按联系人清空与隐私检查` | `memory/__init__.py`、`memory/clear.py`、`memory/short_term.py`、`tests/test_memory_clear.py`、`tasks.md` | 通过：scope `memory` 与 `memory/` 模块一致 |
| 3 | `d3678a3` | `fix(runtime): 扫码等待阶段停机不再以异常退出` | `src/wechat_agent/main.py`、`tests/test_main_wiring.py` | 通过：`runtime` 覆盖入口与信号停机 |
| 4 | `adc242f` | `docs: 编写 README 启动与使用指南` | `README.md` | 通过：纯文档提交，按规范省略 scope |
| 5 | `03e82b4` | `test(integration): 添加端到端集成测试` | `tests/integration/test_end_to_end.py`、`src/wechat_agent/multimodal/__init__.py`、`tasks.md` | 偏差：scope `integration` 不在现行列表；且包含 `multimodal/__init__.py` 生产导出 |
| 6 | `8524a29` | `style(test): 修正 test_memory_clear 的列表推导式格式` | `tests/test_memory_clear.py` | 偏差：type `style` 原未定义（本次已补入规范）；scope `test` 不在现行列表 |
| 7 | `0cb4175` | `refactor(gateway): 拆分 WeChatTransport 为接收与发送两个协议` | `wechat/ilink.py`、`wechat/sender.py`、`wechat/transport.py`、`tests/fakes/fake_transport.py`、`tests/test_models.py`、`design.md`、`tasks.md` | 通过：全部改动集中在 gateway 接入层 |
| 8 | `a340c72` | `chore(docs): 添加提交规范文档` | `docs/commit-convention.md`、`tasks.md` | 通过：流程规范类文档，scope `docs` 匹配 |
| 9 | `bb94e7f` | `chore(config): 添加 Makefile 质量门` | `Makefile`、`tasks.md` | 偏差：按规范 `Makefile` 属 `build`，且 scope `config` 不覆盖 Makefile |
| 10 | `4167a52` | `feat(multimodal): 实现处理器注册表与默认降级` | `multimodal/__init__.py`、`multimodal/handlers.py`、`tests/test_multimodal_handlers.py`、`tasks.md` | 通过：scope `multimodal` 与 `multimodal/` 模块一致 |

## 规范问题与处理

本次只核对与记录，**不改写历史**（未执行 rebase / amend / filter-branch）。发现的问题及影响：

1. **`8524a29 style(test)`**：`style` 类型原先未在规范中定义。本次把 `style` 补入 type 列表（仅格式调整、不改变行为）；scope `test` 登记为历史例外。
2. **`03e82b4 test(integration)`**：`integration` scope 不在现行模块列表中；同一提交还包含 `multimodal/__init__.py` 的导出改动（生产文件与测试混提）。影响仅限历史可读性，无功能影响；新提交按被测模块选 scope，测试与生产改动尽量分开。
3. **`bb94e7f chore(config)`**：`Makefile` 按规范应使用 `build` 类型，scope `config` 不覆盖构建配置。影响仅限历史分类，无功能影响。
4. **其他历史 scope**：全历史统计还发现 `docs(openspec)` 2 条、`chore(zcode)` 1 条使用未定义 scope。已在 `docs/commit-convention.md` 的「历史 scope 例外」登记，不再用于新提交。

全历史 subject 形式检查：46 条均符合 `type(scope): subject` / `type: subject`，未发现缺 type 或冒号格式错误。

## 敏感文件全历史扫描

```bash
$ git log --all --name-only --format='' | sed '/^$/d' | sort -u \
    | grep -E '\.env$|(^|/)data/|credentials\.json'
（无输出）

$ git ls-files | grep -E '(^|/)\.env$|(^|/)data/|credentials\.json'
（无输出）
```

结论：`.env`、`data/`、`credentials.json` 从未进入任何一次提交；当前跟踪文件也没有。`.env.example` 是模板，不含真实值。

## 最终交付自查

### make check

```bash
$ make check
.venv/bin/ruff check src/ tests/
All checks passed!
.venv/bin/ruff format --check src/ tests/
68 files already formatted
.venv/bin/pytest
...
============================= 283 passed in 1.01s ==============================
```

### openspec validate

```bash
$ openspec validate wechat-ai-agent
Change 'wechat-ai-agent' is valid
```

### README 快速启动（临时 DATA_DIR + 本地 mock iLink，不真实扫码）

按 README 的「快速启动」执行：复制 `.env.example` 为临时 `.env`，填写 LLM 三项与 `DRY_RUN=true`，把 `ILINK_BASE_URL` 指向本地 mock，启动后在二维码提示处停止。

```text
应用已启动 dry_run=True data_dir=/tmp/readme-check-83/data whitelist_enabled=False
正在扫码登录 hint=请用微信扫描终端中的二维码，登录成功前会一直等待
请用微信扫描二维码完成登录：
https://example.invalid/qr/readme
收到停机信号，正在停止
停机请求期间中断登录或轮询，按正常退出处理 error_type=RuntimeError
进程已停止
app_exit=0
无 Traceback
```

结论：命令可执行，扫码提示与 README 描述一致，停机不再打栈。

### 敏感文件与 README 链接

- `git ls-files` 中无 `.env`、`data/`、`credentials.json`（见上）。
- `git check-ignore .env data/memory/x.json` 两行均命中 `.gitignore`。
- README 本地链接 7 处全部有效：

```text
.env.example -> OK
docs/acceptance.md -> OK
docs/demo-script.md -> OK
docs/acceptance.md -> OK
docs/demo-script.md -> OK
docs/commit-convention.md -> OK
openspec/changes/wechat-ai-agent/ -> OK
```

## 规范同步

- `docs/commit-convention.md`：新增 `style` type；补充「纯文档提交可省略 scope」；登记历史 scope 例外（`openspec` / `zcode` / `test` / `integration`）；修正示例中的 `docs(openspec)`。
- `tasks.md` 提交约定：同步 `style` type，并指向历史 scope 例外。

## 结论

抽查未发现需要修改代码的问题。三处历史偏差与三类历史 scope 已登记，均只影响历史分类的可读性，无功能影响；历史保持原样，后续提交按更新后的规范执行。
