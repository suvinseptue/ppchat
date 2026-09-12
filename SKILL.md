---
name: ppchat
description: 解密并处理本机微信(macOS 4.x / Windows 4.0)聊天记录：把某个群某天的消息汇总成结构化结果，并从中提取需求、关联截图。当用户要“汇总某个微信群某天的聊天”“收集群里的需求”“看看某群昨天聊了什么”时使用。
---

# ppchat — 微信聊天记录处理

本机微信 4.x（macOS）/ 4.0（Windows）聊天记录的解密 + 汇总 + 需求提取工具。分层：采集(解密) → 存储(归一化 SQLite) → 通用 Access API → 应用层(汇总/需求，由 agent 推理)。

约定：所有命令用仓库内的虚拟环境（macOS `./.venv/bin/python`，Windows `ppchat.cmd` / `.venv\Scripts\python.exe`）。

## 一次性准备：抓取解密密钥（很少需要重做）

密钥按微信账号是永久的，已缓存在 `~/.ppchat/keys.json`。**仅在以下情况**需要重新抓取：`keys.json` 丢失、换了微信账号、或出现抓取时未加载的新数据库。

```bash
killall WeChat
sudo lldb -o "command script import tools/lldb_capture.py" -o "ppc_waitfor"
# 启动微信并登录、打开几个聊天；待 [+] captured key 停止增长后：
#   (lldb) ppc_dump
#   (lldb) quit
./.venv/bin/python tools/build_keymap.py      # 生成 ~/.ppchat/keys.json (应 18/18)
```

细节见 `docs/superpowers/specs/2026-08-15-ppchat-wechat-skill-design.md`。

## Windows（微信 4.0）

Windows 微信 4.0（进程 `Weixin.exe`）与 macOS 4.x 共用同一套 SQLCipher 4 解密 / ingest / 存储。差异只在取密钥：以**管理员**运行、微信需**登录中**，扫进程内存即可，**无需**重签名 / 关 SIP / extract 副本。3.x（`WeChat.exe`）不支持。

```bat
install.cmd          # 一键：缺 Python 则安装、建 .venv、装依赖、写 %USERPROFILE%\.ppchat
setup-keys.cmd       # 仅此步需手工：微信已登录 + 右键管理员运行
ppchat.cmd -c "from ppchat import parse; print(parse.ingest())"
```

分步说明、路径表、故障排除见 `docs/windows-install.md`。

手动等价（管理员 PowerShell / cmd，`Weixin.exe` 已登录）：

```bash
python tools/find_keys_windows.py
python tools/build_keymap.py %USERPROFILE%\.ppchat\candidates_windows.json
```

数据目录默认 `%USERPROFILE%\Documents\xwechat_files`；也可在 `%USERPROFILE%\.ppchat\config.json` 写 `"db_root"` 覆盖。杀软 / EDR 可能拦截 `ReadProcessMemory`。4.1+ 若不再缓存 `x'<96hex>'` 字面量，扫描器会改用各库文件头 salt 在内存里抓 ±2KiB 窗口，再 HMAC 校验。

## 日常流程（无需 sudo）

### 1. 入库（解密 + 归一化，可反复运行、幂等）

```bash
# 入某个群（推荐先入目标群）
./.venv/bin/python -c "from ppchat import parse; print(parse.ingest(chat_wxid='43421369381@chatroom'))"
# 或入全部群
./.venv/bin/python -c "from ppchat import parse; print(parse.ingest())"
```

数据落在 `~/.ppchat/ppchat.db`。

### 2. 导出某群某天的分析包

```bash
./.venv/bin/python -c "from ppchat import app; b=app.export_day('富德系统支持','2026-08-17'); print(b['_bundle_dir'], b['message_count'])"
```

生成 `out/<群名>/<日期>/messages.json`。

### 3. 汇总 + 需求提取（agent 推理，Approach A）

