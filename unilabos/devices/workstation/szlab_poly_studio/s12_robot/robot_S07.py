from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from unilabos.devices.workstation.szlab_poly_studio.sensor import S07Sensors
from unilabos.devices.workstation.szlab_poly_studio.s07_solid_addition.s07 import (
    SZLabS07SolidAdditionDevice,
)
from unilabos.devices.workstation.szlab_poly_studio.s07_solid_addition.sensors import (
    POSITION_RANGE,
)

from .robot_tasks import build_variables, powder_container_sensor


class SzlabRobotS07Mixin:
    def _resolve_s071_place_position(self, position: str) -> str:
        position = str(position)
        if position.strip().lower() == "auto":
            raise ValueError("S071 position 不支持 auto，必须在 Task 启动前指定具体槽位")
        powder_container_sensor(position)
        return position

    def _run_s071_place(self, position: str = "1-1") -> dict[str, Any]:
        position = self._resolve_s071_place_position(position)
        sensor = powder_container_sensor(position)
        return self._submit_robot_task(
            task="place",
            station="S071",
            task_number=13,
            variables=build_variables("place_to_s071", S071取放料编号=self._slot_number(position)),
            reset_variables={"S071取放料编号": 0, "任务号": 0},
            position=str(position),
            target_sensor_variable=sensor,
        )

    def _run_s071_pick(self, position: str = "1-1") -> dict[str, Any]:
        sensor = powder_container_sensor(position)
        return self._submit_robot_task(
            task="pick",
            station="S071",
            task_number=14,
            variables=build_variables("pick_from_s071", S071取放料编号=self._slot_number(position)),
            reset_variables={"S071取放料编号": 0, "任务号": 0},
            position=str(position),
            source_sensor_variable=sensor,
        )

    def _run_s071_pick_and_rotate_to_feed(
        self,
        position: str = "1-1",
        load_position: int = 1,
    ) -> dict[str, Any]:
        position = str(position)
        load_position = int(load_position)
        powder_container_sensor(position)
        self._slot_number(position)
        if load_position not in POSITION_RANGE:
            raise ValueError("load_position 必须在 1-10 范围内")
        if self._plc_gateway is None:
            raise RuntimeError("S071 并行上料需要注入 szlab_poly_plc 网关")

        resolved_contract = self._resolved_action_contract(
            "submit_pick_from_s071_and_rotate_to_feed",
            {"position": position, "load_position": load_position},
        )
        contract_assertion = self._assert_contract_conditions(resolved_contract)
        if not contract_assertion["success"]:
            return {
                "success": False,
                "message": "S071 复合动作最终安全断言失败",
                "status": "precondition_failed",
                "position": position,
                "load_position": load_position,
                "contract_assertion": contract_assertion,
            }

        s07 = SZLabS07SolidAdditionDevice(
            plc_device_id=self.plc_device_id,
            poll_interval=0.2,
        )
        s07.set_plc_gateway(self._plc_gateway)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="S071ParallelLoading") as executor:
            robot_future = executor.submit(self._run_s071_pick, position)
            rotate_future = executor.submit(
                s07.rotate_powder_cartridge_to_feed,
                load_position,
            )
            try:
                robot_result = robot_future.result()
            except Exception as exc:
                robot_result = {"success": False, "message": str(exc)}
            try:
                rotate_result = rotate_future.result()
            except Exception as exc:
                rotate_result = {"success": False, "message": str(exc)}

        success = bool(robot_result.get("success")) and bool(rotate_result.get("success"))
        return {
            "success": success,
            "message": (
                "S071 取粉罐与 S07 旋转到上料位均已完成"
                if success
                else "S071 并行上料部分失败；现场状态可能已变化，禁止自动重试"
            ),
            "status": "completed" if success else "partial_failure",
            "position": position,
            "load_position": load_position,
            "contract_assertion": contract_assertion,
            "robot_pick": robot_result,
            "s07_rotate": rotate_result,
        }

    def _run_s072_place(self, product_type: int) -> dict[str, Any]:
        return self._submit_robot_task(
            task="place",
            station="S072",
            task_number=15,
            variables=build_variables("place_to_s072", S072取放料产品=product_type),
            reset_variables={"S072取放料产品": 0, "任务号": 0},
            product_type=int(product_type),
        )

    def _run_s072_pick(self, product_type: int) -> dict[str, Any]:
        return self._submit_robot_task(
            task="pick",
            station="S072",
            task_number=16,
            variables=build_variables("pick_from_s072", S072取放料产品=product_type),
            reset_variables={"S072取放料产品": 0, "任务号": 0},
            product_type=int(product_type),
        )
