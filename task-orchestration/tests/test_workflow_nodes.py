"""权威 workflow Action 节点提取测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from task_orchestration.workflow_nodes import extract_workflow_nodes


def test_extracts_rule_action_nodes_with_canonical_fields():
    nodes = extract_workflow_nodes(
        {
            "rules": [
                {
                    "actions": [
                        {
                            "action": {
                                "workflow_node_id": "rule-node",
                                "device_id": " robot ",
                                "method": " run ",
                                "params": {"amount": 2},
                                "index": 1,
                                "node": "展示名",
                            }
                        }
                    ]
                }
            ]
        }
    )

    assert [node.model_dump() for node in nodes] == [
        {
            "node_id": "rule-node",
            "device_id": "robot",
            "method": "run",
            "params": {"amount": 2},
            "is_action": True,
        }
    ]


def test_extracts_top_level_nodes_with_same_normalization():
    nodes = extract_workflow_nodes(
        {
            "nodes": [
                {
                    "workflow_node_id": "top-node",
                    "device_id": " robot ",
                    "method": " run ",
                    "params": {"amount": 3},
                }
            ]
        }
    )

    assert [node.model_dump() for node in nodes] == [
        {
            "node_id": "top-node",
            "device_id": "robot",
            "method": "run",
            "params": {"amount": 3},
            "is_action": True,
        }
    ]


def test_extracts_data_nodes_with_same_normalization():
    nodes = extract_workflow_nodes(
        {
            "data": {
                "nodes": [
                    {
                        "workflow_node_id": "data-node",
                        "device_id": " robot ",
                        "method": " run ",
                        "params": {"amount": 4},
                    }
                ]
            }
        }
    )

    assert [node.model_dump() for node in nodes] == [
        {
            "node_id": "data-node",
            "device_id": "robot",
            "method": "run",
            "params": {"amount": 4},
            "is_action": True,
        }
    ]


@pytest.mark.parametrize("container", ["rules", "nodes", "data.nodes"])
def test_real_s07_legacy_nodes_use_execution_chain_method(container):
    fixture_path = (
        Path(__file__).parents[2]
        / "tests/szlab_poly_studio/example/s07_robot_workflow.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    legacy_nodes = fixture["nodes"]
    if container == "rules":
        workflow = {
            "rules": [
                {"actions": [{"action": node} for node in legacy_nodes]}
            ]
        }
    elif container == "data.nodes":
        workflow = {"data": {"nodes": legacy_nodes}}
    else:
        workflow = fixture

    nodes = extract_workflow_nodes(workflow)

    assert [node.method for node in nodes] == [
        "submit_place_to_s071",
        "submit_place_to_s072",
        "submit_pick_from_s072",
    ]
