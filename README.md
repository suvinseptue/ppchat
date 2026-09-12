# ppchat

本机微信聊天记录的解密、入库和导出工具。支持 **macOS 微信 4.x** 与 **Windows 微信 4.0**（进程名 `Weixin.exe`）。不支持 Windows 微信 3.x（`WeChat.exe`）。

采集层负责解密，结果写入 `~/.ppchat/ppchat.db`。之后可以按群、按天导出，或走本机只读 HTTP API。汇总 / 需求提取由 agent 读导出结果再写回，编排见 [`SKILL.md`](SKILL.md)。

需要 **Python 3.10+**。命令一律走仓库里的虚拟环境。

| 平台 | 解释器 |
| --- | --- |
| macOS | `./.venv/bin/python` |
| Windows | `ppchat.cmd` 或 `.venv\Scripts\python.exe` |

---

## 安装

### Windows（一键）

1. 把本仓库拷到 Windows（`git clone` 或解压均可）。
2. 双击根目录 **`install.cmd`**。

脚本会：没有 Python 3.10+ 就装 3.12、创建 `.venv`、安装 `requirements.txt`、建立 `%USERPROFILE%\.ppchat` 并写入 `config.json`。

刚装完 Python 若仍提示找不到解释器：关掉窗口，再双击一次 `install.cmd`（刷新 PATH）。脚本可反复跑。

微信数据目录 `db_root` 对不上时，改 `%USERPROFILE%\.ppchat\config.json`：

```json
{"db_root": "D:\\WeChatFiles\\xwechat_files"}
```

指向含 `<账号>\db_storage\` 的那个 `xwechat_files` 目录。

更细的路径表和排错见 [`docs/windows-install.md`](docs/windows-install.md)。

不想双击时：

```bat
cd /d D:\path\to\ppchat
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_windows.ps1
```

### macOS

```bash
cd /path/to/ppchat
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

---

## 一次性：抓解密密钥

密钥按微信账号基本不变，缓存在 `~/.ppchat/keys.json`（Windows 即 `%USERPROFILE%\.ppchat\keys.json`）。只在文件丢失、换账号、或出现新库时重做。

### Windows

1. 打开并登录微信 **4.0**（任务管理器里应是 `Weixin.exe`）。
2. 右键 **`setup-keys.cmd`** → 以管理员身份运行。

杀软可能拦截读进程内存；扫不到时把仓库和 `.venv\Scripts\python.exe` 加入排除。部分 4.1+ 不再明文缓存密钥，本期没有兜底。

### macOS

不要重签 `/Applications/WeChat.app`。只用 `~/.ppchat/extract/` 里的临时副本，抓完即删。终端需要「完全磁盘访问」。

```bash
bash tools/get_keys.sh status    # 正版必须是腾讯签名；若已被 ad-hoc，先从官网覆盖安装
bash tools/get_keys.sh prepare   # 复制正版到 ~/.ppchat/extract/ 并只给副本签名
killall WeChat 2>/dev/null || true
sudo lldb -o "command script import tools/lldb_capture.py" -o "ppc_waitfor"
# 另开一个终端：
open -n "$HOME/.ppchat/extract/WeChat.app"
# 登录并打开几个聊天，待 [+] captured key 停止增长后：
#   (lldb) ppc_dump
#   (lldb) quit
bash tools/get_keys.sh cleanup
./.venv/bin/python tools/build_keymap.py
```

---

## 使用

在仓库根目录。Windows 把下面的 `./.venv/bin/python` 换成 `ppchat.cmd` 即可。

### 入库

解密并写入 `~/.ppchat/ppchat.db`，可反复跑。

```bash
# 某个群（推荐）
./.venv/bin/python -c "from ppchat import parse; print(parse.ingest(chat_wxid='群wxid@chatroom'))"

# 全部群
./.venv/bin/python -c "from ppchat import parse; print(parse.ingest())"
```

### 导出某群某天

```bash
./.venv/bin/python -c "from ppchat import app; b=app.export_day('群名','2026-08-17'); print(b['_bundle_dir'], b['message_count'])"
```

生成 `out/<群名>/<日期>/messages.json`。

### 本地 HTTP API（只读，绑定 127.0.0.1）

```bash
./.venv/bin/python -m ppchat.api_http
```

打开 <http://127.0.0.1:5030/docs>。端点：`/groups`、`/contacts`、`/messages`、`/messages/{id}`、`/health`。

### 汇总与需求

读 `messages.json`，再调用 `app.save_summary` / `app.save_requirements` 写回。输出格式和跨对话分析位点见 [`SKILL.md`](SKILL.md) 与 `ppchat/app.py` 顶部说明。

---

## 路径

| 路径 | 作用 |
| --- | --- |
| `~/.ppchat/` | 工作目录（Windows 为 `%USERPROFILE%\.ppchat\`） |
| `~/.ppchat/keys.json` | 密钥缓存 |
| `~/.ppchat/ppchat.db` | 入库后的归一化库 |
| `~/.ppchat/config.json` | Windows 可写 `db_root` 覆盖微信数据目录 |
| 仓库 `.venv/` | Python 虚拟环境 |
| 仓库 `out/` | 导出的分析包 |

默认微信数据目录：

- macOS：`~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files`
- Windows：`%USERPROFILE%\Documents\xwechat_files`（也可从注册表或 `config.json` 覆盖）
