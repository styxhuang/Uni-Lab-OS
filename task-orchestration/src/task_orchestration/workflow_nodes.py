"""统一提取仓库执行链支持的 workflow Action 节点。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from scripts.run_workflow_local import node_method, workflow_node_from_mapping


class WorkflowNodeInput(BaseModel):
    """供 Task 契约编译使用的规范化 workflow 节点。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    device_id: str
    method: str
    params: Any
    is_action: bool


def _normalize_node(raw: dict[str, Any]) -> WorkflowNodeInput:
    node_id = next(
        (
            value.strip()
            for field in ("workflow_node_id", "uuid", "id")
            if isinstance((value := raw.get(field)), str) and value.strip()
        ),
        "",
    )
    device_id = next(
        (
            value.strip()
            for field in ("device_id", "device_name")
            if isinstance((value := raw.get(field)), str) and value.strip()
        ),
        "",
    )
    method = next(
        (
            value.strip()
            for field in ("method", "name")
            if isinstance((value := raw.get(field)), str) and value.strip()
        ),
        "",
    )
    params = raw.get("params", raw.get("param", {}))
    if not node_id:
        raise ValueError("workflow node 必须包含非空节点 ID")
    if not device_id or not method:
        return WorkflowNodeInput(
            node_id=node_id,
            device_id=device_id,
            method=method,
            params=params,
            is_action=False,
        )
    if not isinstance(params, dict):
        return WorkflowNodeInput(
            node_id=node_id,
            device_id=device_id,
            method=method,
            params=params,
            is_action=True,
        )
    if "workflow_node_id" in raw:
        candidate = {
            "workflow_node_id": raw.get("workflow_node_id"),
            "device_id": raw.get("device_id"),
            "method": raw.get("method"),
            "params": raw.get("params", {}),
        }
    else:
        candidate = {
            "uuid": raw.get("uuid"),
            "name": raw.get("name"),
            "device_name": raw.get("device_name"),
            "param": raw.get("param", {}),
        }
    normalized = workflow_node_from_mapping(candidate)
    return WorkflowNodeInput(
        node_id=normalized.uuid,
        device_id=normalized.device_name,
        method=node_method(normalized),
        params=normalized.param,
        is_action=True,
    )


def extract_workflow_nodes(workflow: dict[str, Any]) -> list[WorkflowNodeInput]:
    """提取 workflow Action 节点并复用执行链字段规范化。"""
    root = workflow.get("data", workflow)
    if not isinstance(root, dict):
        return []
    raw_nodes = root.get("nodes")
    if isinstance(raw_nodes, list):
        return [
            _normalize_node(raw)
            for raw in raw_nodes
            if isinstance(raw, dict)
        ]

    result: list[WorkflowNodeInput] = []
    rules = root.get("rules", [])
    if not isinstance(rules, list):
        return result
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        actions = rule.get("actions", [])
        if not isinstance(actions, list):
            continue
        for item in actions:
            action = item.get("action") if isinstance(item, dict) else None
            if isinstance(action, dict):
                result.append(_normalize_node(action))
    return result
