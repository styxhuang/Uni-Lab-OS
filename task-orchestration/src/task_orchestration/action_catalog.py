"""从 Uni-Lab AST Registry 构建只读 Action contract catalog。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib
from pathlib import Path
from threading import RLock
from typing import Any, Protocol


class ActionCatalog(Protocol):
    """Task 编译器所需的最小 Action catalog 边界。"""

    def action_contract(
        self, device_id: str, method: str
    ) -> tuple[bool, dict[str, Any] | None]:
        """返回 Action 是否存在及其声明式契约。"""


class AstRegistryActionCatalog:
    """延迟扫描本地 Uni-Lab 设备目录并缓存 Action schema。"""

    def __init__(self, repository_root: Path | str) -> None:
        self._repository_root = Path(repository_root).resolve()
        self._entries: dict[tuple[str, str], dict[str, Any] | None] | None = None
        self._device_modules: dict[str, str] = {}
        self._loaded_modules: set[str] = set()
        self._lock = RLock()

    def action_contract(
        self, device_id: str, method: str
    ) -> tuple[bool, dict[str, Any] | None]:
        entries = self._load()
        key = (device_id, method)
        contract = entries.get(key)
        if key in entries and contract is not None:
            self._load_runtime_resolvers(device_id)
        return key in entries, contract

    def _load(self) -> dict[tuple[str, str], dict[str, Any] | None]:
        with self._lock:
            if self._entries is not None:
                return self._entries
            from unilabos.registry.ast_registry_scanner import scan_directory

            devices_root = self._repository_root / "unilabos" / "devices"
            with ThreadPoolExecutor(max_workers=4) as executor:
                scanned = scan_directory(
                    devices_root,
                    python_path=self._repository_root,
                    executor=executor,
                    raise_on_error=False,
                )
            entries: dict[tuple[str, str], dict[str, Any] | None] = {}
            for device_id, device in scanned["devices"].items():
                module = device.get("module")
                if isinstance(module, str):
                    self._device_modules[device_id] = module.split(":", 1)[0]
                for method, action in device.get("actions", {}).items():
                    entries[(device_id, method)] = action["action_args"].get(
                        "contract"
                    )
                for method in device.get("auto_methods", {}):
                    entries.setdefault((device_id, method), None)
            self._entries = entries
            return entries

    def _load_runtime_resolvers(self, device_id: str) -> None:
        """导入 AST 已确认的本地设备模块，使其注册白名单 resolver。"""
        with self._lock:
            module = self._device_modules.get(device_id)
            if module is None or module in self._loaded_modules:
                return
            importlib.import_module(module)
            self._loaded_modules.add(module)
