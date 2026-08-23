# ppchat — 拓展到 Windows 平台的技术方案

- 日期: 2026-08-23
- 状态: 设计草案，待用户审阅
- 前置文档: `2026-08-15-ppchat-wechat-skill-design.md`（macOS / WeChat 4.1.11）
- 目标: 让「读取微信聊天记录」的采集能力在 **Windows + 微信 4.0** 上同样可用，复用现有存储 / API / 应用层，不动上层业务。

## 0. TL;DR（先看这段）

**Windows 微信 4.0 与 macOS 微信 4.x 用的是同一套 SQLCipher 4 加密方案**：

| 参数 | macOS 4.x（现状） | Windows 4.0 | 结论 |
| --- | --- | --- | --- |
| 加密算法 | AES-256-CBC | AES-256-CBC | 相同 |
| 完整性 | HMAC-SHA512（64B/页） | HMAC-SHA512 | 相同 |
| page / reserve | 4096 / 80（IV16+HMAC64） | 4096 / 80 | 相同 |
| 密钥模型 | 每库独立 raw key | 每库独立 raw key | 相同 |
| mac_key 派生 | PBKDF2-SHA512(key, salt⊕0x3a, iters=2) | 同 | 相同 |
| 内存里的 key 形态 | `x'<64hex enc_key><32hex salt>'` | 同 | 相同 |
| salt = db 前 16B | 是 | 是 | 相同 |

因此现有 `ppchat/decrypt.py`、`ppchat/keys.py`（候选→校验→keymap 的逻辑）、`ppchat/store.py`、`ppchat/app.py`、`ppchat/api_http.py` **可原样复用**。

真正需要平台分叉的只有三处：

1. **取密钥的机制** —— macOS 用 `task_for_pid` + Mach VM（且受 Hardened Runtime 约束，要对 extract 副本重签名）；Windows 用 `OpenProcess(PROCESS_VM_READ|PROCESS_QUERY_INFORMATION)` + `VirtualQueryEx` + `ReadProcessMemory` 扫 `Weixin.exe`，**以管理员运行即可，无 SIP / 无重签名 / 无 extract 副本**。这一层 Windows 反而更简单。
2. **路径发现** —— 数据目录、进程名、账号目录定位（`config.py`）。
3. **parse 层 schema 校验** —— Windows 4.0 的 `message_0.db` / `contact.db` 表列布局大概率与 macOS 4.x 一致（同为 WCDB 4.0），但需在验收阶段做一次字段对比确认（沿用前置文档 §8 的做法）。

关键改造动作是：**把前置文档里设计过、但当前代码尚未落地的 `provider.py`（MessageProvider）抽象缝真正建出来**，把 macOS 专属逻辑收敛到 `WeChat4MacProvider`，再新增 `WeChat4WindowsProvider`。

## 1. 现状盘点（代码级）

当前 `ppchat/` 已实现、但 **平台耦合点**如下：

- `config.py`：硬编码 macOS —— `WECHAT_APP=/Applications/WeChat.app`、`CONTAINER=~/Library/Containers/com.tencent.xinWeChat/.../xwechat_files`、`EXTRACT_APP`（重签名副本）、`TENCENT_TEAM_ID`。`account_dirs()/default_account_dir()/db_files()` 逻辑本身是平台无关的（只依赖 `CONTAINER/<account>/db_storage/**/*.db` 结构），Windows 4.0 目录结构相同，可复用。
- `wechat_app.py`：**纯 macOS**（codesign / Hardened Runtime / extract 副本守卫）。Windows 完全用不到，属于 macOS Provider 私有。
- `keys.py`：`build_key_map()` 消费的是一个 **平台无关的候选 JSON**（`windows`/`literals`/`keys`/`pairs` 四种候选形态），再用 `decrypt.verify_key` 逐库 HMAC 校验。**这一层可原样复用** —— Windows 只需产出同样格式的候选 JSON。
  - ⚠️ 命名注意：`keys.py` 里的 `"windows"` 指的是「salt 周边的内存窗口候选」，**与操作系统 Windows 无关**，不要混淆。
