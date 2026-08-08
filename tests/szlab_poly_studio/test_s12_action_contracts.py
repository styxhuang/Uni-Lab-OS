from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from unilabos.devices.workstation.szlab_poly_studio.s04_magnetic_stirring.sensors import (
    s04_ready_var,
)
from unilabos.devices.workstation.szlab_poly_studio.s05_photoshotting.sensors import (
    S05_READY,
)
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot import (
    SzlabMixerRobotDevice,
)
from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_tasks import (
    S05_MATERIAL_SENSOR,
    S06_MATERIAL_SENSOR,
    product_slot_sensor,
)
from unilabos.registry.ast_registry_scanner import scan_directory
from unilabos.registry.decorators import get_action_meta


def _contract_api():
    module_name = "unilabos.registry.action_contract"
    assert importlib.util.find_spec(module_name) is not None, "缺少 ActionContract 安全解析执行 API"
    return importlib.import_module(module_name)


def _contract(method_name: str) -> dict[str, Any]:
    meta = get_action_meta(getattr(SzlabMixerRobotDevice, method_name))
    assert meta is not None
    contract = meta["contract"]
    assert contract is not None, f"{method_name} 缺少声明式契约"
    return contract


def _resolve(method_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _contract_api().resolve_action_contract(_contract(method_name), arguments)


def test_s072_pick_and_s06_place_export_opposite_occupancy_contracts():
    s072 = _resolve(
        "submit_pick_from_s072",
        {"product_type": 2, "position": 1},
    )
    s06 = _resolve("submit_place_to_s06", {})

    assert s072["opc_conditions"] == [
        {
            "source": "occupancy",
            "variable": "occupancy:szlab_mixer_robot:S072:lane:2",
            "operator": "eq",
            "expected": True,
        }
    ]
    assert s072["effects"] == [
        {
            "source": "occupancy",
            "variable": "occupancy:szlab_mixer_robot:S072:lane:2",
            "expected": False,
        }
    ]
    assert [item["resource_id"] for item in s072["physical_resources"]] == [
        "device:szlab_mixer_robot",
        "slot:szlab_mixer_robot:S072:lane:2",
    ]
    assert s06["opc_conditions"][0] == {
        "source": "opc",
        "variable": S06_MATERIAL_SENSOR,
        "operator": "eq",
        "expected": False,
    }
    assert s06["effects"][0] == {
        "source": "opc",
        "variable": S06_MATERIAL_SENSOR,
        "expected": True,
    }


def test_s03_dynamic_product_and_position_resolve_variable_and_resource():
    resolved = _resolve(
        "submit_pick_from_s03",
        {"product_type": 1, "position": "1-2"},
    )

    variable = product_slot_sensor(1, "1-2", used=False)
    assert resolved["opc_conditions"][0]["variable"] == variable
    assert resolved["opc_conditions"][0]["expected"] is True
    assert resolved["effects"][0] == {
        "source": "opc",
        "variable": variable,
        "expected": False,
    }
    assert resolved["physical_resources"][1]["resource_id"] == (
        "slot:szlab_mixer_robot:S03:1:1-2"
    )


@pytest.mark.parametrize(
    ("pick_name", "place_name", "arguments"),
    [
        ("submit_pick_from_s03", "submit_place_to_s03", {"product_type": 1, "position": "1-1"}),
        ("submit_pick_from_s04", "submit_place_to_s04", {"position": 2, "sample_id": "sample"}),
        ("submit_pick_from_s05", "submit_place_to_s05", {"sample_id": "sample"}),
        ("submit_pick_from_s09", "submit_place_to_s09", {"product_type": 2, "position": 4}),
    ],
)
def test_pick_and_place_have_reverse_preconditions_and_effects(
    pick_name: str,
    place_name: str,
    arguments: dict[str, Any],
):
    pick = _resolve(pick_name, arguments)
    place = _resolve(place_name, arguments)

    assert pick["opc_conditions"][0]["variable"] == place["opc_conditions"][0]["variable"]
    assert pick["opc_conditions"][0]["expected"] is True
    assert pick["effects"][0]["expected"] is False
    assert place["opc_conditions"][0]["expected"] is False
    assert place["effects"][0]["expected"] is True


def test_s04_and_s05_place_include_station_ready_gate():
    s04 = _resolve("submit_place_to_s04", {"position": 3, "sample_id": "sample"})
    s05 = _resolve("submit_place_to_s05", {"sample_id": "sample"})

    assert s04["opc_conditions"][1] == {
        "source": "opc",
        "variable": s04_ready_var(3),
        "operator": "eq",
        "expected": True,
    }
    assert s05["opc_conditions"] == [
        {
            "source": "opc",
            "variable": S05_MATERIAL_SENSOR,
            "operator": "eq",
            "expected": False,
        },
        {
            "source": "opc",
            "variable": S05_READY,
            "operator": "eq",
            "expected": True,
        },
    ]


def test_s09_and_s072_use_formal_software_occupancy_resources():
    for method_name, arguments, station in [
        ("submit_place_to_s09", {"product_type": 3, "position": 1}, "S09"),
        ("submit_pick_from_s072", {"product_type": 1}, "S072"),
    ]:
        resolved = _resolve(method_name, arguments)
        variable = resolved["opc_conditions"][0]["variable"]
        resource_id = resolved["physical_resources"][1]["resource_id"]
        assert variable.startswith(f"occupancy:szlab_mixer_robot:{station}:")
        assert resource_id.startswith(f"slot:szlab_mixer_robot:{station}:")


def test_device_skips_logical_occupancy_conditions_without_local_state():
    device = SzlabMixerRobotDevice()
    resolved = device._resolved_action_contract(
        "submit_pick_from_s072",
        {"product_type": 1},
    )

    assertion = device._assert_contract_conditions(resolved)

    assert assertion == {
        "success": True,
        "values": {},
        "mismatches": [],
        "skipped_logical_conditions": [
            "occupancy:szlab_mixer_robot:S072:lane:1"
        ],
    }
    assert not hasattr(device, "_software_occupancy")
    assert not hasattr(device, "set_software_occupancy")


@pytest.mark.parametrize(
    "method_name",
    [
        "submit_pick_from_s01",
        "submit_pick_from_s072",
        "submit_place_to_s09",
    ],
)
def test_logical_occupancy_is_explicitly_typed_for_task_compiler(
    method_name: str,
):
    arguments = {"product_type": 1, "position": 1}
    resolved = _resolve(method_name, arguments)

    assert resolved["opc_conditions"][0]["source"] == "occupancy"
    assert resolved["effects"][0]["source"] == "occupancy"


def test_s071_auto_is_rejected_and_schema_lists_only_concrete_slots():
    class NoIoGateway:
        def read_variable(self, *_args, **_kwargs):
            raise AssertionError("auto 应在读取 PLC 前拒绝")

        def write_variable(self, *_args, **_kwargs):
            raise AssertionError("auto 应在写入 PLC 前拒绝")

    device = SzlabMixerRobotDevice()
    device.set_plc_gateway(NoIoGateway())

    result = device.submit_place_to_s071(position="auto")
    root = Path("unilabos/devices/workstation/szlab_poly_studio/s12_robot")
    with ThreadPoolExecutor(max_workers=1) as executor:
        ast_meta = scan_directory(
            root,
            python_path=Path(".").resolve(),
            executor=executor,
        )["devices"]["szlab_mixer_robot"]
    from unilabos.registry.registry import Registry

    entry = Registry()._build_device_entry_from_ast("szlab_mixer_robot", ast_meta)
    position_schema = entry["class"]["action_value_mappings"][
        "auto-submit_place_to_s071"
    ]["schema"]["properties"]["goal"]["properties"]["position"]

    assert result["success"] is False
    assert result["status"] == "invalid_arguments"
    assert "auto" in result["message"]
    assert position_schema["enum"] == ["1-1", "1-2", "1-3", "2-1", "2-2", "2-3"]
    assert "auto" not in position_schema["enum"]


def test_formal_szlab_workflow_uses_concrete_s071_slots():
    workflow_path = Path(
        "unilabos/devices/workstation/szlab_poly_studio/workflows/"
        "szlab_single_sample_atomic_workflow.json"
    )
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    positions = [
        item["action"]["params"]["position"]
        for rule in workflow["rules"]
        for item in rule["actions"]
        if item["action"].get("method") == "submit_place_to_s071"
    ]

    assert positions
    assert "auto" not in positions
    assert set(positions) <= {"1-1", "1-2", "1-3", "2-1", "2-2", "2-3"}


def test_s072_beaker_flow_uses_hardware_product_type():
    flow = json.loads(
        Path("szlab_robot_action_workflow_flow.json").read_text(encoding="utf-8")
    )
    s072_actions = [
        item["action"]
        for rule in flow["rules"]
        for item in rule["actions"]
        if item["action"]["method"]
        in {"submit_place_to_s072", "submit_pick_from_s072"}
    ]

    assert [action["node"] for action in s072_actions] == [
        "S072 放烧杯",
        "S072 取烧杯",
    ]
    assert [action["params"] for action in s072_actions] == [
        {"product_type": 2},
        {"product_type": 2},
    ]


def test_s071_workflow_contract_state_sequence_never_repicks_empty_slot():
    workflow = json.loads(
        Path(
            "unilabos/devices/workstation/szlab_poly_studio/workflows/"
            "szlab_single_sample_atomic_workflow.json"
        ).read_text(encoding="utf-8")
    )
    s071_actions = [
        item["action"]
        for item in workflow["rules"][0]["actions"]
        if item["action"]["method"]
        in {
            "submit_place_to_s071",
            "submit_pick_from_s071_and_rotate_to_feed",
        }
    ]
    state = {
        "slot:szlab_mixer_robot:S071:1:1-1": True,
        "slot:szlab_mixer_robot:S071:1:1-2": True,
        "slot:szlab_mixer_robot:S071:1:2-2": False,
        "slot:szlab_mixer_robot:S071:1:2-3": False,
    }
    picked_slots: list[str] = []

    for action in s071_actions:
        resolved = _resolve(action["method"], action["params"])
        resource_id = next(
            resource["resource_id"]
            for resource in resolved["physical_resources"]
            if resource["resource_id"].startswith("slot:")
        )
        condition = resolved["opc_conditions"][0]
        effect = resolved["effects"][0]
        assert state[resource_id] is condition["expected"], action["workflow_node_id"]
        if action["method"] == "submit_pick_from_s071_and_rotate_to_feed":
            picked_slots.append(resource_id)
        state[resource_id] = effect["expected"]

    assert picked_slots == [
        "slot:szlab_mixer_robot:S071:1:1-1",
        "slot:szlab_mixer_robot:S071:1:1-2",
    ]


def test_s072_schema_and_contract_use_product_lane_without_position():
    assert "position" not in inspect.signature(
        SzlabMixerRobotDevice.submit_place_to_s072
    ).parameters
    assert "position" not in inspect.signature(
        SzlabMixerRobotDevice.submit_pick_from_s072
    ).parameters

    pick = _resolve("submit_pick_from_s072", {"product_type": 2})
    place = _resolve("submit_place_to_s072", {"product_type": 2})

    assert pick["opc_conditions"][0]["variable"] == (
        "occupancy:szlab_mixer_robot:S072:lane:2"
    )
    assert place["physical_resources"][1]["resource_id"] == (
        "slot:szlab_mixer_robot:S072:lane:2"
    )
    assert "position" not in json.dumps([pick, place])


def test_entity_effect_settle_succeeds_after_delayed_plc_update(monkeypatch):
    device = SzlabMixerRobotDevice(
        effect_settle_timeout=0.2,
        effect_poll_interval=0.01,
    )
    values = iter([False, False, True])
    device.set_plc_gateway(
        type(
            "Gateway",
            (),
            {"read_variable": lambda _self, _name, use_cache=False: next(values)},
        )()
    )
    sleeps: list[float] = []
    monkeypatch.setattr(
        "unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    result = device._verify_contract_effects(
        {
            "effects": [
                {
                    "source": "opc",
                    "variable": "实体传感器",
                    "expected": True,
                }
            ]
        }
    )

    assert result["success"] is True
    assert result["attempts"]["实体传感器"] == 3
    assert sleeps == [0.01, 0.01]


def test_entity_effect_settle_timeout_is_structured(monkeypatch):
    device = SzlabMixerRobotDevice(
        effect_settle_timeout=0.05,
        effect_poll_interval=0.01,
    )
    device.set_plc_gateway(
        type(
            "Gateway",
            (),
            {"read_variable": lambda _self, _name, use_cache=False: False},
        )()
    )
    monotonic_values = iter([0.0, 0.0, 0.02, 0.051])
    monkeypatch.setattr(
        "unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot.time.sleep",
        lambda _seconds: None,
    )

    result = device._verify_contract_effects(
        {
            "effects": [
                {
                    "source": "opc",
                    "variable": "实体传感器",
                    "expected": True,
                }
            ]
        }
    )

    assert result["success"] is False
    assert result["timed_out"] is True
    assert result["mismatches"][0]["reason"] == "effect_settle_timeout"


def test_contract_assertion_and_hardware_write_share_robot_task_lock(monkeypatch):
    class Gateway:
        def __init__(self):
            self.values = {
                S06_MATERIAL_SENSOR: False,
                "Robot_Home": True,
                "Robot_任务允许写入": True,
                "Robot_任务完成": 11,
            }

        def read_variable(self, name: str, use_cache: bool = False):
            del use_cache
            return self.values.get(name, True)

        def write_variable(self, name: str, value: Any):
            self.values[name] = value
            if name == "Robot_任务写入完成" and value is True:
                self.values[S06_MATERIAL_SENSOR] = True

        def wait_variable_equal(self, name: str, expected: Any, interval: float):
            del interval
            return self.read_variable(name) == expected

    device = SzlabMixerRobotDevice()
    device.set_plc_gateway(Gateway())
    lock_states: list[tuple[str, bool]] = []
    original_assert = device._assert_contract_conditions
    original_write = device._write_variable

    def assert_with_lock(contract):
        lock_states.append(("assert", device._robot_task_lock._is_owned()))
        return original_assert(contract)

    def write_with_lock(name, value):
        lock_states.append(("write", device._robot_task_lock._is_owned()))
        return original_write(name, value)

    monkeypatch.setattr(device, "_assert_contract_conditions", assert_with_lock)
    monkeypatch.setattr(device, "_write_variable", write_with_lock)

    result = device.submit_place_to_s06()

    assert result["success"] is True
    assert lock_states
    assert all(owned for _phase, owned in lock_states)


def test_unknown_resolver_missing_argument_and_invalid_result_fail_explicitly():
    api = _contract_api()

    with pytest.raises(api.ContractResolutionError, match="未注册"):
        api.resolve_contract_value(
            {"kind": "resolver", "resolver_id": "unknown", "arguments": {}},
            {},
        )
    with pytest.raises(api.ContractResolutionError, match="缺少 Action 参数"):
        api.resolve_contract_value(
            {"kind": "parameter", "parameter": "position", "path": []},
            {},
        )

    resolver_id = "tests.invalid_result"
    api.register_contract_resolver(resolver_id, lambda: object(), replace=True)
    with pytest.raises(api.ContractResolutionError, match="非法结果"):
        api.resolve_contract_value(
            {"kind": "resolver", "resolver_id": resolver_id, "arguments": {}},
            {},
        )


def test_runtime_final_assertion_is_nonblocking_and_rejects_before_hardware_write(monkeypatch):
    class Gateway:
        def __init__(self):
            self.writes: list[tuple[str, Any]] = []

        def read_variable(self, name: str, use_cache: bool = False):
            del use_cache
            values = {
                S06_MATERIAL_SENSOR: True,
                "Robot_Home": True,
                "Robot_任务允许写入": True,
            }
            return values[name]

        def write_variable(self, name: str, value: Any):
            self.writes.append((name, value))

    device = SzlabMixerRobotDevice()
    gateway = Gateway()
    device.set_plc_gateway(gateway)
    monkeypatch.setattr(
        device,
        "_wait_sensor_conditions",
        lambda *_args, **_kwargs: pytest.fail("运行时不应进入阻塞 sensor gate"),
        raising=False,
    )

    result = device.submit_place_to_s06()

    assert result["success"] is False
    assert result["status"] == "precondition_failed"
    assert result["contract_assertion"]["mismatches"][0]["variable"] == S06_MATERIAL_SENSOR
    assert gateway.writes == []


def test_runtime_and_ast_export_all_s12_contracts_consistently():
    root = Path("unilabos/devices/workstation/szlab_poly_studio/s12_robot")
    with ThreadPoolExecutor(max_workers=2) as executor:
        scanned = scan_directory(
            root,
            python_path=Path(".").resolve(),
            executor=executor,
        )

    ast_actions = scanned["devices"]["szlab_mixer_robot"]["actions"]
    schedulable = {
        name
        for name in ast_actions
        if name.startswith("submit_")
    }
    assert schedulable
    for method_name in schedulable:
        ast_contract = ast_actions[method_name]["action_args"]["contract"]
        assert ast_contract is not None, method_name
        assert ast_contract == _contract(method_name)
        resolved = _contract_api().resolve_action_contract(
            ast_contract,
            {
                "product_type": 1,
                "position": (
                    "1-1"
                    if any(token in method_name for token in ("s03", "s071", "s11"))
                    else 1
                ),
                "sample_id": "sample",
                "load_position": 1,
            },
        )
        assert "device:szlab_mixer_robot" in {
            item["resource_id"] for item in resolved["physical_resources"]
        }


def test_all_25_s12_action_schemas_expose_the_same_contract_as_action_entry():
    from unilabos.registry.registry import Registry

    root = Path("unilabos/devices/workstation/szlab_poly_studio/s12_robot")
    with ThreadPoolExecutor(max_workers=2) as executor:
        scanned = scan_directory(
            root,
            python_path=Path(".").resolve(),
            executor=executor,
        )

    ast_meta = scanned["devices"]["szlab_mixer_robot"]
    registry = Registry()
    entry = registry._build_device_entry_from_ast("szlab_mixer_robot", ast_meta)
    registry.device_type_registry = {"szlab_mixer_robot": entry}
    registry.resolve_types_for_device(
        "szlab_mixer_robot",
        cls=SzlabMixerRobotDevice,
    )
    action_entries = entry["class"]["action_value_mappings"]
    schedulable = {
        name: action
        for name, action in action_entries.items()
        if name.startswith("auto-submit_")
    }

    assert len(schedulable) == 25
    for action_name, action_entry in schedulable.items():
        method_name = action_name.removeprefix("auto-")
        ast_contract = ast_meta["actions"][method_name]["action_args"]["contract"]
        assert action_entry["contract"] == ast_contract == _contract(method_name)
        assert action_entry["schema"]["contract"] is action_entry["contract"]
