"""Task Template Action 契约编译测试。"""

from __future__ import annotations

import pytest

from task_orchestration.compiler import (
    TemplateCompilationError,
    WorkflowActionNode,
    compile_template_contract,
)


def _contract(
    *,
    preconditions: list[dict] | None = None,
    effects: list[dict] | None = None,
    resources: list[str | dict] | None = None,
) -> dict:
    return {
        "opc_conditions": preconditions or [],
        "effects": effects or [],
        "physical_resources": [
            {"resource_id": resource} for resource in (resources or [])
        ],
    }


def _condition(
    variable: str | dict,
    expected: object,
    *,
    source: str = "opc",
    operator: str = "eq",
) -> dict:
    return {
        "source": source,
        "variable": variable,
        "operator": operator,
        "expected": expected,
    }


def _effect(variable: str, expected: object, *, source: str = "opc") -> dict:
    return {"source": source, "variable": variable, "expected": expected}


def test_s072_pick_then_s06_place_compiles_admission_and_resources():
    from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot import (
        SzlabMixerRobotDevice,
    )
    from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_tasks import (
        S06_MATERIAL_SENSOR,
        robot_slot_resource,
    )
    from unilabos.registry.decorators import get_action_meta

    pick_meta = get_action_meta(SzlabMixerRobotDevice.submit_pick_from_s072)
    place_meta = get_action_meta(SzlabMixerRobotDevice.submit_place_to_s06)
    assert pick_meta is not None and place_meta is not None

    compiled = compile_template_contract(
        ["pick-s072", "place-s06"],
        [
            WorkflowActionNode(
                node_id="pick-s072",
                device_id="szlab_mixer_robot",
                method="submit_pick_from_s072",
                params={"product_type": 2},
                action_contract=pick_meta["contract"],
            ),
            WorkflowActionNode(
                node_id="place-s06",
                device_id="szlab_mixer_robot",
                method="submit_place_to_s06",
                params={},
                action_contract=place_meta["contract"],
            ),
        ],
    )

    assert [
        (gate.source, gate.variable, gate.expected)
        for gate in compiled.admission_gates
    ] == [
        ("occupancy", "occupancy:szlab_mixer_robot:S072:lane:2", True),
        ("opc", S06_MATERIAL_SENSOR, False),
    ]
    assert [item.node_id for item in compiled.node_contracts] == [
        "pick-s072",
        "place-s06",
    ]
    assert [len(item.preconditions) for item in compiled.node_contracts] == [1, 1]
    assert compiled.resource_requirements == [
        "device:szlab_mixer_robot",
        "slot:szlab_mixer_robot:S072:lane:2",
        robot_slot_resource("S06"),
    ]


def test_prior_effect_satisfies_later_precondition_and_removes_admission():
    compiled = compile_template_contract(
        ["prepare", "consume"],
        [
            WorkflowActionNode(
                node_id="prepare",
                device_id="device",
                method="prepare",
                params={},
                action_contract=_contract(
                    effects=[_effect("ready", True)],
                    resources=["device:robot"],
                ),
            ),
            WorkflowActionNode(
                node_id="consume",
                device_id="device",
                method="consume",
                params={},
                action_contract=_contract(
                    preconditions=[_condition("ready", True)],
                    resources=["device:robot", "slot:station"],
                ),
            ),
        ],
    )

    assert compiled.admission_gates == []
    assert compiled.resource_requirements == ["device:robot", "slot:station"]


def test_opposite_prior_effect_rejects_template_with_structured_error():
    with pytest.raises(TemplateCompilationError) as caught:
        compile_template_contract(
            ["prepare", "consume"],
            [
                WorkflowActionNode(
                    node_id="prepare",
                    device_id="device",
                    method="prepare",
                    params={},
                    action_contract=_contract(
                        effects=[_effect("ready", False)]
                    ),
                ),
                WorkflowActionNode(
                    node_id="consume",
                    device_id="device",
                    method="consume",
                    params={},
                    action_contract=_contract(
                        preconditions=[_condition("ready", True)]
                    ),
                ),
            ],
        )

    assert caught.value.code == "contradictory_precondition"
    assert caught.value.detail == {
        "node_id": "consume",
        "source": "opc",
        "variable": "ready",
        "expected": True,
        "established": False,
    }


def test_nested_json_causality_distinguishes_numbers_from_booleans():
    established = {"items": [{"value": 1}, [False, 2]]}
    required = {"items": [{"value": True}, [0, 2]]}

    with pytest.raises(TemplateCompilationError) as caught:
        compile_template_contract(
            ["prepare", "consume"],
            [
                WorkflowActionNode(
                    node_id="prepare",
                    device_id="device",
                    method="prepare",
                    params={},
                    action_contract=_contract(
                        effects=[_effect("nested", established)]
                    ),
                ),
                WorkflowActionNode(
                    node_id="consume",
                    device_id="device",
                    method="consume",
                    params={},
                    action_contract=_contract(
                        preconditions=[_condition("nested", required)]
                    ),
                ),
            ],
        )

    assert caught.value.code == "contradictory_precondition"
    assert caught.value.detail["established"] == established
    assert caught.value.detail["expected"] == required