- `decrypt.py`：纯算法，平台无关，**原样复用**。
- `parse.py`：WeChat 4.x schema 知识（`message_0.db` 的 `Msg_<md5(wxid)>` 表、`Name2Id`、`contact.db` 的 `contact` 表、zstd 解压、类型映射）。这是 **唯一可能需要按 Windows 微调**的业务解析层。
- `store.py` / `app.py` / `api_http.py`：平台无关，**原样复用**。
- `tools/`：`find_keys_macos(.c)`、`get_keys.sh`、`lldb_capture.py` 是 macOS 取密钥工具；`build_keymap.py` 平台无关可复用。

**注意**：前置文档 §3 画的 `provider.py`（MessageProvider 接口 + WeChat4MacProvider）**目前并未落地**——采集流程是 `tools/get_keys.sh` + `keys.py` + `parse.py` 直接串起来的。Windows 拓展的第一步就是补上这个缝。

## 2. 版本分叉：先锁定「Windows 微信 4.0」

Windows 上存在两代不兼容的实现，必须先明确目标：

- **微信 3.x（`WeChat.exe` / `WeChatWin.dll`）**：单一 master key（32B，从 `WeChatWin.dll` 内存特征扫描，常用 `iphone\x00` 锚点回退 0x70 定位）、SQLCipher 参数与 4.0 不同、DB 布局是 `MSG0.db..MSGN.db` / `MicroMsg.db`，与现有 4.x 解析层**完全不同**。
- **微信 4.0（`Weixin.exe`）**：WCDB / SQLCipher 4，**与 macOS 4.x 同构**，目录 `xwechat_files/<account>/db_storage/`。

**建议：本期只做 Windows 微信 4.0**（与现有 4.x 代码天然对齐，改造量最小），把 3.x 作为独立 `WeChat3WindowsProvider` 列入未来扩展点（YAGNI，先不写）。这样既落地快，又不污染 4.x 解析层。

> 4.1.x 的隐患（同前置文档 §5.6）：部分平台从 4.1 起可能不再以明文 `x'..'` 形态缓存 raw key，需要断点抓 passphrase 派生。Windows 4.1.x 目前内存扫描通常仍有效；若失效，走「断点/派生捕获」兜底（Windows 侧对应 `x64dbg` / MinHook 或 Frida 脚本 hook `CCKeyDerivationPBKDF` 的等价函数），但**本期不实现**，仅预留。

## 3. 架构改造：落地 Provider 抽象缝

目标是让「如何拿到某账号的一批明文 DB + 定位聊天/联系人/图片」成为可插拔实现，其余层完全不感知平台。

```
[Ingestion] MessageProvider（可插拔）
    ├─ WeChat4MacProvider     （现有 macOS 逻辑收敛于此）
    └─ WeChat4WindowsProvider （本期新增）
          platform config → key backend → decrypt.py（共享）→ parse schema
                          │ normalize
                          ▼
[Storage]  ~/.ppchat/ppchat.db（共享，schema 不变）
                          ▼
[Access API] store.get_groups/get_messages/get_images（共享，不变）
                          ▼
[Application] app.py + SKILL.md 编排（共享，不变）
```

### 3.1 MessageProvider 接口（新增 `ppchat/provider.py`）

抽象出「平台/版本相关」的最小方法集，其余走共享实现：

```python
class MessageProvider(Protocol):
    name: str                                  # 'wechat4-mac' | 'wechat4-win'

    def account_dirs(self) -> list[Path]: ...          # 发现账号目录
    def db_files(self, account_dir: Path) -> list[Path]: ...
    def acquire_keys(self, account_dir: Path) -> dict[str, str]:
        """返回 {db_path: key_hex}；内部负责取候选→校验→缓存 keys.json。
        平台差异全部封在这里（macOS: 重签副本+Mach VM / lldb；Windows: ReadProcessMemory）。"""
    def ingest(self, chat_wxid: str | None, account_dir: Path) -> dict:
        """解密→归一化→写入 store。可复用现有 parse.ingest，仅 schema 细节按需覆写。"""
```

选择运行时 Provider：按 `sys.platform` + 探测数据目录/进程自动选择，允许环境变量 `PPCHAT_PROVIDER` 覆盖。

### 3.2 config 平台化（改造 `ppchat/config.py`）

把 macOS 常量收敛为「平台 profile」，对外仍暴露 `CONTAINER / db_files / default_account_dir`：

