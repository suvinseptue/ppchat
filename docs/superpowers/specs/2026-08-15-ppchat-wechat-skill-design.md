# ppchat — 微信聊天记录处理 Skill 设计文档

- 日期: 2026-08-15
- 状态: 设计已确认，待用户审阅
- 目标平台: macOS，WeChat 4.1.11（本机验证）
- 使用范围: 单用户、本机运行，优先"跑通 + 简单"，暂不考虑分发与跨平台

## 1. 背景与目标

在本机 Mac 上处理微信聊天记录，实现两个功能：

1. **聊天内容汇总**：总结某个群「某一天」的聊天内容，主产物是**结构化 JSON**（话题、参与人、待办项），可读的 Markdown 总结为辅。
2. **需求收集与关联**：由 LLM 从当天聊天中**自动识别**需求（无需群成员做特殊标记），并将每条需求**关联到真实的截图/照片**——图片需从微信中**解密提取**为普通图片文件、落地本地、按路径引用。暂不做 OCR / 图片转文字。

核心难点：微信 4.x 本地数据库是加密的，必须先攻克**解密**拿到明文，才能做后续分析。

### 非目标（YAGNI）

- 不做跨平台（Windows/Linux/iOS）——但通过 Provider 接口预留扩展点。
- 不做图片 OCR / 多模态转文字。
- 不做独立可自动化 CLI（cron 每晚跑）——当前用「脚本 + Agent」模式；未来若需要，可在此基础上加壳，工作量不浪费。
- 不做外部推送（飞书/Notion/邮件）。

## 2. 关键约束（来自需求澄清）

- 可以使用 `sudo`（读取进程内存通常需要）。
- 可以要求运行时微信正在登录/运行。
- 密钥希望「取一次、缓存复用」，尽量少折腾。
- **尽量避免关闭 SIP**（研究确认可避免，见 §5）。

## 3. 架构总览（Approach A：脚本做提取，Agent 做分析）

管线拆成职责单一、通过磁盘/DB 交接的模块。确定性的、涉及隐私的步骤全部留在本地脚本；只有「推理」交给 Agent。

分层：

```
[Ingestion 采集层]  MessageProvider（可插拔，当前仅 WeChat4MacProvider）
    key.py → decrypt.py → parse.py   （WeChat 4.x schema 知识只存在于这一层）
        │  normalize 归一化
        ▼
[Storage 存储层]  ~/.ppchat/ppchat.db（我们自有的稳定 schema）  +  磁盘上的 images/
        │
        ▼
[Access API 访问层]  通用数据原语（不含任何业务逻辑）：
    get_groups() / get_contacts() / get_messages(chat_id, since, until, limit, offset)
    / get_message(id) / get_images(message_id)
    （可选：在同一批函数之上再包一层仅绑定 127.0.0.1 的本地 HTTP/FastAPI）
        │
        ▼
[Application 应用层]  构建在 API 之上、由 Agent 编排（业务逻辑在这里，不进 API）：
    • 每日汇总      → 调 get_messages(group, day)，推理，产出 summary.md
    • 需求提取      → 调 get_messages + get_images，推理，产出 requirements.json
```

**设计要点**

- **API 层是"哑"的通用数据边界**，只提供针对单条消息 / 单个聊天的基本操作，不含"需求候选""每日汇总"等个人业务逻辑；这些属于应用层（Agent 编排）。
- **两个解耦缝**：
  1. *Ingestion Provider*——隔离"如何获取原始数据"。将来 WeChat 3.x / Windows / iOS 备份等，作为新 Provider 写入同一存储，消费方不变。
  2. *Query API*——隔离"消费方如何读取"。上层永远不碰微信文件，甚至不需要知道存储 schema。

### 目录结构

```
ppchat/
  SKILL.md                 # 编排：Agent 如何跑管线并分析产物
  README.md
  requirements.txt
  ppchat/                  # python 包
    config.py              # 路径、账号目录发现、常量
    key.py                 # 1. 从运行中的微信提取 SQLCipher 密钥并缓存
    decrypt.py             # 2. 用密钥打开 WeChat 4.x .db（SQLCipher 4，原始 key 模式）
    parse.py               # 3. 读某群某天消息 → 归一化写入 store
    images.py              # 4. 解密 .dat 图片，落地并回填 attachments.local_path
    store.py               # 本地 normalized SQLite 的读写 + Query API 原语
    provider.py            # MessageProvider 接口 + WeChat4MacProvider
    (api_http.py)          # 可选：FastAPI 本地 HTTP 包装
  out/                     # 生成产物（gitignore）
    <group>/<YYYY-MM-DD>/
      messages.json        # 该群该天消息的导出视图（store 的 export）
      images/              # 解密后的图片文件
      summary.md           # Agent 读 messages.json 后写
      requirements.json    # Agent 写（需求 + 关联图片路径）
```

### 端到端数据流

