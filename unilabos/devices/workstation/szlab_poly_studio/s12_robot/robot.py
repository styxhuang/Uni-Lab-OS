from __future__ import annotations

import os
import threading
import time
from typing import Any, Literal

from unilabos.registry.action_contract import resolve_action_contract
from unilabos.registry.decorators import (
    ActionInputHandle,
    DataSource,
    action,
    device,
    get_action_meta,
    not_action,
)
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_tasks import (
    ROBOT_ACTION_SPECS,
    ROBOT_HOME_VARIABLE,
    ROBOT_TASK_COMPLETE_VARIABLE,
    ROBOT_TASK_NUMBER_VARIABLE,
    ROBOT_WRITE_ALLOWED_VARIABLE,
    ROBOT_WRITE_DONE_VARIABLE,
)
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S01 import SzlabRobotS01Mixin
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S02 import SzlabRobotS02Mixin
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S03 import SzlabRobotS03Mixin
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S04 import SzlabRobotS04Mixin
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S05 import SzlabRobotS05Mixin
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S06 import SzlabRobotS06Mixin
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S07 import SzlabRobotS07Mixin
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S08 import SzlabRobotS08Mixin
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S09 import SzlabRobotS09Mixin
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S10 import SzlabRobotS10Mixin
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_S11 import SzlabRobotS11Mixin


