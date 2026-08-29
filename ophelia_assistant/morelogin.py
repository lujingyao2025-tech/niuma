from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests


BROWSER_PROVIDER_NAMES = {
    "morelogin": "MoreLogin",
    "adspower": "AdsPower Browser",
    "bitbrowser": "BitBrowser Global",
}


class BrowserProviderError(RuntimeError):
    pass


# Backward-compatible name for callers of older releases.
MoreLoginError = BrowserProviderError


@dataclass
class BrowserConnection:
    profile_no: int
    cdp_url: str


class BrowserProvider(Protocol):
    display_name: str
    last_error: str

    def start_profile(self, profile_no: int) -> BrowserConnection: ...

    def ping(self) -> bool: ...

    def list_windows(self) -> list[tuple[str, str]]: ...

    def list_running_windows(self) -> list[tuple[str, str]]: ...


_CDP_DIRECT_KEYS = (
    "cdpUrl",
    "wsEndpoint",
    "cdp",
    "webSocketDebuggerUrl",
    "debuggerAddress",
    "debuggingAddress",
    "browserDebuggingAddress",
    "webDriverDebuggingAddress",
    "remoteDebuggingAddress",
    "debuggerUrl",
    "http",
    "ws",
    "devtools",
)
_WS_KEYS = ("puppeteer", "selenium", "webSocketDebuggerUrl", "url", "ws", "http")
_DEBUG_PORT_KEYS = (
    "debugPort",
    "debug_port",
    "port",
    "remote_debugging_port",
    "remoteDebuggingPort",
)


