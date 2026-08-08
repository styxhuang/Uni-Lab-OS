"""仅内存的 OPC 快照接收与持久化状态管理。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import threading
import time
from typing import Any


@dataclass
class _OpcSnapshot:
    """单个 workflow/provider 的变量增量快照。"""

    sequence: int
    values: dict[str, Any] = field(default_factory=dict)
    updated_at_by_variable: dict[str, float] = field(default_factory=dict)


class OpcConditionProvider:
    """保存非持久化 OPC 值，不判定 Task 3 编译产物。

    ``admission_gates`` 在本阶段只编译和持久化；运行时准入由后续任务实现。
    该 provider 不保存 OPC 地址、账户或密码。
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._snapshots: dict[tuple[str, str], _OpcSnapshot] = {}
        self._lock = threading.RLock()

    def update(
        self,
        workflow_path: str,
        plc_device_id: str,
        sequence: int,
        variables: Mapping[str, Any],
    ) -> bool:
        """合并增量变量；重复或过期 sequence 返回 False。"""
        key = (workflow_path, plc_device_id)
        with self._lock:
            current = self._snapshots.get(key)
            if current is not None and sequence <= current.sequence:
                return False
            values = dict(current.values) if current is not None else {}
            updated_at_by_variable = (
                dict(current.updated_at_by_variable) if current is not None else {}
            )
            values.update(variables)
            received_at = self._clock()
            updated_at_by_variable.update(
                {variable: received_at for variable in variables}
            )
            self._snapshots[key] = _OpcSnapshot(
                sequence=sequence,
                values=values,
                updated_at_by_variable=updated_at_by_variable,
            )
        return True

    def can_update(self, workflow_path: str, plc_device_id: str, sequence: int) -> bool:
        """在不修改快照的前提下判断序列是否会被接受。"""
        with self._lock:
            current = self._snapshots.get((workflow_path, plc_device_id))
            return current is None or sequence > current.sequence

    def export_state(
        self, workflow_path: str, plc_device_id: str
    ) -> dict[str, Any] | None:
        """导出可持久化的快照状态。"""
        with self._lock:
            snapshot = self._snapshots.get((workflow_path, plc_device_id))
            if snapshot is None:
                return None
            return {
                "plc_device_id": plc_device_id,
                "sequence": snapshot.sequence,
                "values": dict(snapshot.values),
                "updated_at_by_variable": dict(snapshot.updated_at_by_variable),
            }

    def restore_state(self, workflow_path: str, state: Mapping[str, Any]) -> None:
        """从持久化状态恢复单个 provider 快照。"""
        plc_device_id = str(state["plc_device_id"])
        with self._lock:
            self._snapshots[(workflow_path, plc_device_id)] = _OpcSnapshot(
                sequence=int(state["sequence"]),
                values=dict(state["values"]),
                updated_at_by_variable=dict(state["updated_at_by_variable"]),
            )

    def clear_state(self, workflow_path: str, plc_device_id: str) -> None:
        with self._lock:
            self._snapshots.pop((workflow_path, plc_device_id), None)

    def clear_workflow(self, workflow_path: str) -> None:
        """清除工作区全部 PLC 的内存快照及序列水位。"""
        with self._lock:
            self._snapshots = {
                key: snapshot
                for key, snapshot in self._snapshots.items()
                if key[0] != workflow_path
            }