```
WeChat 进程(运行中) ──key.py──> 缓存密钥映射 (~/.ppchat/keys.json, 0600)
                                      │
加密 db_storage/*.db ──decrypt.py(SQLCipher4)──> 直接读取(不落明文副本)
                                      │  normalize
                                      ▼
                          ~/.ppchat/ppchat.db (自有 schema)
                                      │
                        Access API: get_messages / get_images ...
                                      │
                     Agent 读取 → summary.md + requirements.json
                                      │(需求关联的图片按需)
                              images.py 解密 .dat → images/
```

## 4. 本机现状（已探测确认）

- 微信版本 **4.1.11**，进程 `xwechat_mac`，数据目录 `xwechat_files`。
- 账号目录：`~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/q10500484_744d/`。
- `db_storage/` 下 **18 个加密 .db**；聊天正文主要在 `message/message_0.db`（约 30MB，实时更新），联系人在 `contact/contact.db`，全文检索 `message/message_fts.db`。
- `.db` 文件头为随机字节（非 `SQLite format 3`），确认为 SQLCipher 加密。
- `/Applications/WeChat.app` 带 **Hardened Runtime** 标志（`flags=0x10000(runtime)`）——这是内存读取受限的根因（见 §5）。

## 5. 解密模块设计（核心攻坚，全部位于 WeChat4MacProvider）

四个子问题：

### 5.1 权限模型——为何能不关 SIP

常见的"关闭 SIP"是误导：SIP 只保护 Apple 系统进程，微信不是系统进程。真正阻止内存读取的是微信的 **Hardened Runtime**（本机已确认开启）——对启用 Hardened Runtime 的 App，即使 `sudo` 也会 `task_for_pid()` 失败。符合用户约束（可 sudo、不关 SIP）的做法是**一次性 ad-hoc 重签名**去掉 Hardened Runtime：

```bash
killall WeChat        # 必须先退出；运行中的二进制无法被重签名
sudo codesign --force --deep --sign - /Applications/WeChat.app
# 重新打开微信并登录
```

之后 `sudo` + `task_for_pid` 即可工作。需在文档中说明的注意点：

- 执行重签的 Terminal 需要「完全磁盘访问 (Full Disk Access)」。
- 微信**自动更新后签名会被系统还原**，需重新执行此步。
- `key.py` 需**检测** `task_for_pid` 失败并打印上述明确指引，而不是静默报错。

### 5.2 密钥提取（主方案：内存扫描）

WeChat 4.1.x 用 WCDB（SQLCipher 4），采用**每个数据库独立密钥**（非 3.x/4.0 的单一 master key），所以要提取一组密钥。WCDB 在进程内存中缓存的每个 key 形如字面量字符串 `x'<64hex enc_key><32hex salt>'`（96 hex）。`key.py` 通过 Mach VM API（`task_for_pid` → `mach_vm_region` → `mach_vm_read`）扫描可读内存区域匹配该模式。这部分是少量 C/ctypes，其余为 Python。

### 5.3 密钥↔数据库匹配 + 校验

将扫到的候选 key 绑定到具体 DB：校验其 16 字节 salt 是否等于该 `.db` 文件的前 16 字节，再用 SQLCipher 4 的 page-1 HMAC-SHA512 确认：

- `mac_salt = db_salt XOR 0x3a`
- `mac_key = PBKDF2-HMAC-SHA512(key, mac_salt, iterations=2, dklen=32)`
- 校验 page 内容 HMAC 与存储值一致

产物为经校验的**密钥映射** `{db_path: key_hex}`，缓存到 `~/.ppchat/keys.json`（权限 `0600`）。据现场报告，同一账号密钥在重启/更新后**逐字节稳定**——因此确实是「取一次」；仅当某 DB 无缓存密钥（懒加载）时才重新扫描。

### 5.4 读取数据

用原始 key + salt 以 SQLCipher 4 方式（`PRAGMA cipher_compatibility=4`，raw-key 模式）打开每个 DB **直接读取**——**不在磁盘写解密后的 .db 副本**（更好隐私），随后 ETL 到自有的 `~/.ppchat/ppchat.db`。聊天场景主要需要 `message_0.db` 与 `contact.db`。

### 5.5 图片解密（独立子问题）

WeChat 4.x 图片以加密 `.dat` 存储，解码方案为 XOR / AES-V1 / AES-V2，且**图片 AES 密钥同样来自进程内存**（与 DB 密钥不同）。`images.py` 独立处理；若其不稳定，功能 1（汇总）仍可在无图片下工作。

### 5.6 兜底方案

若内存扫描漏取：在 Apple 的 `CCKeyDerivationPBKDF`（微信用系统 CommonCrypto 做派生）设置 LLDB 断点，在派生发生时捕获密钥——跨版本更稳健，作为**文档化备份**，非默认路径。

### 5.7 待实现阶段验证的开放项

- 4.1.11 的 `message_0.db` 具体表/列布局（4.x 各小版本 schema 有差异）——这是 phase 2 第一项编码任务。
- 是否 18 个密钥都需要，还是仅 message/contact 子集。
- 本机 `.dat` 图片属于哪种变体（XOR / V1 / V2）。

