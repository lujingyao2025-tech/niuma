# 牛马邮箱 v0.91.0 用户端源码

这是牛马邮箱 Windows 用户端的完整 Python 源码，不包含管理员验证码生成器及管理员私钥。

## 当前主要功能

- 手动录入或从 Excel 导入姓名、地区、邮箱和自定义变量，最多100条。
- 以“活动/批次”组织联系人，每个活动独立管理联系人、模板、窗口和草稿。
- 本地生成邮件标题和正文，不调用外部 AI 接口。
- 支持 MoreLogin、AdsPower Browser、BitBrowser Global。
- 窗口顺序支持动态添加、单项删除和全部清空，最多30个窗口。
- 一次最多处理30条任务，相邻任务启动间隔3秒。
- 自动打开 Gmail Compose，填写并校验 To、Subject 和正文。
- 不自动点击 Send；可在用户手动发送后检测结果并更新任务状态。
- 支持模板库、自定义变量、签名以及按浏览器窗口绑定发件人和模板。
- 支持任务历史、失败记录、数据库备份与恢复。
- 支持中英文界面、深浅色模式和自定义皮肤。
- 默认使用 PySide6 “外贸邮件工作室”界面：56px 功能轨、活动列表、工作区页签和右侧检查器。
- 支持 Ctrl+K 命令面板与键盘优先操作；旧 Tkinter 界面可用 `--legacy-tk` 启动。
- 默认发件人姓名为 Anna Lee，用户可以在设置中修改。
- 首次免费使用3天；支持设备绑定的管理员验证码授权。

## 源码结构

- `app.py`：程序入口。
- `ophelia_assistant/ui.py`：Tkinter 用户界面。
- `ophelia_assistant/studio/`：PySide6 外贸邮件工作室界面。
- `ophelia_assistant/config.py`：设置、模板和窗口顺序规则。
- `ophelia_assistant/trial.py`：3天试用与用户端验证码校验。
- `ophelia_assistant/morelogin.py`：三种浏览器窗口接口。
- `ophelia_assistant/browser.py`：Gmail Compose 自动填写。
- `ophelia_assistant/database.py`：本地任务数据库。
- `ophelia_assistant/workflow.py`：任务生成和草稿流程。
- `assets/`：软件图标、山水背景和公告图片。
- `tests/`：核心规则测试。

## 可复现环境

推荐使用 Windows 10/11 和 Python 3.12。

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe app.py
```

也可以直接双击 `RUN_SOURCE.bat` 创建环境并启动。

`requirements.txt` 记录直接依赖的兼容范围，`requirements-lock.txt` 锁定 v0.91.0 验证过的完整依赖集。源码运行和 EXE 构建脚本默认使用锁定文件，以便重现同一环境。

## 运行测试

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 构建 Windows EXE

双击 `BUILD_WINDOWS_EXE.bat`。构建完成后文件位于：

```text
dist\NiuMaMail-v0.91.0.exe
```

PyInstaller 生成的是免安装 EXE。正式安装包仍可使用 NSIS 或其他 Windows 安装工具封装。

## 安全说明

用户端只包含 Ed25519 公钥，用于验证管理员签发的授权码。管理员私钥、私钥备份和授权码生成器必须与本仓库分离并离线保管；`.gitignore` 已显式排除 `admin_tools/`。
