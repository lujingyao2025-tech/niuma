# 牛马邮箱 v0.54.0 用户端源码

这是牛马邮箱 Windows 用户端的完整 Python 源码，不包含管理员验证码生成器及管理员私钥。

## 当前主要功能

- 手动录入名字、地区和邮箱，每次最多10条。
- 本地生成邮件标题和正文，不调用外部 AI 接口。
- 支持 MoreLogin、AdsPower Browser、BitBrowser Global。
- 窗口顺序支持动态添加、单项删除和全部清空，最多30个窗口。
- 一次最多处理30条任务，相邻任务启动间隔3秒。
- 自动打开 Gmail Compose，并填写 To、Subject 和正文。
- 不自动点击 Send，必须由用户检查后手动发送。
- 英文邮件模板支持系统变量和5个自定义变量。
- 默认发件人姓名为 Anna Lee，用户可以在设置中修改。
- 首次免费使用3天；支持设备绑定的管理员验证码授权。

## 源码结构

- `app.py`：程序入口。
- `ophelia_assistant/ui.py`：Tkinter 用户界面。
- `ophelia_assistant/config.py`：设置、模板和窗口顺序规则。
- `ophelia_assistant/trial.py`：3天试用与用户端验证码校验。
- `ophelia_assistant/morelogin.py`：三种浏览器窗口接口。
- `ophelia_assistant/browser.py`：Gmail Compose 自动填写。
- `ophelia_assistant/database.py`：本地任务数据库。
- `ophelia_assistant/workflow.py`：任务生成和草稿流程。
- `assets/`：软件图标、山水背景和公告图片。
- `tests/`：核心规则测试。

## 开发环境

推荐使用 Windows 10/11 和 Python 3.12。

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

也可以直接双击 `RUN_SOURCE.bat` 创建环境并启动。

## 运行测试

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 构建 Windows EXE

双击 `BUILD_WINDOWS_EXE.bat`。构建完成后文件位于：

```text
dist\NiuMaMail-v0.54.0.exe
```

PyInstaller 生成的是免安装 EXE。正式安装包仍可使用 NSIS 或其他 Windows 安装工具封装。

## 安全说明

用户端只包含 Ed25519 公钥，用于验证管理员签发的授权码。管理员私钥和管理员验证码生成器不在此源码包内，不能从用户端源码直接生成有效管理员验证码。