## 6. 归一化数据模型（`~/.ppchat/ppchat.db`，stdlib sqlite3）

我们**自有**的稳定 schema，与微信内部表解耦；Provider 负责把微信行映射进来，API 之上只见此 schema。

```
chats            一行一个会话（群或私聊）
  id             INTEGER PK
  wxid           TEXT UNIQUE      -- 群 "12345@chatroom" / 私聊为对方 wxid
  type           TEXT             -- 'group' | 'private'
  name           TEXT             -- 群名 / 联系人显示名

contacts         一行一个人（含群成员）
  id             INTEGER PK
  wxid           TEXT UNIQUE
  display_name   TEXT
  remark         TEXT

messages
  id             INTEGER PK
  chat_id        INTEGER FK -> chats.id
  sender_wxid    TEXT
  sender_id      INTEGER FK -> contacts.id   (解析前可空)
  ts             INTEGER          -- unix 秒（与 chat_id 联合索引）
  type           TEXT             -- 归一枚举: text|image|voice|video|link|file|sticker|system|other
  local_type     INTEGER          -- 原始微信消息类型，保留用于调试
  text           TEXT             -- 明文正文（纯媒体为空）
  svr_id         TEXT             -- 微信服务器消息 id，用于去重
  raw            TEXT             -- 原始 payload/xml，便于后续重解析

attachments      关联到消息的图片/媒体
  id             INTEGER PK
  message_id     INTEGER FK -> messages.id
  kind           TEXT             -- 'image' | 'video' | 'file' | 'voice'
  src_ref        TEXT             -- 微信存储中加密 .dat 的路径
  local_path     TEXT             -- 我们解密后写入 out/.../images/ 的文件（提取前为空）
  sha256         TEXT
  status         TEXT             -- 'pending' | 'extracted' | 'failed'
  error          TEXT

ingest_state     增量 ETL 记账
  source_db      TEXT PK          -- 如 'message_0.db'
  last_svr_id    TEXT             -- 或 last rowid/ts
  updated_at     INTEGER

meta(schema_version)
```

**要点**

- **索引**：`messages(chat_id, ts)`（`get_messages(chat, day)` 热路径）、`messages(svr_id)`（去重）。
- **去重 / 增量**：重复运行幂等——`svr_id` + `ingest_state` 保证只拉新行、不重复。
- **类型归一**：微信整型类型（1=text, 3=image, 34=voice, 43=video, 49=app/link/file, 10000=system …）折叠为可读 `type`，原值存 `local_type`，不丢信息。
- **附件懒加载**：ingest 时建行 `status='pending'` 并填 `src_ref`；真正解密到 `local_path` 在消费方按需触发，全量 ingest 无需解密每张图。

**API 映射**：`get_groups()` = `chats WHERE type='group'`；`get_messages(chat_id, since, until, …)` = 索引范围扫描 join `contacts` 取发送者名；`get_images(message_id)` = `attachments WHERE kind='image'`，首次访问时解密。

**开放项**：微信→`messages` 的**精确字段映射**需在 phase 2 读取解密后的 DB 才能定稿——这是第一项编码任务（见 §5.7、§8）。

## 7. Access API（通用数据原语）

只暴露中性、可复用、稳定的原语，不含业务：

- `get_groups()` / `get_contacts()`
- `get_messages(chat_id, since, until, limit, offset)`
- `get_message(message_id)`
- `get_images(message_id)`

可选：在同一批函数上包一层仅绑 `127.0.0.1` 的本地 HTTP（FastAPI），让 Agent/其他工具按需查询，并把「需 sudo 的解密」与「只读分析」自然分离。

## 8. 验收 / 验证阶段（重要）

进入验证阶段（最终验收）时，必须执行：

- **字段对比任务**：将 WeChat 解密后拿到的**明文字段全集**与我们 §6 的 normalized schema **逐一对比**，产出「缺失/未映射字段清单」（字段名、来源表、样例值、含义猜测），交用户确认是否需要补入。用户在看到清单前无法确定需要哪些字段，因此此步骤是验收前置条件，不可省略。（对应 TODO: field-compare）

## 9. 阶段划分

1. **架构设计（本文档）** — 已完成、待用户审阅。
2. **解密攻坚（phase 2）** — 实现 key.py/decrypt.py，先在本机跑通取密钥 + 打开 `message_0.db`，读出真实表结构。
3. **管线与存储** — store.py / provider.py / parse.py，ETL 到 ppchat.db，落地 Access API。
4. **图片解密** — images.py。
5. **应用层** — SKILL.md 编排 Agent 产出 summary.md + requirements.json。
6. **验收** — 含 §8 字段对比。

## 10. 参考（仅作研究/学习，代码自研）

- WCDB / SQLCipher 4 加密结构与 page HMAC 校验方法。
- 社区工具的 macOS 取密钥思路：内存模式扫描（`x'<96 hex>'`）与 `CCKeyDerivationPBKDF` LLDB 断点两条路线；`task_for_pid` 依赖目标 App 签名而非 SIP 的结论。