读 `out/<群名>/<日期>/messages.json`，据此产出两份结构化结果，并用 app 写回：

- `app.save_summary(group, date, summary_dict)` → 写 `summary.json` + 渲染 `summary.md`
- `app.save_requirements(group, date, requirements_dict)` → 写 `requirements.json`

输出 schema 见 `ppchat/app.py` 顶部 docstring。要点：
- summary：`overview` + `topics[]`(title/detail/participants/message_ids) + `participants[]` + `todos[]`。
- requirements：每条含 `title/detail/raised_by/status/message_ids/images[]`；`images` 关联截图（`message_id` + `src_ref`；`local_path` 需 `images.py` 提取后才有，当前为 null）。

参考产物：`out/富德系统支持/2026-08-17/`。一次性生成脚本示例：`tools/produce_20260817.py`。

### 4. 事件分析位点（跨对话续跑）

位点按「群 + 种类」记在 `~/.ppchat/ppchat.db` 的 `analysis_cursors`。种类名由你（agent）对照已有列表识别，代码不对词、没有别名表。

```bash
./.venv/bin/python -c "from ppchat import app; print(app.list_cursors('富德系统支持'))"
./.venv/bin/python -c "from ppchat import app; print(app.get_cursor('富德系统支持','需求分析'))"
./.venv/bin/python -c "from ppchat import app; b=app.export_after('富德系统支持', after_message_id=123); print(b['_bundle_dir'], b['message_count'])"
./.venv/bin/python -c "from ppchat import app; print(app.save_cursor('富德系统支持','需求分析', 456, kind_label='需求统计'))"
```

编排：

1. `parse.ingest` 目标群（与现在相同）。
2. 从用户话里解析群；对不上或多个匹配则先问，不写位点。
3. `list_cursors(group)`，判断此次分析对应哪个已有 `kind`，或应新开。
   - 对不上 / 多个都像：问用户。
   - 续跑：用**已有行的** `kind` 调 `get_cursor`（不要因为用户说了近义词就另写一行）。
   - 新开：用用户这句话里的说法当 `kind`。
4. 无位点（`get_cursor` 为 `None`，含消息已删导致的失效）：问从哪天或哪条开始。不要 `export_after` 全量，也不要默认「今天」。
5. 有位点：`export_after(group, after_message_id=cursor['last_message_id'])`。
6. 用户指定了日期区间：`export_after(group, since=..., until=...)`（unix 秒；`until` 不含）。
7. `message_count == 0`：告诉用户没有新消息，**不要** `save_cursor`。
8. 读 bundle 做分析，按现有方式写产物（`save_requirements` / `save_summary`）。
9. 用 bundle **最后一条**的 `id` 调 `save_cursor`。结束时用一句话说清：种类、推到哪条消息、时间。

说法对照：

- 「根据最近聊天做需求汇集」→ 有位点从其后开始；没有则先问起点。
- 「从上一次需求分析之后再统计」→ 对照已有种类续跑，复用该行 `kind`。
- 「从 8 月 20 日开始做事件复盘」→ 新种类 `事件复盘`，`since` 为那天 0 点。

## 本地 HTTP API（通用数据原语，只读，绑定 127.0.0.1）

```bash
./.venv/bin/python -m ppchat.api_http      # http://127.0.0.1:5030  (/docs 有交互文档)
```

端点：`/groups`、`/contacts`、`/messages?chat=<wxid>&since=&until=&limit=&offset=`、
`/messages/{id}`、`/messages/{id}/images`、`/health`。此层不含业务逻辑。

## 现状 / 未做

- 分析位点已落地（`app.list_cursors` / `get_cursor` / `save_cursor` / `export_after`）。种类语义匹配由 agent 做，不在代码里。
- 图片仅建了占位 `attachments` 行（`pending`），`.dat` 实体解密（`images.py`）**尚未实现**，故 `local_path=null`。
- 语音转写、appmsg 细分、群内昵称解析：未做。