def _cdp_value(row: dict) -> str:
    for key in (
        "cdpUrl",
        "wsEndpoint",
        "webSocketDebuggerUrl",
        "debuggerAddress",
        "debugPort",
        "debug_port",
        "remoteDebuggingPort",
        "remote_debugging_port",
        "cdp",
        "ws",
    ):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _cdp_reachable(value: str, timeout: float = 2.0) -> bool:
    """Lightweight TCP reachability check for a CDP host:port endpoint."""
    import socket

    text = value.strip()
    if text.startswith(("ws://", "wss://", "http://", "https://")):
        from urllib.parse import urlparse

        parsed = urlparse(text)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme in {"wss", "https"} else 80)
    else:
        if text.isdigit():
            host = "127.0.0.1"
            port = int(text)
        else:
            if ":" not in text:
                return False
            host, port_text = text.rsplit(":", 1)
            try:
                port = int(port_text)
            except ValueError:
                return False
            host = host.strip("[]")
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _normalize_cdp(value: object) -> str:
    """Turn provider CDP responses into a URL Playwright can connect to."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("ws://", "wss://", "http://", "https://")):
        return text
    if re.fullmatch(r"(?:[a-zA-Z0-9_.-]+|\[[0-9a-fA-F:]+\]):\d+", text):
        return f"http://{text}"
    return text


def _cdp_from_data(data: object) -> str:
    """Extract the first usable CDP endpoint from common provider payloads."""
    if isinstance(data, list):
        for item in data:
            found = _cdp_from_data(item)
            if found:
                return found
        return ""
    if not isinstance(data, dict):
        return ""
    for key in _CDP_DIRECT_KEYS:
        value = data.get(key)
        if isinstance(value, dict):
            found = _cdp_from_data(value)
            if found:
                return found
        elif isinstance(value, str):
            found = _normalize_cdp(value)
            if found:
                return found
    ws = data.get("ws")
    if isinstance(ws, dict):
        for key in _WS_KEYS:
            found = _normalize_cdp(ws.get(key))
            if found:
                return found
    port = next(
        (
            value
            for key in _DEBUG_PORT_KEYS
            if (value := data.get(key)) not in (None, "")
        ),
        None,
    )
    if port is not None:
        try:
            port_number = int(port)
        except (TypeError, ValueError):
            port_number = 0
        if port_number > 0:
            host = data.get("host") or data.get("ip") or data.get("address") or "127.0.0.1"
            found = _normalize_cdp(f"{host}:{port_number}")
            if found:
                return found
    return ""


class MoreLoginClient:
    display_name = BROWSER_PROVIDER_NAMES["morelogin"]

    def __init__(self, base_url: str = "http://127.0.0.1:40000") -> None:
        self.base_url = base_url.rstrip("/")
        self.last_error = ""

    def start_profile(self, profile_no: int) -> BrowserConnection:
        payloads = (
            {"uniqueId": profile_no},
            {"envId": profile_no},
            {"id": profile_no},
        )
        errors: list[str] = []
        for payload in payloads:
            try:
                response = requests.post(
                    f"{self.base_url}/api/env/start",
                    json=payload,
                    timeout=30,
                )
                response.raise_for_status()
                response_data = response.json()
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"payload={payload}：{exc}")
                continue
            if response_data.get("code") != 0:
                errors.append(
                    f"payload={payload}：{response_data.get('msg') or '启动浏览器失败'}"
                )
                continue
            cdp_url = _cdp_from_data(response_data.get("data"))
            if not cdp_url:
                errors.append(f"payload={payload}：未返回 CDP 连接地址")
                continue
            self.last_error = ""
            return BrowserConnection(profile_no=profile_no, cdp_url=cdp_url)
        self.last_error = errors[-1] if errors else "MoreLogin 启动浏览器失败"
        raise BrowserProviderError(f"无法连接 MoreLogin：{self.last_error}")

    def ping(self) -> bool:
        errors: list[str] = []
        try:
            response = requests.get(f"{self.base_url}/api/env/list", timeout=2)
            payload = response.json()
            if response.ok and payload.get("code") == 0:
                self.last_error = ""
                return True
            errors.append(f"{response.status_code}：{payload.get('msg') or response.text[:120]}")
        except (requests.RequestException, ValueError) as exc:
            errors.append(str(exc))
        try:
            response = requests.post(
                f"{self.base_url}/api/env/page",
                json={"pageNo": 1, "pageSize": 1},
                timeout=2,
            )
            payload = response.json()
            if response.ok and payload.get("code") == 0:
                self.last_error = ""
                return True
            errors.append(f"{response.status_code}：{payload.get('msg') or response.text[:120]}")
        except (requests.RequestException, ValueError) as exc:
            errors.append(str(exc))
        self.last_error = errors[-1] if errors else "MoreLogin 本地接口无响应"
        return False

    def list_windows(self) -> list[tuple[str, str]]:
        windows: list[tuple[str, str]] = []
        try:
            response = requests.get(f"{self.base_url}/api/env/list", timeout=5)
            payload = response.json()
            data = payload.get("data") or {}
            rows = data.get("list") if isinstance(data, dict) else data
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                number = row.get("uniqueId") or row.get("envId") or row.get("id")
                if number is not None:
                    windows.append(
                        (
                            str(number),
                            str(row.get("name") or row.get("envName") or ""),
                        )
                    )
        except (requests.RequestException, ValueError):
            pass
        return windows

    def list_running_windows(
        self, verify_connection: bool = True
    ) -> list[tuple[str, str]]:
        windows: list[tuple[str, str]] = []
        status_keys = ("status", "running", "opened", "is_open", "online")
        running_values = {True, 1, "1", "running", "online", "opened", "open", "active"}
        try:
            response = requests.get(f"{self.base_url}/api/env/list", timeout=5)
            payload = response.json()
            data = payload.get("data") or {}
            rows = data.get("list") if isinstance(data, dict) else data
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                number = row.get("uniqueId") or row.get("envId") or row.get("id")
                if number is None:
                    continue
                if not any(key in row for key in status_keys):
                    continue
                cdp = _cdp_value(row)
                if not cdp or (verify_connection and not _cdp_reachable(cdp)):
                    continue
                status = row.get("status") or row.get("running") or row.get("opened") or row.get("is_open") or row.get("online")
                if status in running_values:
                    windows.append(
                        (
                            str(number),
                            str(row.get("name") or row.get("envName") or ""),
                        )
                    )
        except (requests.RequestException, ValueError):
            pass
        if not windows:
            self.last_error = "MoreLogin 未返回已打开窗口状态，请确认窗口已启动"
        return windows


_API_KEY_NAMES = {
    "apikey",
    "api_key",
    "api-key",
    "apitoken",
    "api_token",
    "api-token",
    "accesstoken",
    "access_token",
    "access-token",
    "userkey",
    "user_key",
    "user-key",
    "securetoken",
    "secure_token",
    "secure-token",
    "authkey",
    "auth_key",
    "auth-key",
    "authorization",
    "token",
    "secret",
    "password",
}
_API_KEY_LINE_RE = re.compile(
    r"(?:api[_-]?key|api[_-]?token|user[_-]?key|access[_-]?token|secure[_-]?token|auth[_-]?key|authorization|token|secret|password)\s*[:=]\s*[\"']?([A-Za-z0-9_\-.]{8,})[\"']?",
    re.I,
)


class AdsPowerClient:
    display_name = BROWSER_PROVIDER_NAMES["adspower"]

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:50325",
        api_key: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.headers = (
            {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        )
        self.last_error = ""
        self._discovered_key = ""
        self._profiles: list[dict] = []

    @staticmethod
    def _local_api_directories() -> list[Path]:
        roots = [
            os.getenv("APPDATA"),
            os.getenv("LOCALAPPDATA"),
            os.getenv("USERPROFILE"),
        ]
        relative_paths = (
            Path("adspower_global/cwd_global/source/local_api"),
            Path("AdsPower Global/cwd_global/source/local_api"),
            Path(".config/adspower_global/cwd_global/source/local_api"),
            Path(".config/AdsPower Global/cwd_global/source/local_api"),
        )
        return [
            Path(root) / relative
            for root in roots
            if root
            for relative in relative_paths
        ]

    @classmethod
    def _local_api_files(cls) -> list[Path]:
        files: list[Path] = []
        for directory in cls._local_api_directories():
            try:
                for path in sorted(directory.iterdir()):
                    if path.is_file():
                        files.append(path)
            except OSError:
                continue
        return files

    @classmethod
    def _local_api_texts(cls) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for path in cls._local_api_files():
            try:
                entries.append(
                    (path.name, path.read_text(encoding="utf-8", errors="ignore"))
                )
            except OSError:
                continue
        return entries

    @staticmethod
    def _parse_urls(text: str) -> list[str]:
        urls = [
            match.rstrip("/\"' ,)}")
            for match in re.findall(r"https?://[^\s\"'<>]+", text or "")
        ]
        urls.extend(
            f"http://{match}"
            for match in re.findall(
                r"(?:localhost|127\.0\.0\.1|local\.adspower\.(?:net|com)):\d+",
                text or "",
                flags=re.IGNORECASE,
            )
        )
        unique: list[str] = []
        for value in urls:
            normalized = str(value or "").strip().rstrip("/")
            if normalized and normalized not in unique:
                unique.append(normalized)
        return unique

    @staticmethod
    def _plausible_key(value: str) -> bool:
        if len(value) < 8:
            return False
        if value.lower() in {"true", "false", "null", "none"}:
            return False
        if re.match(r"^(https?|ws)://", value, re.I):
            return False
        return True

    @staticmethod
    def _first_named_string(value: object, names: set[str]) -> str:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).strip().lower().replace("_", "-")
                if normalized in names and isinstance(item, str):
                    return item.strip()
            for item in value.values():
                found = AdsPowerClient._first_named_string(item, names)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = AdsPowerClient._first_named_string(item, names)
                if found:
                    return found
        return ""

    @classmethod
    def _parse_api_key(cls, text: str) -> str:
        if not text:
            return ""
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            found = cls._first_named_string(parsed, _API_KEY_NAMES)
            if found and cls._plausible_key(found):
                return found
        for match in _API_KEY_LINE_RE.finditer(text):
            candidate = match.group(1).strip().strip("\"'")
            if candidate and cls._plausible_key(candidate):
                return candidate
        return ""

    @classmethod
    def _discover_api_key(cls) -> str:
        for _name, text in cls._local_api_texts():
            found = cls._parse_api_key(text)
            if found:
                return found
        return ""

    def _candidate_urls(self) -> list[str]:
        discovered: list[str] = []
        for _name, text in self._local_api_texts():
            discovered.extend(self._parse_urls(text))
        candidates = [
            *discovered,
            self.base_url,
            "http://local.adspower.net:50325",
            "http://localhost:50325",
            "http://127.0.0.1:50325",
            "http://local.adspower.net:50151",
            "http://localhost:50151",
            "http://127.0.0.1:50151",
        ]
        unique: list[str] = []
        for value in candidates:
            normalized = str(value or "").strip().rstrip("/")
            if normalized and normalized not in unique:
                unique.append(normalized)
        return unique

    def _effective_headers(self) -> dict[str, str]:
        if self.headers:
            return self.headers
        if not self._discovered_key:
            self._discovered_key = self._discover_api_key()
        if self._discovered_key:
            return {"Authorization": f"Bearer {self._discovered_key}"}
        return {}

    def _get(self, base_url: str, path: str, **kwargs):
        headers = dict(self._effective_headers())
        headers.update(kwargs.pop("headers", {}) or {})
        return requests.get(f"{base_url}{path}", headers=headers, **kwargs)

    @staticmethod
    def _reachable(base_url: str) -> bool:
        """Cheap connection probe so unreachable candidates fail in ~1s."""
        try:
            requests.get(base_url, timeout=1)
            return True
        except requests.RequestException:
            return False

    def _load_profiles(self) -> list[dict]:
        errors: list[str] = []
        for base_url in self._candidate_urls():
            base_url = base_url.rstrip("/")
            try:
                response = self._get(
                    base_url,
                    "/api/v1/user/list",
                    params={"page": "1", "page_size": "100"},
                    timeout=8,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"{base_url}：{exc}")
                continue
            if payload.get("code") != 0:
                errors.append(f"{base_url}：{payload.get('msg') or '窗口列表读取失败'}")
                continue
            profiles = payload.get("data", {}).get("list") or []
            if not profiles:
                errors.append(f"{base_url}：AdsPower 当前没有可用窗口")
                continue
            self.base_url = base_url
            self.last_error = ""
            self._profiles = profiles
            return profiles
        self.last_error = errors[-1] if errors else "未发现 AdsPower Local API"
        raise BrowserProviderError(f"无法读取 AdsPower 窗口列表：{self.last_error}")

    @classmethod
    def _params_for_profile(cls, profile: dict) -> list[dict[str, str]]:
        params: list[dict[str, str]] = []
        serial = str(profile.get("serial_number") or "").strip()
        user_id = str(profile.get("user_id") or "").strip()
        if serial:
            params.append({"serial_number": serial})
        if user_id:
            params.append({"user_id": user_id})
        return params

    @classmethod
    def _resolve_profile_params(
        cls, profiles: list[dict], profile_no: int
    ) -> tuple[list[dict[str, str]] | None, str]:
        """Map a user-entered window number to AdsPower start parameters."""
        needle = str(profile_no)
        for profile in profiles:
            serial = str(profile.get("serial_number") or "").strip()
            user_id = str(profile.get("user_id") or "").strip()
            if serial == needle or user_id == needle:
                return cls._params_for_profile(profile), ""
        if 1 <= profile_no <= len(profiles):
            return cls._params_for_profile(profiles[profile_no - 1]), ""
        available = "、".join(
            str(profile.get("serial_number") or profile.get("user_id") or "?")
            for profile in profiles[:20]
        )
        return None, f"窗口序号 {profile_no} 不存在；当前 AdsPower 可用序号：{available}"

    def start_profile(self, profile_no: int) -> BrowserConnection:
        profiles: list[dict] = []
        try:
            profiles = self._profiles or self._load_profiles()
        except BrowserProviderError:
            # Fall back to a direct attempt in case the list endpoint is blocked.
            profiles = []
        param_sets, resolve_error = self._resolve_profile_params(profiles, profile_no)
        if param_sets is None and resolve_error:
            self.last_error = resolve_error
            raise BrowserProviderError(f"无法启动 AdsPower 窗口：{resolve_error}")
        if not param_sets:
            param_sets = [
                {"serial_number": str(profile_no)},
                {"user_id": str(profile_no)},
            ]
        errors: list[str] = []
        for base_url in self._candidate_urls():
            base_url = base_url.rstrip("/")
            if not self._reachable(base_url):
                errors.append(f"{base_url}：本地接口未响应")
                continue
            for params in param_sets:
                if not params:
                    continue
                payload = None
                try:
                    response = self._get(
                        base_url,
                        "/api/v1/browser/start",
                        params={**params, "ip_tab": "0"},
                        timeout=20,
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (requests.RequestException, ValueError):
                    # Newer AdsPower builds also accept a POST JSON request.
                    try:
                        response = requests.post(
                            f"{base_url}/api/v1/browser/start",
                            json={**params, "ip_tab": "0"},
                            headers=self._effective_headers(),
                            timeout=20,
                        )
                        response.raise_for_status()
                        payload = response.json()
                    except (requests.RequestException, ValueError) as exc:
                        errors.append(f"{base_url} params={params}：{exc}")
                        continue
                if payload.get("code") != 0:
                    errors.append(
                        f"{base_url} params={params}：{payload.get('msg') or '启动窗口失败'}"
                    )
                    continue
                cdp_url = _cdp_from_data(payload.get("data"))
                if not cdp_url:
                    errors.append(f"{base_url} params={params}：未返回 CDP 连接地址")
                    continue
                self.base_url = base_url
                self.last_error = ""
                return BrowserConnection(profile_no=profile_no, cdp_url=cdp_url)
        self.last_error = errors[-1] if errors else "未发现 AdsPower Local API"
        raise BrowserProviderError(
            "无法连接 AdsPower Browser。请在 AdsPower 的“自动化 → API”中确认接口状态正常；"
            + (
                "如已开启安全校验，请在设置中填写 API Key。"
                if not self.api_key
                else "请检查 API 权限或窗口序号。"
            )
            + f"\n详细信息：{self.last_error}"
        )

    def ping(self) -> bool:
        headers = self._effective_headers()
        health_paths = (
            "/status",
            "/api/v1/browser/local-active",
            "/api/v1/group/list?page=1&page_size=1",
            "/api/v1/user/list?page=1&page_size=1",
        )
        errors: list[str] = []
        for base_url in self._candidate_urls():
            base_url = base_url.rstrip("/")
            if not self._reachable(base_url):
                errors.append(f"{base_url}：本地接口未响应")
                continue
            for path in health_paths:
                try:
                    response = requests.get(
                        f"{base_url}{path}",
                        headers=headers,
                        timeout=2,
                    )
                    payload = response.json()
                except requests.RequestException as exc:
                    errors.append(f"{base_url}：{exc}")
                    break
                except ValueError as exc:
                    errors.append(f"{base_url}{path}：返回内容不是有效 JSON（{exc}）")
                    continue
                if response.ok and payload.get("code") == 0:
                    self.base_url = base_url
                    self.last_error = ""
                    return True
                message = payload.get("msg") or f"HTTP {response.status_code}"
                errors.append(f"{base_url}{path}：{message}")
                if response.status_code in {401, 403}:
                    break
        self.last_error = errors[-1] if errors else "未发现 AdsPower Local API"
        if not self.api_key and not self._discovered_key:
            self.last_error += "；若 AdsPower 已开启安全校验，请填写 API Key"
        return False

    def list_windows(self) -> list[tuple[str, str]]:
        windows: list[tuple[str, str]] = []
        try:
            profiles = self._profiles or self._load_profiles()
        except BrowserProviderError:
            profiles = []
        for profile in profiles:
            number = profile.get("serial_number") or profile.get("user_id")
            if number:
                windows.append((str(number), str(profile.get("name") or "")))
        return windows

    def list_running_windows(
        self, verify_connection: bool = True
    ) -> list[tuple[str, str]]:
        windows: list[tuple[str, str]] = []
        running_values = {True, 1, "1", "running", "online", "opened", "open", "active", "Active"}
        try:
            profiles = self._profiles or self._load_profiles()
        except BrowserProviderError:
            profiles = []
        for profile in profiles:
            number = profile.get("serial_number") or profile.get("user_id")
            if not number:
                continue
            status = (
                profile.get("status")
                or profile.get("browser_status")
                or profile.get("online")
            )
            if status is None:
                continue
            cdp = _cdp_value(profile)
            if not cdp or (verify_connection and not _cdp_reachable(cdp)):
                continue
            if status in running_values:
                windows.append((str(number), str(profile.get("name") or "")))
        if not windows:
            self.last_error = "AdsPower 未返回已打开窗口状态，请确认窗口已启动"
        return windows


class BitBrowserClient:
    display_name = BROWSER_PROVIDER_NAMES["bitbrowser"]

    def __init__(self, base_url: str = "http://127.0.0.1:54345") -> None:
        self.base_url = base_url.rstrip("/")
        self.last_error = ""
        self._profiles: list[dict] | None = None

    @staticmethod
    def _successful(payload: dict) -> bool:
        return (
            payload.get("success") is True
            or payload.get("code") == 0
            or str(payload.get("status", "")).lower() == "success"
        )

    @staticmethod
    def _profile_list(payload: dict) -> list[dict]:
        data = payload.get("data") or {}
        if isinstance(data, list):
            return data
        for key in ("list", "items", "rows", "profiles", "records", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, list):
                return nested
        return []

    def _load_profiles(self, minimum_count: int) -> list[dict]:
        page_size = min(max(minimum_count, 100), 1000)
        errors: list[str] = []
        try:
            response = requests.post(
                f"{self.base_url}/browser/list",
                json={"page": 0, "pageSize": page_size},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"POST /browser/list：{exc}")
            try:
                response = requests.get(
                    f"{self.base_url}/browser/list",
                    params={"page": 0, "pageSize": page_size},
                    timeout=20,
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                self.last_error = f"无法读取 BitBrowser Global 窗口列表：{exc}"
                raise BrowserProviderError(self.last_error) from exc
        if not self._successful(payload):
            self.last_error = payload.get("msg") or "BitBrowser Global 窗口列表读取失败"
            raise BrowserProviderError(self.last_error)
        profiles = self._profile_list(payload)
        if not profiles:
            self.last_error = "BitBrowser Global 当前没有可用窗口"
            raise BrowserProviderError(self.last_error)
        self._profiles = profiles
        return profiles

    def _profile_id(self, profile_no: int) -> str:
        profiles = self._profiles or self._load_profiles(profile_no)
        sequence_keys = (
            "seq",
            "serialNumber",
            "serial_number",
            "serialNum",
            "serial",
            "browserSeq",
            "sort",
            "index",
            "order",
            "position",
        )
        for profile in profiles:
            for key in sequence_keys:
                raw_value = profile.get(key)
                try:
                    if int(raw_value) == profile_no:
                        return str(profile.get("id") or profile.get("browserId") or "")
                except (TypeError, ValueError):
                    if str(raw_value).strip() == str(profile_no):
                        return str(profile.get("id") or profile.get("browserId") or "")
        if profile_no > len(profiles):
            profiles = self._load_profiles(profile_no)
        if profile_no <= 0 or profile_no > len(profiles):
            seqs = []
            for profile in profiles:
                try:
                    seqs.append(int(profile.get("seq")))
                except (TypeError, ValueError):
                    continue
            hint = (
                f"；可用真实序号区间：{min(seqs)} ~ {max(seqs)}"
                if seqs
                else ""
            )
            self.last_error = (
                f"BitBrowser Global 窗口序号 {profile_no} 不存在；"
                f"当前读取到 {len(profiles)} 个窗口{hint}。"
                "也可使用列表位置 1~N（按列表顺序）"
            )
            raise BrowserProviderError(self.last_error)
        profile = profiles[profile_no - 1]
        profile_id = profile.get("id") or profile.get("browserId")
        if not profile_id:
            self.last_error = f"BitBrowser Global 第 {profile_no} 个窗口缺少 ID"
            raise BrowserProviderError(self.last_error)
        return str(profile_id)

    def start_profile(self, profile_no: int) -> BrowserConnection:
        profile_id = self._profile_id(profile_no)
        try:
            response = requests.post(
                f"{self.base_url}/browser/open",
                json={"id": profile_id},
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            self.last_error = f"无法连接 BitBrowser Global：{exc}"
            raise BrowserProviderError(self.last_error) from exc
        cdp_url = _cdp_from_data(payload.get("data"))
        if not self._successful(payload) and not cdp_url:
            self.last_error = payload.get("msg") or "BitBrowser Global 启动窗口失败"
            raise BrowserProviderError(self.last_error)
        if not cdp_url:
            self.last_error = "BitBrowser Global 未返回 CDP 连接地址"
            raise BrowserProviderError(self.last_error)
        self.last_error = ""
        return BrowserConnection(profile_no=profile_no, cdp_url=cdp_url)

    def ping(self) -> bool:
        errors: list[str] = []
        try:
            response = requests.post(
                f"{self.base_url}/browser/list",
                json={"page": 0, "pageSize": 1},
                timeout=2,
            )
            payload = response.json()
            if response.ok and self._successful(payload):
                self.last_error = ""
                return True
            errors.append(
                f"{response.status_code}：{payload.get('msg') or response.text[:120]}"
            )
        except (requests.RequestException, ValueError) as exc:
            errors.append(str(exc))
        try:
            response = requests.get(
                f"{self.base_url}/browser/list",
                params={"page": 0, "pageSize": 1},
                timeout=2,
            )
            payload = response.json()
            if response.ok and self._successful(payload):
                self.last_error = ""
                return True
            errors.append(
                f"{response.status_code}：{payload.get('msg') or response.text[:120]}"
            )
        except (requests.RequestException, ValueError) as exc:
            errors.append(str(exc))
        self.last_error = errors[-1] if errors else "BitBrowser Global 本地接口无响应"
        return False

    def list_windows(self) -> list[tuple[str, str]]:
        windows: list[tuple[str, str]] = []
        try:
            profiles = self._profiles or self._load_profiles(1)
        except BrowserProviderError:
            profiles = []
        for profile in profiles:
            number = profile.get("seq")
            if number is not None:
                windows.append((str(number), str(profile.get("name") or "")))
        return windows

    def list_running_windows(
        self, verify_connection: bool = True
    ) -> list[tuple[str, str]]:
        windows: list[tuple[str, str]] = []
        running_values = {True, 1, "1", "running", "online", "opened", "open", "active"}
        try:
            profiles = self._profiles or self._load_profiles(1)
        except BrowserProviderError:
            profiles = []
        for profile in profiles:
            number = profile.get("seq")
            if number is None:
                continue
            status = profile.get("status") or profile.get("online")
            if status is None:
                continue
            cdp = _cdp_value(profile)
            if not cdp or (verify_connection and not _cdp_reachable(cdp)):
                continue
            if status in running_values:
                windows.append((str(number), str(profile.get("name") or "")))
        if not windows:
            self.last_error = "BitBrowser 未返回已打开窗口状态，请确认窗口已启动"
        return windows


def create_browser_provider(settings) -> BrowserProvider:
    provider = str(getattr(settings, "browser_provider", "morelogin") or "morelogin")
    if provider == "adspower":
        return AdsPowerClient(settings.adspower_url, settings.adspower_api_key)
    if provider == "bitbrowser":
        return BitBrowserClient(settings.bitbrowser_url)
    return MoreLoginClient(settings.morelogin_url)
