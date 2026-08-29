"""NiuMaMail per-user installer launcher (compiled with PyInstaller)."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path


APP_NAME = "牛马邮箱"
APP_VERSION = "0.91.0"
EXE_NAME = "NiuMaMail.exe"
UNINSTALL_VBS = "uninstall.vbs"
REG_PATH = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\NiuMaMail"
CREATE_NO_WINDOW = 0x08000000


def _message(text: str, title: str, flags: int = 0x40) -> int:
    try:
        return int(ctypes.windll.user32.MessageBoxW(None, text, title, flags))
    except Exception:
        return 0


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))


def _arg_value(name: str) -> str | None:
    try:
        index = sys.argv.index(name)
        return sys.argv[index + 1]
    except (ValueError, IndexError):
        return None


def _write_log(text: str) -> None:
    log_path = _arg_value("--log")
    if log_path:
        try:
            Path(log_path).write_text(text, encoding="utf-8")
        except OSError:
            pass


def install_dir() -> Path:
    return _local_app_data() / "Programs" / "NiuMaMail"


def _run_powershell(script: str) -> None:
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            creationflags=CREATE_NO_WINDOW,
            timeout=30,
        )
    except Exception:
        pass


def _create_shortcut(lnk: Path, target: Path, description: str) -> None:
    script = (
        "$ws = New-Object -ComObject WScript.Shell;"
        f"$s = $ws.CreateShortcut('{lnk}');"
        f"$s.TargetPath = '{target}';"
        f"$s.WorkingDirectory = '{target.parent}';"
        f"$s.IconLocation = '{target},0';"
        f"$s.Description = '{description}';"
        "$s.Save()"
    )
    _run_powershell(script)


def _write_uninstall_script(install: Path) -> Path:
    vbs = install / UNINSTALL_VBS
    vbs.write_text(
        """Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
On Error Resume Next
ws.RegDelete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\NiuMaMail\\"
desktop = ws.ExpandEnvironmentStrings("%USERPROFILE%") & "\\Desktop\\牛马邮箱.lnk"
startmenu = ws.ExpandEnvironmentStrings("%APPDATA%") & "\\Microsoft\\Windows\\Start Menu\\Programs\\牛马邮箱.lnk"
fso.DeleteFile desktop, True
fso.DeleteFile startmenu, True
install = ws.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\\Programs\\NiuMaMail"
If fso.FolderExists(install) Then
    fso.DeleteFolder install, True
End If
""",
        encoding="utf-8",
    )
    return vbs


def _write_uninstall_registry(install: Path, vbs: Path) -> None:
    script = (
        "New-Item -Path 'HKCU:\\Software\\Microsoft\\Windows\\"
        "CurrentVersion\\Uninstall\\NiuMaMail' -Force | Out-Null;"
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\"
        "CurrentVersion\\Uninstall\\NiuMaMail' -Name DisplayName -Value '牛马邮箱';"
        f"Set-ItemProperty -Path '{REG_PATH}' -Name DisplayVersion -Value '{APP_VERSION}';"
        f"Set-ItemProperty -Path '{REG_PATH}' -Name Publisher -Value 'NiuMaMail';"
        f"Set-ItemProperty -Path '{REG_PATH}' -Name InstallLocation -Value '{install}';"
        f"Set-ItemProperty -Path '{REG_PATH}' -Name DisplayIcon -Value '{install / EXE_NAME},0';"
        f"Set-ItemProperty -Path '{REG_PATH}' -Name UninstallString -Value 'wscript.exe \"{vbs}\"';"
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\"
        "CurrentVersion\\Uninstall\\NiuMaMail' -Name NoModify -Value 1;"
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\"
        "CurrentVersion\\Uninstall\\NiuMaMail' -Name NoRepair -Value 1"
    )
    _run_powershell(script)


def main() -> int:
    try:
        if sys.platform != "win32":
            return 1
        silent = "--silent" in sys.argv
        no_registry = "--no-registry" in sys.argv
        source = _arg_value("--source")
        if source:
            bundled = Path(source)
        else:
            bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
            candidates = list(bundle_dir.glob("NiuMaMail*.exe"))
            bundled = candidates[0] if candidates else bundle_dir / EXE_NAME
        if not bundled.exists():
            raise RuntimeError("安装程序缺少主程序文件，请重新下载。")
        install = install_dir()
        install.mkdir(parents=True, exist_ok=True)
        target = install / EXE_NAME
        shutil.copy2(bundled, target)
        vbs = _write_uninstall_script(install)
        if not no_registry:
            _write_uninstall_registry(install, vbs)

        desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
        start_menu = (
            Path(os.environ.get("APPDATA", Path.home()))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
        )
        desktop_lnk = desktop / f"{APP_NAME}.lnk"
        start_menu_lnk = start_menu / f"{APP_NAME}.lnk"
        desktop_lnk.parent.mkdir(parents=True, exist_ok=True)
        start_menu_lnk.parent.mkdir(parents=True, exist_ok=True)
        _create_shortcut(desktop_lnk, target, APP_NAME)
        _create_shortcut(start_menu_lnk, target, APP_NAME)

        if not silent:
            answer = _message(
                f"牛马邮箱 v{APP_VERSION} 安装完成。\n\n"
                "桌面和开始菜单已创建快捷方式。\n\n是否立即启动？",
                "安装完成",
                0x4 | 0x40,
            )
            if answer == 6:
                try:
                    os.startfile(str(target))
                except OSError:
                    pass
        _write_log("INSTALL_OK")
        return 0
    except Exception as exc:
        _write_log(f"INSTALL_FAIL\n{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        if "--silent" not in sys.argv:
            _message(f"安装失败：{exc}", "安装失败", 0x10)
        return 1


if __name__ == "__main__":
    sys.exit(main())