@device(
    id="szlab_mixer_robot",
    display_name="SZLab Mixer 机器人任务",
    category=["robotic_arm"],
    description="SZLab Mixer 机器人任务设备，负责向 PLC 下发 S01-S11 取放料任务号",
)
class SzlabMixerRobotDevice(
    SzlabRobotS01Mixin,
    SzlabRobotS02Mixin,
    SzlabRobotS03Mixin,
    SzlabRobotS04Mixin,
    SzlabRobotS05Mixin,
    SzlabRobotS06Mixin,
    SzlabRobotS07Mixin,
    SzlabRobotS08Mixin,
    SzlabRobotS09Mixin,
    SzlabRobotS10Mixin,
    SzlabRobotS11Mixin,
):
    def __init__(
        self,
        plc_device_id: str = "szlab_poly_plc",
        poll_interval: float = 1.0,
        write_done_hold_seconds: float = 0.0,
        effect_settle_timeout: float = 0.5,
        effect_poll_interval: float = 0.05,
        *args,
        **kwargs,
    ):
        self.plc_device_id = plc_device_id
        self.poll_interval = float(poll_interval)
        self.write_done_hold_seconds = float(write_done_hold_seconds)
        self.effect_settle_timeout = max(0.0, float(effect_settle_timeout))
        self.effect_poll_interval = max(0.001, float(effect_poll_interval))
        self._plc_gateway = None
        self._last_task: dict[str, Any] = {}
        self._robot_task_lock = threading.RLock()

    @not_action
    def set_plc_gateway(self, plc_gateway) -> None:
        self._plc_gateway = plc_gateway

    @not_action
    def _write_variable(self, name: str, value: Any) -> None:
        if self._plc_gateway is None:
            raise RuntimeError("机器人任务需要注入 szlab_poly_plc 网关")
        self._plc_gateway.write_variable(name, value)

    @not_action
    def _read_variable(self, name: str, use_cache: bool = False) -> Any:
        if self._plc_gateway is None:
            raise RuntimeError("机器人任务需要注入 szlab_poly_plc 网关")
        return self._plc_gateway.read_variable(name, use_cache=use_cache)

    @not_action
    def _resolved_action_contract(
        self,
        method_name: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        method = getattr(type(self), method_name)
        meta = get_action_meta(method)
        if meta is None or meta.get("contract") is None:
            raise RuntimeError(f"S12 Action 缺少声明式契约: {method_name}")
        return resolve_action_contract(meta["contract"], parameters)

    @not_action
    def _assert_contract_conditions(
        self,
        resolved_contract: dict[str, Any],
    ) -> dict[str, Any]:
        mismatches: list[dict[str, Any]] = []
        values: dict[str, Any] = {}
        skipped_logical_conditions: list[str] = []
        for condition in resolved_contract["opc_conditions"]:
            variable = str(condition["variable"])
            if condition.get("source", "opc") == "occupancy":
                skipped_logical_conditions.append(variable)
                continue
            expected = condition["expected"]
            actual = self._read_variable(variable, use_cache=False)
            values[variable] = actual
            if actual != expected:
                mismatches.append(
                    {
                        "variable": variable,
                        "expected": expected,
                        "actual": actual,
                    }
                )
        return {
            "success": not mismatches,
            "values": values,
            "mismatches": mismatches,
            "skipped_logical_conditions": skipped_logical_conditions,
        }

    @not_action
    def _verify_contract_effects(
        self,
        resolved_contract: dict[str, Any],
    ) -> dict[str, Any]:
        mismatches: list[dict[str, Any]] = []
        values: dict[str, Any] = {}
        attempts: dict[str, int] = {}
        skipped_logical_effects: list[str] = []
        timed_out = False
        for effect in resolved_contract["effects"]:
            variable = str(effect["variable"])
            if effect.get("source", "opc") == "occupancy":
                skipped_logical_effects.append(variable)
                continue
            expected = effect["expected"]
            started_at = time.monotonic()
            count = 0
            while True:
                count += 1
                actual = self._read_variable(variable, use_cache=False)
                if actual == expected:
                    break
                if time.monotonic() - started_at >= self.effect_settle_timeout:
                    timed_out = True
                    break
                time.sleep(self.effect_poll_interval)
            attempts[variable] = count
            values[variable] = actual
            if actual != expected:
                mismatches.append(
                    {
                        "variable": variable,
                        "expected": expected,
                        "actual": actual,
                        "reason": "effect_settle_timeout",
                    }
                )
        return {
            "success": not mismatches,
            "values": values,
            "mismatches": mismatches,
            "attempts": attempts,
            "timed_out": timed_out,
            "skipped_logical_effects": skipped_logical_effects,
        }

    @not_action
    def _wait_variable_equal(
        self,
        name: str,
        expected: Any,
        interval: float | None = None,
    ) -> bool:
        waiter = getattr(self._plc_gateway, "wait_variable_equal", None) if self._plc_gateway is not None else None
        if callable(waiter):
            return bool(waiter(name, expected, interval=interval or self.poll_interval))

        poll_interval = self.poll_interval if interval is None else interval
        while True:
            if self._read_variable(name, use_cache=False) == expected:
                return True
            time.sleep(poll_interval)

    @not_action
    def _wait_variable_truthy(
        self,
        name: str,
        interval: float | None = None,
    ) -> tuple[bool, Any]:
        poll_interval = self.poll_interval if interval is None else interval
        waiter = getattr(self._plc_gateway, "wait_variable_equal", None) if self._plc_gateway is not None else None
        if callable(waiter):
            success = bool(waiter(name, True, interval=poll_interval))
            return success, True if success else None

        last_value = None
        while True:
            last_value = self._read_variable(name, use_cache=False)
            if bool(last_value):
                return True, last_value
            time.sleep(poll_interval)

    @not_action
    def _slot_number(self, position: str | int) -> int:
        if isinstance(position, int):
            return position
        row_text, col_text = str(position).split("-", maxsplit=1)
        row = int(row_text)
        col = int(col_text)
        if row < 1 or col < 1:
            raise ValueError(f"位置编号必须从 1 开始: {position}")
        return (row - 1) * 6 + col

    @not_action
    def _should_skip_robot_precheck_variable(self, variable_name: str) -> bool:
        raw_variables = os.environ.get("SKIP_ROBOT_PRECHECK_VARIABLES", "")
        skipped_variables = {
            item.strip()
            for item in raw_variables.replace(";", ",").split(",")
            if item.strip()
        }
        return variable_name in skipped_variables

    @not_action
    def _run_robot_handshake_precheck(self, target_station: str) -> dict[str, Any]:
        if os.environ.get("SKIP_ROBOT_HANDSHAKE_CHECK") == "1":
            return {
                "target_station": target_station,
                "skipped": True,
                "message": "已跳过 Robot_Home 和 Robot_任务允许写入前置检查",
            }

        status: dict[str, Any] = {
            "target_station": target_station,
            ROBOT_HOME_VARIABLE: None,
            ROBOT_WRITE_ALLOWED_VARIABLE: None,
        }
        if self._should_skip_robot_precheck_variable(ROBOT_HOME_VARIABLE):
            status[ROBOT_HOME_VARIABLE] = "skipped"
        else:
            home_ready, home_value = self._wait_variable_truthy(
                ROBOT_HOME_VARIABLE,
                interval=self.poll_interval,
            )
            status[ROBOT_HOME_VARIABLE] = home_value
            if not home_ready:
                raise RuntimeError(f"等待 {ROBOT_HOME_VARIABLE} 为 True 失败")

        allowed, allowed_value = self._wait_variable_truthy(
            ROBOT_WRITE_ALLOWED_VARIABLE,
            interval=self.poll_interval,
        )
        status[ROBOT_WRITE_ALLOWED_VARIABLE] = allowed_value
        if not allowed:
            raise RuntimeError(f"等待 {ROBOT_WRITE_ALLOWED_VARIABLE} 为 True 失败")
        return status

    @not_action
    def _wait_robot_task_complete(self, task_number: int) -> tuple[bool, str, Any]:
        if os.environ.get("SKIP_ROBOT_HANDSHAKE_CHECK") == "1":
            return True, "已跳过 Robot_任务完成等待", None
        expected = int(task_number)
        success = self._wait_variable_equal(
            ROBOT_TASK_COMPLETE_VARIABLE,
            expected,
            interval=self.poll_interval,
        )
        if success:
            return True, f"{ROBOT_TASK_COMPLETE_VARIABLE} == {expected}", expected
        actual = None
        try:
            actual = self._read_variable(ROBOT_TASK_COMPLETE_VARIABLE, use_cache=False)
        except Exception:
            actual = None
        return False, f"等待 {ROBOT_TASK_COMPLETE_VARIABLE} == {expected} 失败", actual

    @not_action
    def _reset_pc_to_plc_variables(
        self,
        reset_variables: dict[str, Any],
        verify: bool = False,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        reset_writes: dict[str, Any] = {}
        readback: dict[str, Any] = {}
        errors: dict[str, str] = {}
        attempts = max(1, int(max_attempts))
        for attempt in range(1, attempts + 1):
            errors = {}
            for name, value in reset_variables.items():
                try:
                    self._write_variable(name, value)
                    reset_writes[name] = value
                except Exception as exc:
                    errors[name] = str(exc)
            if errors or not verify:
                break
            readback = {}
            mismatches: dict[str, str] = {}
            for name, expected in reset_variables.items():
                try:
                    actual = self._read_variable(name, use_cache=False)
                    readback[name] = actual
                    if actual != expected:
                        mismatches[name] = f"期望 {expected!r}，实际 {actual!r}"
                except Exception as exc:
                    mismatches[name] = str(exc)
            errors = mismatches
            if not errors:
                break
            if attempt < attempts:
                time.sleep(min(self.poll_interval, 0.2))
        return {
            "success": not errors,
            "written_variables": reset_writes,
            "readback": readback,
            "errors": errors,
        }

    @not_action
    def _ensure_written_variables_nonzero(self, written_variables: dict[str, Any]) -> dict[str, Any]:
        names = [name for name in written_variables if name != ROBOT_WRITE_DONE_VARIABLE]
        readback: dict[str, Any] = {name: None for name in names}
        while True:
            zero_variables = {}
            for name in names:
                value = self._read_variable(name, use_cache=False)
                readback[name] = value
                if not bool(value):
                    zero_variables[name] = value
            if not zero_variables:
                return readback
            time.sleep(min(self.poll_interval, 0.2))

    @not_action
    def _submit_robot_task(
        self,
        task: str,
        station: str,
        task_number: int,
        variables: dict[str, Any] | None = None,
        reset_variables: dict[str, Any] | None = None,
        verify_reset: bool = False,
        **data: Any,
    ) -> dict[str, Any]:
        with self._robot_task_lock:
            return self._submit_robot_task_locked(
                task=task,
                station=station,
                task_number=task_number,
                variables=variables,
                reset_variables=reset_variables,
                verify_reset=verify_reset,
                **data,
            )

    @not_action
    def _submit_robot_task_locked(
        self,
        task: str,
        station: str,
        task_number: int,
        variables: dict[str, Any] | None = None,
        reset_variables: dict[str, Any] | None = None,
        verify_reset: bool = False,
        **data: Any,
    ) -> dict[str, Any]:
        reset_variables = reset_variables or {ROBOT_TASK_NUMBER_VARIABLE: 0}
        method_name = next(
            (
                f"submit_{spec.method_name}"
                for spec in ROBOT_ACTION_SPECS.values()
                if spec.task_number == int(task_number)
            ),
            "",
        )
        try:
            resolved_contract = self._resolved_action_contract(method_name, data)
            contract_assertion = self._assert_contract_conditions(resolved_contract)
        except Exception as exc:
            result = {
                "success": False,
                "message": f"机器人动作契约解析或断言失败: {exc}",
                "task": task,
                "station": station,
                "task_number": int(task_number),
                "status": "rejected",
                **data,
            }
            self._last_task = result
            return result

        if not contract_assertion["success"]:
            result = {
                "success": False,
                "message": f"{station} {task} 最终安全断言失败",
                "task": task,
                "station": station,
                "task_number": int(task_number),
                "status": "precondition_failed",
                "contract_assertion": contract_assertion,
                "resolved_contract": resolved_contract,
                **data,
            }
            self._last_task = result
            return result

        try:
            handshake_precheck = self._run_robot_handshake_precheck(station)
        except Exception as exc:
            message = str(exc)
            self._last_task = {
                "task": task,
                "station": station,
                "task_number": int(task_number),
                "status": "rejected",
                "handshake_message": message,
                **data,
            }
            return {"success": False, "message": message, **self._last_task}

        written_variables: dict[str, Any] = {}
        try:
            for name, value in (variables or {}).items():
                int_value = int(value)
                self._write_variable(name, int_value)
                written_variables[name] = int_value
            self._write_variable(ROBOT_TASK_NUMBER_VARIABLE, int(task_number))
            written_variables[ROBOT_TASK_NUMBER_VARIABLE] = int(task_number)
            self._write_variable(ROBOT_WRITE_DONE_VARIABLE, False)
            written_variables[ROBOT_WRITE_DONE_VARIABLE] = False
            write_readback = self._ensure_written_variables_nonzero(written_variables)
            self._write_variable(ROBOT_WRITE_DONE_VARIABLE, True)
            written_variables[ROBOT_WRITE_DONE_VARIABLE] = True
            if self.write_done_hold_seconds > 0:
                time.sleep(self.write_done_hold_seconds)
        except Exception as exc:
            rollback_variables = {
                ROBOT_WRITE_DONE_VARIABLE: False,
                **{
                    name: reset_variables[name]
                    for name in written_variables
                    if name in reset_variables
                },
            }
            reset_result = self._reset_pc_to_plc_variables(
                rollback_variables,
                verify=verify_reset,
            )
            self._last_task = {
                "task": task,
                "station": station,
                "task_number": int(task_number),
                "status": "write_failed",
                "written_variables": written_variables,
                "handshake_precheck": handshake_precheck,
                "reset": reset_result,
                **data,
            }
            return {"success": False, "message": str(exc), **self._last_task}

        complete_success, complete_message, complete_value = self._wait_robot_task_complete(task_number)
        if os.environ.get("SKIP_RESET_AFTER_RUN") == "1":
            try:
                self._write_variable(ROBOT_WRITE_DONE_VARIABLE, False)
            except Exception:
                pass
            reset_result = {
                "success": True,
                "written_variables": {ROBOT_WRITE_DONE_VARIABLE: False},
                "errors": {},
                "skipped": True,
                "message": "已跳过任务完成后的参数复位，仅复位 Robot_任务写入完成",
            }
        else:
            reset_result = self._reset_pc_to_plc_variables(
                {ROBOT_WRITE_DONE_VARIABLE: False, **reset_variables},
                verify=verify_reset,
            )
        status = "completed" if complete_success and reset_result["success"] else "failed"

        self._last_task = {
            "task": task,
            "station": station,
            "task_number": int(task_number),
            "status": status,
            "written_variables": written_variables,
            "write_readback": write_readback,
            "completion_variable": ROBOT_TASK_COMPLETE_VARIABLE,
            "completion_value": complete_value,
            "completion_message": complete_message,
            "handshake_precheck": handshake_precheck,
            "contract_assertion": contract_assertion,
            "resolved_contract": resolved_contract,
            "reset": reset_result,
            **data,
        }
        if not complete_success:
            return {
                "success": False,
                "message": complete_message,
                **self._last_task,
            }
        if not reset_result["success"]:
            return {
                "success": False,
                "message": "机器人任务已完成，但 PC->PLC 变量复位失败",
                **self._last_task,
            }
        try:
            effect_verification = self._verify_contract_effects(resolved_contract)
        except Exception as exc:
            effect_verification = {
                "success": False,
                "values": {},
                "mismatches": [{"message": str(exc)}],
            }
        self._last_task["effect_verification"] = effect_verification
        if not effect_verification["success"]:
            self._last_task["status"] = "verification_failed"
            return {
                "success": False,
                "message": "机器人任务已完成，但契约 effect 快速验证失败；禁止自动重试",
                **self._last_task,
            }
        return {
            "success": True,
            "message": f"机器人任务已完成: {station} {task}",
            **self._last_task,
        }

    @action(
        auto_prefix=True,
        description="S01 取料",
        contract={
            "opc_conditions": [{"source": "occupancy", "variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S01"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "effects": [{"source": "occupancy", "variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S01"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S01"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_pick_from_s01(
        self,
        product_type: int = 1,
        position: int = 1,
    ) -> dict[str, Any]:
        try:
            return self._run_s01_pick(product_type, position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S01", "position": position}

    @action(
        auto_prefix=True,
        description="S02 放 TIP",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S02"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S02"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S02"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_place_to_s02(self, position: int = 1) -> dict[str, Any]:
        try:
            return self._run_s02_place(position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "place", "station": "S02", "position": position}

    @action(
        auto_prefix=True,
        description="S02 取 TIP",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S02"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S02"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S02"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_pick_from_s02(self, position: int = 1) -> dict[str, Any]:
        try:
            return self._run_s02_pick(position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S02", "position": position}

    @action(
        auto_prefix=True,
        description="S03 放容器",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S03"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S03"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S03"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_place_to_s03(self, product_type: int = 1, position: str = "1-1") -> dict[str, Any]:
        try:
            return self._run_s03_place(product_type, position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "place", "station": "S03", "position": position}

    @action(
        auto_prefix=True,
        description="S03 取容器",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S03"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S03"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S03"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_pick_from_s03(self, product_type: int = 1, position: str = "1-1") -> dict[str, Any]:
        try:
            return self._run_s03_pick(product_type, position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S03", "position": position}

    @action(
        auto_prefix=True,
        description="S04 放料",
        contract={
            "opc_conditions": [
                {"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {
                    "kind": "constant", "value": "S04"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False},
                {"variable": {"kind": "resolver", "resolver_id": "szlab.s12.station_ready_variable", "arguments": {"station": {
                    "kind": "constant", "value": "S04"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True},
            ],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S04"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S04"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_place_to_s04(self, position: int = 1, sample_id: str = "") -> dict[str, Any]:
        try:
            return self._run_s04_place(position=position, sample_id=sample_id)
        except Exception as exc:
            return {
                "success": False,
                "message": str(exc),
                "task": "place",
                "station": "S04",
                "position": position,
                "sample_id": sample_id,
            }

    @action(
        auto_prefix=True,
        description="S04 取料",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S04"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S04"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S04"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_pick_from_s04(self, position: int = 1) -> dict[str, Any]:
        try:
            return self._run_s04_pick(position=position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S04", "position": position}

    @action(
        auto_prefix=True,
        description="S05 放料",
        contract={
            "opc_conditions": [
                {"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable",
                              "arguments": {"station": {"kind": "constant", "value": "S05"}}}, "expected": False},
                {"variable": {"kind": "resolver", "resolver_id": "szlab.s12.station_ready_variable",
                              "arguments": {"station": {"kind": "constant", "value": "S05"}}}, "expected": True},
            ],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S05"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S05"}}}}],
        },
    )
    def submit_place_to_s05(self, sample_id: str = "") -> dict[str, Any]:
        try:
            return self._run_s05_place(sample_id=sample_id)
        except Exception as exc:
            return {
                "success": False,
                "message": str(exc),
                "task": "place",
                "station": "S05",
                "sample_id": sample_id,
            }

    @action(
        auto_prefix=True,
        description="S05 取料",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S05"}}}, "expected": True}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S05"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S05"}}}}],
        },
    )
    def submit_pick_from_s05(self, sample_id: str = "") -> dict[str, Any]:
        try:
            return self._run_s05_pick(sample_id=sample_id)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S05", "sample_id": sample_id}

    @action(
        auto_prefix=True,
        description="S06 放料",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S06"}}}, "expected": False}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S06"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S06"}}}}],
        },
    )
    def submit_place_to_s06(self) -> dict[str, Any]:
        try:
            return self._run_s06_place()
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "place", "station": "S06"}

    @action(
        auto_prefix=True,
        description="S06 取料",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S06"}}}, "expected": True}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S06"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S06"}}}}],
        },
    )
    def submit_pick_from_s06(self) -> dict[str, Any]:
        try:
            return self._run_s06_pick()
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S06"}

    @action(
        auto_prefix=True,
        description="S071 放粉罐",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S071"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S071"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S071"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_place_to_s071(
        self,
        position: Literal["1-1", "1-2", "1-3", "2-1", "2-2", "2-3"] = "1-1",
    ) -> dict[str, Any]:
        try:
            return self._run_s071_place(position)
        except Exception as exc:
            return {
                "success": False,
                "message": str(exc),
                "status": "invalid_arguments",
                "task": "place",
                "station": "S071",
                "position": position,
            }

    @action(
        auto_prefix=True,
        description="S071 取粉罐",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S071"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S071"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S071"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_pick_from_s071(self, position: str = "1-1") -> dict[str, Any]:
        try:
            return self._run_s071_pick(position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S071", "position": position}

    @action(
        auto_prefix=True,
        description="并行执行 S071 取粉罐与 S07 旋转到上料位",
        contract={
            "opc_conditions": [
                {"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {
                    "kind": "constant", "value": "S071"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True},
                {"variable": "S07原点信号", "expected": True},
                {"variable": "S07允许加工", "expected": True},
            ],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S071"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": "device:szlab_s07_solid_addition"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S071"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_pick_from_s071_and_rotate_to_feed(
        self,
        position: str = "1-1",
        load_position: int = 1,
    ) -> dict[str, Any]:
        try:
            return self._run_s071_pick_and_rotate_to_feed(position, load_position)
        except Exception as exc:
            return {
                "success": False,
                "message": str(exc),
                "status": "rejected",
                "position": position,
                "load_position": load_position,
            }

    @action(
        auto_prefix=True,
        description="S072 放料",
        contract={
            "opc_conditions": [{"source": "occupancy", "variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S072"}, "product_type": {"kind": "parameter", "parameter": "product_type"}}}, "expected": False}],
            "effects": [{"source": "occupancy", "variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S072"}, "product_type": {"kind": "parameter", "parameter": "product_type"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S072"}, "product_type": {"kind": "parameter", "parameter": "product_type"}}}}],
        },
    )
    def submit_place_to_s072(self, product_type: int = 1) -> dict[str, Any]:
        try:
            return self._run_s072_place(product_type)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "place", "station": "S072"}

    @action(
        auto_prefix=True,
        description="S072 取料",
        contract={
            "opc_conditions": [{"source": "occupancy", "variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S072"}, "product_type": {"kind": "parameter", "parameter": "product_type"}}}, "expected": True}],
            "effects": [{"source": "occupancy", "variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S072"}, "product_type": {"kind": "parameter", "parameter": "product_type"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S072"}, "product_type": {"kind": "parameter", "parameter": "product_type"}}}}],
        },
    )
    def submit_pick_from_s072(self, product_type: int = 1) -> dict[str, Any]:
        try:
            return self._run_s072_pick(product_type)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S072"}

    @action(
        auto_prefix=True,
        description="S08 放瓶",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S08"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S08"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S08"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_place_to_s08(
        self,
        product_type: int = 1,
        position: int = 1,
    ) -> dict[str, Any]:
        try:
            return self._run_s08_place(product_type, position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "place", "station": "S08", "position": position}

    @action(
        auto_prefix=True,
        description="S08 取瓶",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S08"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S08"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S08"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_pick_from_s08(
        self,
        product_type: int = 1,
        position: int = 1,
    ) -> dict[str, Any]:
        try:
            return self._run_s08_pick(product_type, position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S08", "position": position}

    @action(
        auto_prefix=True,
        description="S08 倒料",
        contract={
            "opc_conditions": [{"variable": "传感器状态_上位机[3].NO[14]", "expected": True}],
            "effects": [{"variable": "传感器状态_上位机[3].NO[14]", "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": "slot:szlab_mixer_robot:S08:pour:1"}],
        },
    )
    def submit_pour_from_s08(self, product_type: int = 1) -> dict[str, Any]:
        try:
            return self._run_s08_pour(product_type)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pour", "station": "S08", "product_type": product_type}

    @action(
        auto_prefix=True,
        description="S09 放料",
        contract={
            "opc_conditions": [{"source": "occupancy", "variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S09"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "effects": [{"source": "occupancy", "variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S09"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S09"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
        handles=[
            ActionInputHandle(
                key="product_type",
                data_type="szlab_s09_product_type",
                label="S09取放料产品",
                data_key="product_type",
                data_source=DataSource.HANDLE,
                description="S09取放料产品：1=TIP盒，2=液体试剂瓶，3=烧杯",
            ),
            ActionInputHandle(
                key="position",
                data_type="szlab_s09_position",
                label="S09取放料编号",
                data_key="position",
                data_source=DataSource.HANDLE,
                description="S09取放料编号：TIP盒 1-2，液体试剂瓶 1-5，烧杯 1",
            ),
        ],
    )
    def submit_place_to_s09(self, product_type: int = 1, position: int = 1) -> dict[str, Any]:
        try:
            return self._run_s09_place(product_type, position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "place", "station": "S09", "position": position}

    @action(
        auto_prefix=True,
        description="S09 取料",
        contract={
            "opc_conditions": [{"source": "occupancy", "variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S09"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "effects": [{"source": "occupancy", "variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S09"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S09"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
        handles=[
            ActionInputHandle(
                key="product_type",
                data_type="szlab_s09_product_type",
                label="S09取放料产品",
                data_key="product_type",
                data_source=DataSource.HANDLE,
                description="S09取放料产品：1=TIP盒，2=液体试剂瓶，3=烧杯",
            ),
            ActionInputHandle(
                key="position",
                data_type="szlab_s09_position",
                label="S09取放料编号",
                data_key="position",
                data_source=DataSource.HANDLE,
                description="S09取放料编号：TIP盒 1-2，液体试剂瓶 1-5，烧杯 1",
            ),
        ],
    )
    def submit_pick_from_s09(self, product_type: int = 1, position: int = 1) -> dict[str, Any]:
        try:
            return self._run_s09_pick(product_type, position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S09", "position": position}

    @action(
        auto_prefix=True,
        description="S10 放试剂瓶",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S10"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S10"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S10"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_place_to_s10(self, position: int = 1) -> dict[str, Any]:
        try:
            return self._run_s10_place(position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "place", "station": "S10", "position": position}

    @action(
        auto_prefix=True,
        description="S10 取试剂瓶",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S10"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S10"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S10"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_pick_from_s10(self, position: int = 1) -> dict[str, Any]:
        try:
            return self._run_s10_pick(position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S10", "position": position}

    @action(
        auto_prefix=True,
        description="S11 放成品",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S11"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S11"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S11"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_place_to_s11(self, product_type: int = 1, position: str = "1-1") -> dict[str, Any]:
        try:
            return self._run_s11_place(product_type, position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "place", "station": "S11", "position": position}

    @action(
        auto_prefix=True,
        description="S11 取成品",
        contract={
            "opc_conditions": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S11"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": True}],
            "effects": [{"variable": {"kind": "resolver", "resolver_id": "szlab.s12.slot_variable", "arguments": {"station": {"kind": "constant", "value": "S11"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}, "expected": False}],
            "physical_resources": [{"resource_id": "device:szlab_mixer_robot"}, {"resource_id": {"kind": "resolver", "resolver_id": "szlab.s12.slot_resource", "arguments": {"station": {"kind": "constant", "value": "S11"}, "product_type": {"kind": "parameter", "parameter": "product_type"}, "position": {"kind": "parameter", "parameter": "position"}}}}],
        },
    )
    def submit_pick_from_s11(self, product_type: int = 1, position: str = "1-1") -> dict[str, Any]:
        try:
            return self._run_s11_pick(product_type, position)
        except Exception as exc:
            return {"success": False, "message": str(exc), "task": "pick", "station": "S11", "position": position}

    @action(auto_prefix=True, description="读取最近一次机器人任务提交记录")
    def last_submitted_task(self) -> dict[str, Any]:
        return {"success": True, "task": self._last_task}
