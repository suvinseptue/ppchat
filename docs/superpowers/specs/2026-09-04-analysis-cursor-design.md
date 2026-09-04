# ppchat — 事件分析消息位点

- 日期: 2026-09-04
- 状态: 设计已确认，待用户审阅
- 前置文档: `2026-08-15-ppchat-wechat-skill-design.md`
- 目标: 每次事件分析结束后记下「处理到哪一条消息」，下次续跑时从该条之后开始

## 1. 背景与目标

现有应用层按「某群某一天」导出消息，再由 Agent 写 `summary` / `requirements`。库里没有「上次这种分析看到哪一条」，换一次 Cursor 对话就接不上。

用户会说「根据最近聊天做需求汇集」，之后再说「从上一次需求分析之后再统计」。系统必须能回答：该群的这种分析，上次处理到哪一条。

### 非目标（YAGNI）

- 不做位点历史版本、自动回退、跨群共用一条指针。
- 不做代码里的种类别名表（「需求分析」vs「需求统计」由 Agent 识别）。
- 不替换现有的按天导出 / 按天产物；增量导出是旁边多一条路。
- 不把位点逻辑放进 Access API（`store.get_messages` 等保持中性）。

## 2. 已确认的语义

- **粒度**：一行 = 一个群 + 一种分析。
- **种类**：开放。第一次记下时用的那串就是 `kind`（例如 `需求分析`）。之后用户换一种说法，由 Agent 对照该群已有种类判断是续跑还是新开；对不上或有歧义时先问用户。
- **无位点**：不扫全量、不默认「今天」。先问用户从哪天或哪条开始。
- **推进**：分析结束把指针推到**这次实际处理的最后一条**。指定了区间就停在区间末尾，不跳到该群当前最新一条。
- **「之后」**：按 `sort_seq` 严格大于位点那条，不含已处理的那条。

## 3. 架构

位点是应用层状态，表建在现有 `~/.ppchat/ppchat.db`，读写只走 `app.py`。

```
parse.ingest(群)
    → app.list_cursors / get_cursor
    →（无位点则问起点）
    → app.export_after(...)
    → Agent 读 bundle、推理、save_requirements / save_summary（现有）
    → app.save_cursor(群, kind, 这次最后一条 id)
```

`store.py` 只负责在 schema 里 `CREATE TABLE IF NOT EXISTS`，不提供 `get_cursor` 这类业务函数。现有 `export_day` 不动。

## 4. 数据模型

表 `analysis_cursors`：

| 字段 | 作用 |
|---|---|
| `chat_wxid` | 群 wxid |
| `kind` | 种类键 = 第一次写入时用的说法 |
| `kind_label` | 最近一次用户/Agent 使用的说法，仅供回显 |
| `last_message_id` | 这次实际处理到的最后一条（`messages.id`） |
| `last_sort_seq` | 用来查「这条之后」 |
| `last_ts` | 便于回显与人工核对 |
| `updated_at` | 写入时间（unix 秒） |

主键：`UNIQUE(chat_wxid, kind)`。覆盖写入，不留历史。

`kind` 不对词、不做 slug。Agent 续跑时必须复用已有行的 `kind`，不能因为用户说了近义词就另写一行。

## 5. API（`app.py`）

四个函数：

- `list_cursors(group=None) -> list[dict]`  
  `group` 可空（全部）或群名/wxid。每项含 `chat_wxid`、群名、`kind`、`kind_label`、`last_message_id`、`last_sort_seq`、`last_ts`、`updated_at`。

- `get_cursor(group, kind) -> dict | None`  
  `kind` 与库里已存值精确匹配。消息已被删除则视为位点失效，返回 `None`（等同没有位点）。

- `save_cursor(group, kind, last_message_id, kind_label=None)`  
  用消息 id 回填 `sort_seq` / `ts`。消息不存在或不属于该群则报错、不写。`kind_label` 缺省则等于 `kind`。按 `(chat_wxid, kind)` 覆盖。

- `export_after(group, after_message_id=None, since=None, until=None) -> dict`  
  增量导出，bundle 形态与 `export_day` 相同（`group` / `messages` / `message_count` / `participants`），另带 `after_message_id` / `since` / `until` 以便核对。下界：同时给了 `after_message_id` 和 `since` 时以 `after_message_id` 为准（`sort_seq` 严格大于该条）。只给 `since` 时从该 unix 秒（含）起。`until` 可选，不含该时刻。两者都空则报错（避免误导出全量）。  
  落盘到 `out/<群>/since-<after_message_id 或 since日期>/messages.json`。

不提供 `resolve_kind`、不提供 `reset_cursor`。要重跑：用户指定新起点再分析，`save_cursor` 覆盖。

群解析复用现有 `resolve_group`。

## 6. Agent 流程（写入 `SKILL.md`）

1. `parse.ingest` 目标群（与现在相同）。
2. 从用户话里解析群；歧义则先问。
3. `list_cursors(group)`，由模型判断此次分析对应哪个已有 `kind`，或应新开。
   - 对不上 / 多个都像：问用户。
   - 续跑：用已有 `kind` 调 `get_cursor`。
   - 新开：用用户这句话里的说法当 `kind`。
4. 无位点（含失效）：问从哪天或哪条开始，再 `export_after(..., since=...)` 或带 `after_message_id`。
5. 有位点：`export_after(group, after_message_id=cursor.last_message_id)`。
6. 导出为空：告知没有新消息，**不改**位点。
7. 读 bundle 做分析，按现有方式写产物（如 `save_requirements`）。
8. 用 bundle **最后一条**的 `id` 调 `save_cursor`。结束时用一句话说清：种类、推到哪条、时间。

用户说法与流程对应：

- 「根据最近聊天做需求汇集」→ 有位点从其后开始；没有则先问起点。
- 「从上一次需求分析之后再统计」→ 对照已有种类，续跑那条指针之后。
- 「从 8 月 20 日开始做事件复盘」→ 新种类 `事件复盘`，从那天 0 点导出到当前（或用户给的终点），分析完记下位点。

## 7. 失败与边界

| 情况 | 处理 |
|---|---|
| 该群该种类没有位点 | 问起点，不扫全量 |
| 导出为空 | 告知没有新内容，不改位点 |
| `last_message_id` 在库里没了 | `get_cursor` 当没有位点，问新起点 |
| 群名对不上 / 对上多个 | 先问，不写位点 |
| 种类语义对不上 / 多个都像 | 先问，不猜着覆盖 |
| `save_cursor` 的消息 id 不属于该群 | 报错，不写 |
| 只分析了指定区间 | 指针停在这次 bundle 最后一条 |

## 8. 测试

对着 `app.py` 的位点/导出写 stdlib 测试（不引入框架）：

1. 同一群两种 `kind` 互不覆盖；再次 `save_cursor` 只更新该行。
2. `export_after` 不含位点那条，只返回其后的消息。
3. 消息 id 不存在或群不匹配时 `save_cursor` 失败；消息删除后 `get_cursor` 视为失效。
4. 导出为空时位点字段不变。

不测 Agent 如何把「需求统计」认到「需求分析」——那是编排，不进单测。

## 9. 验收

- 对一个群做一次需求分析并记下位点后，换一个新对话说「从上一次需求分析之后再统计」，Agent 能 `list_cursors` 对上种类，并从位点下一条开始导出。
- 指定 8 月 21–25 日分析时，位点停在 25 日该窗口最后一条，不跳到「现在」。
- 无位点时 Agent 先问起点，不擅自 `export` 全量。
