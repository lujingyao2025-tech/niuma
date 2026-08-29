# 牛马邮箱 NiuMaMail v0.91.0

牛马邮箱是一款面向外贸邮件发送的 Windows 桌面工具，支持本地生成邮件、通过 MoreLogin / AdsPower / BitBrowser 打开 Gmail Compose 自动填写草稿，并以“活动/批次 + 浏览器窗口绑定”的方式组织发送流程。

本仓库只包含用户端源码，不包含管理员私钥和验证码生成器。

## 主要功能

- 联系人导入：Excel / CSV / 手动录入，按活动批次管理
- 邮件生成：本地模板、自定义变量、签名，不调用外部 AI 接口
- 浏览器窗口：MoreLogin、AdsPower Browser、BitBrowser Global
- 窗口顺序：最多 30 个窗口，支持增删、上下移动、自动填充
- 窗口绑定：按窗口编号保存模板、发件人、锁定状态
- 历史绑定：已关闭窗口的绑定按编号保留，重新打开窗口后自动恢复
- 草稿流程：自动打开 Gmail Compose，校验收件人、主题和正文
- 任务统计：阶段耗时、失败原因、人工确认、数据库备份恢复
- 界面：PySide6 “外贸邮件工作室”，支持中英文、深浅色主题
- 授权：首次 3 天试用，支持设备绑定的管理员验证码

## v0.91.0 稳定性修复

- 窗口顺序和窗口绑定使用独立业务保存，不重写全部设置
- 保存窗口绑定采用合并语义，历史绑定只在“清理无效绑定”时删除
- 设置保存先写临时文件、完成 JSON 校验、再原子替换；失败时磁盘和内存都保持旧值
- 局部保存不会覆盖尚未保存的新 API Key，也不会清除 API Key dirty 状态
- 详情栏展开、宽度、上次页面等 UI 状态独立写入 `ui_state.json`
- 授权状态使用唯一主数据目录，旧数据一次性迁移并写迁移日志
- 授权时间异常时保留原记录、写诊断日志并提示核对，不自动写回
- 设置页新增“数据与授权诊断”，可查看路径、版本、授权来源并导出脱敏诊断
- 自动发送按 Gmail 提示节点判断新发送结果，旧提示残留不再误阻断发送

## 环境要求

- Windows 10 / 11
- Python 3.12
- Gmail 登录态，以及已安装并启动的 MoreLogin / AdsPower / BitBrowser

## 源码运行

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe app.py
```

也可以直接双击 `RUN_SOURCE.bat`。

## 运行测试

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前自动化测试：187 项全部通过，0 失败，0 跳过。

## 构建 EXE

双击 `BUILD_WINDOWS_EXE.bat`，或执行：

```bat
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean NiuMaMail-v0.91.0.spec
```

构建结果：

```text
dist\NiuMaMail-v0.91.0.exe
```

## 本地数据目录

默认数据目录：

```text
%LOCALAPPDATA%\NiuMaMail\
```

主要文件：

- `settings.json`：设置、窗口顺序、窗口绑定
- `ui_state.json`：详情栏等界面状态
- `ophelia.db`：联系人、任务、活动和历史
- `weekly_authorization.dat`：授权状态
- `license_journal.jsonl`：授权变更与异常诊断
- `migrations.jsonl`：数据迁移日志
- `backups/`：设置和数据库备份
- `diagnostics/`：错误报告与执行轨迹

## 源码结构

```text
app.py                           程序入口
ophelia_assistant/config.py      设置存储、窗口顺序、原子写入
ophelia_assistant/trial.py       授权状态、迁移与异常保护
ophelia_assistant/database.py    本地数据库
ophelia_assistant/workflow.py    生成、草稿、发送流程
ophelia_assistant/browser.py     Gmail 自动填写与校验
ophelia_assistant/morelogin.py   浏览器窗口接口
ophelia_assistant/studio/        PySide6 界面
ophelia_assistant/ui.py          Tkinter 旧界面
tests/                           自动化测试
assets/                          图标与界面素材
```

## 安全说明

用户端只包含 Ed25519 公钥，用于验证管理员签发的授权码。管理员私钥、私钥备份和验证码生成器必须与本仓库分离并离线保管；`.gitignore` 已显式排除 `admin_tools/`。