- macOS profile（现状）：`CONTAINER=~/Library/Containers/.../xwechat_files`，`WECHAT_APP/EXTRACT_*/TENCENT_TEAM_ID` 仅 macOS Provider 使用。
- Windows profile（新增）：
  - 数据目录发现优先级：`config.json` 显式配置 → 注册表 `HKCU\Software\Tencent\WeChat` 的 `FileSavePath` / 4.0 对应键 → `%USERPROFILE%\Documents\xwechat_files` → 全盘常见盘符兜底。指向 `<root>/<account>/db_storage`。
  - 进程名：`Weixin.exe`。
  - **无** `WECHAT_APP/EXTRACT_*/TENCENT_TEAM_ID`（Windows 不需要重签名）。
- `~/.ppchat/`（keys.json、ppchat.db、out/）在两平台语义一致（`Path.home()` 跨平台可用）。

`account_dirs()/db_files()` 的现有实现（找含 `db_storage` 的子目录、递归 glob `*.db`）对 Windows 4.0 **同样成立**，只要 `CONTAINER` 指对即可，逻辑基本不用改。

### 3.3 取密钥：Windows 后端（新增 `tools/find_keys_windows.py`）

这是 Windows 侧唯一的「新代码核心」，但比 macOS 简单：

1. **权限**：管理员运行（或提权 `SeDebugPrivilege`）以获得 `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`。无需关任何系统保护、无需重签名、无 extract 副本。
2. **定位进程**：`tasklist` / `CreateToolhelp32Snapshot` 找 `Weixin.exe`（多开取工作集最大的）。要求微信登录/运行中（同 macOS 约束）。
3. **枚举内存**：`VirtualQueryEx` 遍历 `0` → `0x7FFFFFFFFFFF`，保留 `COMMIT` 且可读区域。
4. **扫描候选**：对每个区域 `ReadProcessMemory` 后，正则匹配 `x'([0-9a-fA-F]{96})'`（也兼容 64–192 hex 的宽松匹配），产出 `literals`；或直接产出 salt→enc_key 的 `pairs`。
5. **输出**：写 `~/.ppchat/candidates_windows.json`，**格式与现有 `keys.load_candidates` 完全一致**（`{"windows":[], "literals":[...], "keys":[], "pairs":[...]}`）。

之后 **复用** `tools/build_keymap.py` → `keys.build_key_map()`（逐库 salt 匹配 + `decrypt.verify_key` HMAC 校验）→ 写 `~/.ppchat/keys.json`。**取密钥之后的一切（解密、解析、存储、API）与 macOS 共用同一条代码路径。**

实现选型：纯 `ctypes`（零依赖、与现有 `find_keys_macos.c` 的定位一致）为首选；`pymem` 可作为快速原型。扫描器建议用 Python（`ctypes`）而非 C，Windows 上 `ctypes` 调 kernel32 足够，省一个编译工具链。

### 3.4 parse 层（复用 `ppchat/parse.py`，按需最小覆写）

Windows 4.0 与 macOS 4.x 同为 WCDB 4.0，`message_0.db` 的 `Msg_<md5(wxid)>` 表、`Name2Id`、`contact.db` 的 `contact` 表结构**预期一致**，`parse.ingest` 很可能直接跑通。差异点集中在：

- 列名/大小写、`create_time` 单位、群消息 `sender_wxid:\n` 前缀是否一致；
- zstd 压缩标志、`source` 字段是否同构；
- 附件 `.dat` 路径规则（见 §4）。

处理策略：先直接跑 `parse.ingest`，用验收阶段的**字段对比任务**（见 §6）确认差异；若有差异，用 Provider 子类覆写少量 SQL / 映射，而不是分叉整个 parse。

## 4. 图片 / 附件解密（Windows）

同前置文档 §5.5：微信 4.x 图片以加密 `.dat` 存储（XOR / AES-V1 / AES-V2），**图片 AES 密钥同样来自进程内存**（与 DB key 不同）。Windows 侧对应 `tools/find_image_key_windows.py`（同样 `ReadProcessMemory` 扫描）+ 复用/新增 `images.py` 的 `.dat` 解码（XOR/V1/V2 算法平台无关）。图片路径在 Windows 位于 `xwechat_files/<account>/msg/attach/...` 一类目录，需在验收阶段确认具体规则。图片链路不稳定不影响功能 1（每日汇总）。本期可先做 DB 链路，图片作为 Provider 内的独立子步骤跟进。

## 5. 复用 / 新增清单

