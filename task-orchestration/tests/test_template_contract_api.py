"""服务端权威编译 Template 契约的 API 测试。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from task_orchestration.main import create_app


class StaticActionCatalog:
    """测试用 Action catalog，只返回预置 schema。"""

    def __init__(self, contracts: dict[tuple[str, str], dict | None]) -> None:
        self.contracts = contracts

    def action_contract(self, device_id: str, method: str) -> tuple[bool, dict | None]:
        key = (device_id, method)
        return key in self.contracts, self.contracts.get(key)


def _contract(variable: str, expected: bool, effect: bool) -> dict:
    return {
        "opc_conditions": [
            {
                "source": "opc",
                "variable": variable,
                "operator": "eq",
                "expected": expected,
            }
        ],
        "effects": [
            {"source": "opc", "variable": variable, "expected": effect}
        ],
        "physical_resources": [{"resource_id": "device:robot"}],
    }


def _client(tmp_path) -> TestClient:
    workflow = {
        "rules": [
            {
                "actions": [
                    {
                        "action": {
                            "workflow_node_id": "prepare",
                            "device_id": "robot",
                            "method": "prepare",
                            "params": {},
                        }
                    },
                    {
                        "action": {
                            "workflow_node_id": "consume",
                            "device_id": "robot",
                            "method": "consume",
                            "params": {},
                        }
                    },
                ]
            }
        ]
    }
    (tmp_path / "demo.json").write_text(
        json.dumps(workflow), encoding="utf-8"
    )
    catalog = StaticActionCatalog(
        {
            ("robot", "prepare"): _contract("ready", False, True),
            ("robot", "consume"): _contract("ready", True, False),
        }
    )
    return TestClient(create_app(tmp_path, action_catalog=catalog))


def test_create_template_compiles_authoritative_workflow_nodes(tmp_path):
    client = _client(tmp_path)

    response = client.post(
        "/templates",
        json={
            "workflow_path": "demo.json",
            "expected_version": 0,
            "template": {
                "id": "template",
                "name": "Prepare",
                "node_ids": ["prepare", "consume"],
            },
        },
    )

    assert response.status_code == 200
    template = response.json()["workspace"]["templates"][0]
    assert template["node_ids"] == ["prepare", "consume"]
    assert [item["node_id"] for item in template["node_contracts"]] == [
        "prepare",
        "consume",
    ]
    assert template["admission_gates"] == [
        {
            "source": "opc",
            "variable": "ready",
            "operator": "eq",
            "expected": False,
        }
    ]
    assert template["resource_requirements"] == ["device:robot"]


@pytest.mark.parametrize(
    "workflow",
    [
        {
            "nodes": [
                {
                    "workflow_node_id": "prepare",
                    "device_id": "robot",
                    "method": "prepare",
                    "params": {},
                }
            ]
        },
        {
            "data": {
                "nodes": [
                    {
                        "workflow_node_id": "prepare",
                        "device_id": "robot",
                        "method": "prepare",
                        "params": {},
                    }
                ]
            }
        },
    ],
    ids=["top-level-nodes", "data-nodes"],
)
def test_create_template_compiles_each_authoritative_node_container(
    tmp_path, workflow
):
    (tmp_path / "demo.json").write_text(json.dumps(workflow), encoding="utf-8")
    client = TestClient(
        create_app(
            tmp_path,
            action_catalog=StaticActionCatalog(
                {("robot", "prepare"): _contract("ready", False, True)}
            ),
        )
    )

    response = client.post(
        "/templates",
        json={
            "workflow_path": "demo.json",
            "expected_version": 0,
            "template": {
                "id": "template",
                "name": "Prepare",
                "node_ids": ["prepare"],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["workspace"]["templates"][0]["node_contracts"][0][
        "node_id"
    ] == "prepare"


def test_update_template_recompiles_when_node_ids_change(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/templates",
        json={
            "workflow_path": "demo.json",
            "expected_version": 0,
            "template": {
                "id": "template",
                "name": "Prepare",
                "node_ids": ["prepare"],
            },
        },
    )
    assert created.status_code == 200

    response = client.patch(
        "/templates/template",
        json={
            "workflow_path": "demo.json",
            "expected_version": 1,
            "node_ids": ["prepare", "consume"],
        },
    )

    assert response.status_code == 200
    template = response.json()["workspace"]["templates"][0]
    assert template["node_ids"] == ["prepare", "consume"]
    assert len(template["node_contracts"]) == 2


def test_update_template_rejects_blank_name_structurally(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/templates",
        json={
            "workflow_path": "demo.json",
            "expected_version": 0,
            "template": {
                "id": "template",
                "name": "Prepare",
                "node_ids": ["prepare"],
            },
        },
    )
    assert created.status_code == 200

    response = client.patch(
        "/templates/template",
        json={
            "workflow_path": "demo.json",
            "expected_version": 1,
            "name": " ",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_template_name"


def test_create_template_rejects_client_supplied_compiled_or_legacy_gates(tmp_path):
    client = _client(tmp_path)
    base = {
        "id": "template",
        "name": "Prepare",
        "node_ids": ["prepare"],
    }

    for forbidden in (
        {"admission_gates": []},
        {"node_contracts": []},
        {"resource_requirements": []},
        {"input_triggers": []},
        {"output_triggers": []},
        {"resources": []},
    ):
        response = client.post(
            "/templates",
            json={
                "workflow_path": "demo.json",
                "expected_version": 0,
                "template": {**base, **forbidden},
            },
        )
        assert response.status_code == 422, forbidden


def test_workspace_put_cannot_bypass_server_template_compilation(tmp_path):
    client = _client(tmp_path)

    response = client.put(
        "/workspaces",
        json={
            "expected_version": 0,
            "workspace": {
                "workflow_path": "demo.json",
                "templates": [
                    {
                        "schema_version": 2,
                        "id": "forged",
                        "name": "Forged",
                        "node_ids": [],
                        "node_contracts": [],
                        "admission_gates": [
                            {
                                "source": "opc",
                                "variable": "forged",
                                "operator": "eq",
                                "expected": True,
                            }
                        ],
                        "resource_requirements": ["device:forged"],
                    }
                ],
            },
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "template_write_requires_compilation"
    )


def test_unknown_catalog_action_returns_structured_error(tmp_path):
    client = _client(tmp_path)
    workflow = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    workflow["rules"][0]["actions"][0]["action"]["method"] = "missing"
    (tmp_path / "demo.json").write_text(json.dumps(workflow), encoding="utf-8")

    response = client.post(
        "/templates",
        json={
            "workflow_path": "demo.json",
            "expected_version": 0,
            "template": {
                "id": "template",
                "name": "Prepare",
                "node_ids": ["prepare"],
            },
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "action_schema_not_found",
        "message": "Action schema 不存在: robot.missing",
        "detail": {
            "node_id": "prepare",
            "device_id": "robot",
            "method": "missing",
        },
    }


def test_non_object_workflow_action_params_fail_structurally(tmp_path):
    client = _client(tmp_path)
    workflow = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    workflow["rules"][0]["actions"][0]["action"]["params"] = ["invalid"]
    (tmp_path / "demo.json").write_text(json.dumps(workflow), encoding="utf-8")

    response = client.post(
        "/templates",
        json={
            "workflow_path": "demo.json",
            "expected_version": 0,
            "template": {
                "id": "template",
                "name": "Prepare",
                "node_ids": ["prepare"],
            },
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid_action_parameters"
    assert response.json()["detail"]["detail"]["node_id"] == "prepare"


def test_default_ast_catalog_compiles_real_s12_resolvers(tmp_path):
    source = Path(__file__).parents[2] / "szlab_robot_action_workflow_flow.json"
    (tmp_path / "workflow.json").write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/templates",
        json={
            "workflow_path": "workflow.json",
            "expected_version": 0,
            "template": {
                "id": "transfer",
                "name": "S072 到 S06",
                "node_ids": [
                    "node_004_pick_from_s072",
                    "node_005_place_to_s06",
                ],
            },
        },
    )

    assert response.status_code == 200
    template = response.json()["workspace"]["templates"][0]
    assert len(template["admission_gates"]) == 2
    assert template["resource_requirements"][0] == "device:szlab_mixer_robot"
