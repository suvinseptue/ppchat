---
name: ppchat
description: 解密并处理本机微信(macOS, WeChat 4.x)聊天记录：把某个群某天的消息汇总成结构化结果，并从中提取需求、关联截图。当用户要“汇总某个微信群某天的聊天”“收集群里的需求”“看看某群昨天聊了什么”时使用。
---

# ppchat — 微信聊天记录处理

本机微信 4.x（macOS）聊天记录的解密 + 汇总 + 需求提取工具。分层：采集(解密) → 存储(归一化 SQLite) → 通用 Access API → 应用层(汇总/需求，由 agent 推理)。

约定：所有命令用仓库内的虚拟环境 `./.venv/bin/python`。

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

## 本地 HTTP API（通用数据原语，只读，绑定 127.0.0.1）

```bash
./.venv/bin/python -m ppchat.api_http      # http://127.0.0.1:5030  (/docs 有交互文档)
```

端点：`/groups`、`/contacts`、`/messages?chat=<wxid>&since=&until=&limit=&offset=`、
`/messages/{id}`、`/messages/{id}/images`、`/health`。此层不含业务逻辑。

## 现状 / 未做

- 图片仅建了占位 `attachments` 行（`pending`），`.dat` 实体解密（`images.py`）**尚未实现**，故 `local_path=null`。
- 语音转写、appmsg 细分、群内昵称解析：未做。