| 模块 | 处置 | 说明 |
| --- | --- | --- |
| `decrypt.py` | ✅ 原样复用 | 算法与 Windows 4.0 完全一致 |
| `store.py` / `app.py` / `api_http.py` | ✅ 原样复用 | 平台无关 |
| `keys.py`（build_key_map/校验/缓存） | ✅ 原样复用 | 消费平台无关候选 JSON |
| `tools/build_keymap.py` | ✅ 复用 | 候选→keymap |
| `config.py` | 🔧 改造 | 抽出平台 profile；Windows 路径/进程发现 |
| `parse.py` | 🔧 复用+按需覆写 | 预期一致，差异走 Provider 覆写 |
| `provider.py` | ➕ 新增 | MessageProvider + Mac/Win 两实现（补上设计缝） |
| `tools/find_keys_windows.py` | ➕ 新增 | ctypes 内存扫描产候选 JSON |
| `tools/find_image_key_windows.py` + `images.py` | ➕ 新增 | 图片密钥 + `.dat` 解码（可延后） |
| `wechat_app.py`（codesign 守卫） | ⛔ 仅 macOS | Windows 不涉及 |
| `get_keys.sh` / `lldb_capture.py` / `find_keys_macos.c` | ⛔ 仅 macOS | Windows 不涉及 |

## 6. 验收 / 验证（沿用前置文档 §8）

- **字段对比任务（必做）**：在一台 Windows + 微信 4.0 真机上，解密后把 `message_0.db` / `contact.db` 的明文字段全集与 §6 normalized schema 逐一对比，产出「缺失/未映射字段清单」，确认 Windows 是否引入新字段或语义差异。
- **端到端验收**：取密钥 → 解密 → ingest → `store.get_messages` → `app.export_day` 产出 `messages.json`，与同账号 macOS 侧（若有）交叉核对条数/顺序/发送者名。
- **回归**：确保 macOS 路径不因 Provider 抽象改造而回退（现有 `tests/test_wechat_app.py` 保持通过，新增 Windows 后端的单元测试）。

## 7. 风险与开放项

- **4.1.x 内存 key 形态变化**：若目标机是 4.1.x 且不再明文缓存 `x'..'`，需断点/hook 兜底（Frida / MinHook hook KDF），本期仅预留不实现。
- **Windows 4.0 精确 schema**：`Msg_*` 表列布局需真机确认（§6 字段对比前置条件）。
- **图片 `.dat` 变体与路径**：需真机确认属 XOR/V1/V2 哪种、attach 目录规则。
- **权限/杀软**：`ReadProcessMemory` 可能被 EDR/杀软拦截；需管理员权限，文档需给出提示。
- **多账号/多开**：`Weixin.exe` 多开时按工作集选取，或允许 `--account` 指定。
- **路径发现健壮性**：注册表键在 4.0 可能变化，需多重兜底 + 允许 `config.json` 显式覆盖。

## 8. 阶段划分

1. **抽象缝落地**：新增 `provider.py`，把现有 macOS 逻辑收敛为 `WeChat4MacProvider`，`config.py` 抽平台 profile；保证 macOS 现状零回退（含现有测试）。
2. **Windows 取密钥**：`tools/find_keys_windows.py`（ctypes 内存扫描）→ 复用 `build_keymap` → `keys.json`；在真机验证逐库 HMAC 校验通过。
3. **Windows 采集打通**：`WeChat4WindowsProvider` 复用 `decrypt.py` + `parse.ingest`，ETL 到 `ppchat.db`，跑通 `export_day`。
4. **字段对比验收**（§6）：定稿 Windows→schema 映射，必要处覆写 parse。
5. **图片链路**（可选/延后）：`find_image_key_windows.py` + `images.py` `.dat` 解码。
6. **文档/SKILL**：更新 SKILL.md 编排，加入 Windows 分支的运行前置（管理员、微信登录中）。

## 8.5 真机验证清单（Windows + 微信 4.0，上真机逐条跑）

阶段 1/2/3/5/6 的代码已在 macOS 上落地并通过 48 条单元测试（合成数据/mock）。以下项 **只能在 Windows 真机确认**，代码里都以 `# REAL-MACHINE-VERIFY:` 标注。建议按顺序执行，前一步不过不要往下走。

**准备**
- Windows 10/11 + 微信 4.0，已登录；Python 3.10+；`pip install pycryptodome`。
- 以 **管理员** 运行终端（`ReadProcessMemory` 需要）。

