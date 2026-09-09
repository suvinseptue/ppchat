# ppchat Windows 安装与使用

适用：**Windows 10/11 + 微信 4.0**（进程名 `Weixin.exe`）。不支持微信 3.x（`WeChat.exe`）。

环境安装可以一键完成。**抓解密密钥必须单独做一次**：微信要已登录，并且要以管理员读进程内存。这两件事脚本没法替你点。

## 第 1 步：一键安装环境

1. 把本仓库拷到 Windows（`git clone` 或解压均可）。
2. 双击仓库根目录的 `install.cmd`。

脚本会自动：

- 没有 Python 3.10+ 时，用 `winget` 安装 3.12；没有 winget 则下载官网静默安装包
- 在仓库里创建 `.venv` 并 `pip install -r requirements.txt`
- 创建 `%USERPROFILE%\.ppchat\`，并在 `config.json` 写入微信数据目录 `db_root`

| 路径 | 作用 |
| --- | --- |
| `%USERPROFILE%\.ppchat\` | 工作目录 |
| `%USERPROFILE%\.ppchat\keys.json` | 密钥缓存（第 2 步才生成） |
| `%USERPROFILE%\.ppchat\candidates_windows.json` | 内存扫描候选（第 2 步） |
| `%USERPROFILE%\.ppchat\ppchat.db` | 入库后的归一化库 |
| `%USERPROFILE%\.ppchat\config.json` | `db_root` 等本地配置 |
| 仓库 `.venv\` | Python 虚拟环境 |
| 仓库 `out\` | 导出的分析包 |

`db_root` 默认按这个顺序找：`config.json` → 注册表 `HKCU\Software\Tencent\WeChat\FileSavePath` → `%USERPROFILE%\Documents\xwechat_files`。

如果打印了「db_root 还不存在」，用记事本改 `config.json`：

```json
{"db_root": "D:\\WeChatFiles\\xwechat_files"}
```

指向含 `<账号>\db_storage\` 的那个 `xwechat_files` 目录。

装完 Python 后如果仍提示找不到解释器：关掉窗口，**新开**一个资源管理器再双击 `install.cmd`（刷新 PATH）。脚本本身是幂等的，可反复跑。

## 第 2 步：抓密钥（仅此步要手工准备）

先确认：

1. 微信 **4.0** 已启动并登录（任务管理器里是 `Weixin.exe`）
2. 杀软 / EDR 不要拦截 Python 读进程内存（必要时把仓库目录和 `.venv\Scripts\python.exe` 加入排除）

然后任选一种：

- 右键 `setup-keys.cmd` → **以管理员身份运行**
- 或已在管理员终端里：`powershell -NoProfile -ExecutionPolicy Bypass -File tools\setup_keys_windows.ps1`

成功时会写出 `keys.json`，并对每个库打印 `[OK ]`。密钥按微信账号基本永久，丢了 `keys.json`、换账号、或出现新库时才需要重做。

扫不到密钥时：

- 不是管理员
- 微信没登录 / 实际是 3.x
- 杀软拦截 `ReadProcessMemory`
- 部分 4.1+ 不再在内存里明文缓存 `x'<96hex>'`（本期没有兜底）

## 日常使用

以后不用管理员。在仓库根目录：

```bat
ppchat.cmd -c "from ppchat import parse; print(parse.ingest())"
ppchat.cmd -c "from ppchat import parse; print(parse.ingest(chat_wxid='群wxid@chatroom'))"
ppchat.cmd -c "from ppchat import app; b=app.export_day('群名','2026-08-17'); print(b['_bundle_dir'], b['message_count'])"
ppchat.cmd -m ppchat.api_http
```

`ppchat.cmd` 就是 `.venv\Scripts\python.exe` 的包装。导出结果在 `out\<群名>\<日期>\messages.json`。

更完整的编排（汇总、需求、分析位点）见仓库根目录 `SKILL.md`。

## 从终端安装（不要双击时）

```bat
cd /d D:\path\to\ppchat
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_windows.ps1
```

抓密钥仍需管理员 + 已登录的 `Weixin.exe`。