def test_s03_dynamic_resolver_uses_action_parameters():
    from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot import (
        SzlabMixerRobotDevice,
    )
    from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot_tasks import (
        product_slot_sensor,
    )
    from unilabos.registry.decorators import get_action_meta

    meta = get_action_meta(SzlabMixerRobotDevice.submit_pick_from_s03)
    assert meta is not None
    compiled = compile_template_contract(
        ["pick-s03"],
        [
            WorkflowActionNode(
                node_id="pick-s03",
                device_id="szlab_mixer_robot",
                method="submit_pick_from_s03",
                params={"product_type": 1, "position": "1-2"},
                action_contract=meta["contract"],
            )
        ],
    )

    node = compiled.node_contracts[0]
    assert node.preconditions[0].variable == product_slot_sensor(
        1, "1-2", used=False
    )
    assert node.resources == [
        "device:szlab_mixer_robot",
        "slot:szlab_mixer_robot:S03:1:1-2",
    ]


def test_unknown_resolver_is_structured_compilation_failure():
    contract = _contract(
        resources=[
            {
                "kind": "resolver",
                "resolver_id": "missing.resolver",
                "arguments": {},
            }
        ]
    )

    with pytest.raises(TemplateCompilationError) as caught:
        compile_template_contract(
            ["node"],
            [
                WorkflowActionNode(
                    node_id="node",
                    device_id="device",
                    method="run",
                    params={},
                    action_contract=contract,
                )
            ],
        )

    assert caught.value.code == "contract_resolution_failed"
    assert caught.value.detail["node_id"] == "node"
    assert "未注册" in caught.value.detail["reason"]


def test_missing_bound_action_parameter_is_structured_failure():
    contract = _contract(
        resources=[{"kind": "parameter", "parameter": "slot"}]
    )

    with pytest.raises(TemplateCompilationError) as caught:
        compile_template_contract(
            ["node"],
            [
                WorkflowActionNode(
                    node_id="node",
                    device_id="device",
                    method="run",
                    params={},
                    action_contract=contract,
                )
            ],
        )

    assert caught.value.code == "contract_resolution_failed"
    assert "缺少 Action 参数" in caught.value.detail["reason"]


def test_invalid_s12_resolver_value_is_structured_failure():
    from unilabos.devices.workstation.szlab_poly_studio.s12_robot.robot import (
        SzlabMixerRobotDevice,
    )
    from unilabos.registry.decorators import get_action_meta

    meta = get_action_meta(SzlabMixerRobotDevice.submit_pick_from_s072)
    assert meta is not None

    with pytest.raises(TemplateCompilationError) as caught:
        compile_template_contract(
            ["node"],
            [
                WorkflowActionNode(
                    node_id="node",
                    device_id="szlab_mixer_robot",
                    method="submit_pick_from_s072",
                    params={"product_type": "非法产品类型"},
                    action_contract=meta["contract"],
                )
            ],
        )

    assert caught.value.code == "contract_resolution_failed"
    assert caught.value.detail["node_id"] == "node"


def test_duplicate_admission_conditions_are_stably_deduplicated():
    condition = _condition("ready", True)
    compiled = compile_template_contract(
        ["first", "second"],
        [
            WorkflowActionNode(
                node_id=node_id,
                device_id="device",
                method=node_id,
                params={},
                action_contract=_contract(preconditions=[condition]),
            )
            for node_id in ("first", "second")
        ],
    )

    assert [gate.variable for gate in compiled.admission_gates] == ["ready"]


def test_non_s12_action_without_contract_is_explicitly_empty():
    compiled = compile_template_contract(
        ["plain"],
        [
            WorkflowActionNode(
                node_id="plain",
                device_id="plain_device",
                method="run",
                params={},
                action_contract=None,
            )
        ],
    )

    assert compiled.node_contracts[0].contract_state == "empty"
    assert compiled.node_contracts[0].preconditions == []
    assert compiled.admission_gates == []


@pytest.mark.parametrize(
    ("node_ids", "nodes", "code"),
    [
        (
            ["same", "same"],
            [
                WorkflowActionNode(
                    node_id="same",
                    device_id="device",
                    method="run",
                    params={},
                    action_contract=_contract(),
                )
            ],
            "duplicate_node_id",
        ),
        (
            ["missing"],
            [],
            "workflow_node_not_found",
        ),
        (
            ["not-action"],
            [
                WorkflowActionNode(
                    node_id="not-action",
                    device_id="",
                    method="",
                    params={},
                    action_contract=None,
                    is_action=False,
                )
            ],
            "workflow_node_has_no_action",
        ),
        (
            ["s12"],
            [
                WorkflowActionNode(
                    node_id="s12",
                    device_id="szlab_mixer_robot",
                    method="submit_pick_from_s06",
                    params={},
                    action_contract=None,
                )
            ],
            "required_action_contract_missing",
        ),
    ],
)
def test_invalid_workflow_nodes_fail_structurally(node_ids, nodes, code):
    with pytest.raises(TemplateCompilationError) as caught:
        compile_template_contract(node_ids, nodes)

    assert caught.value.code == code


def test_unsupported_operator_is_rejected_explicitly():
    with pytest.raises(TemplateCompilationError) as caught:
        compile_template_contract(
            ["node"],
            [
                WorkflowActionNode(
                    node_id="node",
                    device_id="device",
                    method="run",
                    params={},
                    action_contract=_contract(
                        preconditions=[
                            _condition("ready", True, operator="ne")
                        ]
                    ),
                )
            ],
        )

    assert caught.value.code == "unsupported_condition_operator"