**S1 路径 / 进程发现（config Windows profile）**
1. 确认进程名是 `Weixin.exe`（不是 3.x 的 `WeChat.exe`）。
2. `python -c "import ppchat.config as c; print(c.CONTAINER); print(c.account_dirs())"` —— `CONTAINER` 是否指到真实 `xwechat_files`，`account_dirs()` 是否列出账号目录。
3. 若发现失败：检查注册表 `HKCU\Software\Tencent\WeChat` 的 `FileSavePath` 是否存在、值是 `xwechat_files` 本身还是父目录；必要时在 `~/.ppchat/config.json` 写 `{"db_root": "D:\\...\\xwechat_files"}` 覆盖。
4. 确认账号目录结构为 `CONTAINER/<account>/db_storage/`，且 `db_storage/message/message_0.db` 存在（`default_account_dir` 打分依赖它）。

**S2 取密钥（find_keys_windows.py → build_keymap → keys.json）**
5. `python tools/find_keys_windows.py` —— 是否扫到 ≥1 个 `x'<96hex>'` literal。扫不到 = 该机可能是 4.1+ 不再明文缓存 key（本期无 hook 兜底，需另立子任务）。
6. `python tools/build_keymap.py ~/.ppchat/candidates_windows.json` —— 是否对每个库 `verify_key`（HMAC-SHA512）通过、写出 `~/.ppchat/keys.json`。这一步通过即证明「Windows 4.0 == macOS 4.x 加密」的核心假设成立。
7. 边界健壮性：literal 跨 8MB 读块可能漏扫（低概率）。若第 5 步扫到的数量明显偏少，给 `_iter_process_memory` 的分块加 ~200B 重叠再试。

**S3 采集打通（WeChat4WindowsProvider → parse.ingest → ppchat.db）**
8. `python -c "from ppchat.provider import get_provider; print(get_provider().ingest())"`（或按 SKILL.md 编排）—— 是否成功 ETL。
9. **阶段 4 字段对比（关键验收，不可省略）**：把解密后的 `message_0.db` / `contact.db` 明文字段全集与 `store.py` normalized schema 逐一对比，重点核对：`Msg_<md5(wxid)>` 表列名、`create_time` 单位（秒/毫秒）、群消息 `sender_wxid:\n` 前缀、`Name2Id`、zstd 压缩标志、`contact` 表列名。产出「缺失/未映射字段清单」交我，若有差异用 `WeChat4WindowsProvider` 覆写少量 SQL（不动 `parse.py` 的 macOS 路径）。

**S4 图片链路（可选，images.py + find_image_key_windows.py）**
10. 找一个真实 `.dat`，`python -c "from ppchat.images import detect_variant; print(detect_variant(open(r'...','rb').read()[:16]))"` —— 确认 V1/V2 magic（`07 08 56 31/32 08 07`）与假设一致。
11. 确认 V1/V2 头部布局（15 字节：magic6 + aes_size@6 + xor_size@10 + pad@14）、AES-128-ECB + PKCS7 对齐规则、V1 固定 key `cfcd208495d565ef`、XOR 默认 `0x88`。任一不符则按真机实际调 `images.py` 常量。
12. `python tools/find_image_key_windows.py` —— 扫图片 AES key（假设为 16/32 字节 ASCII，与 DB key 不同）；用真实 V2 密文块试解验证。
13. 确认 attach 目录规则（假设 `xwechat_files/<account>/msg/attach/<md5(user)>/<YYYY-MM>/Img/<md5>[_t|_h].dat`）。

## 9. 参考（仅研究，代码自研）

- WCDB / SQLCipher 4 页结构与 page-1 HMAC-SHA512 校验；Windows 4.0 与 macOS 4.x 同构的社区结论。
- Windows 取密钥思路：`OpenProcess(PROCESS_VM_READ|PROCESS_QUERY_INFORMATION)` + `VirtualQueryEx` + `ReadProcessMemory` 扫 `Weixin.exe`，正则 `x'<96hex>'` → salt 匹配 → HMAC 校验；无需 SIP/重签名（对比 macOS 的 Hardened Runtime 约束）。
- 3.x 单 master key（`WeChatWin.dll` 特征扫描）与 4.0 每库 key 的差异——本期不覆盖。
