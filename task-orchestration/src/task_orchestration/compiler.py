"""根据 workflow Action schema 编译 Task Template 契约。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from unilabos.registry.action_contract import (
    ContractResolutionError,
    resolve_action_contract,
)

from .models import ContractCondition, ContractEffect, NodeContract


class TemplateCompilationError(ValueError):
    """模板契约无法安全编译。"""

    def __init__(self, code: str, message: str, detail: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class WorkflowActionNode(BaseModel):
    """从权威 workflow 与 Action catalog 拼合出的节点快照。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    device_id: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    action_contract: dict[str, Any] | None
    is_action: bool = True


class CompiledTemplateContract(BaseModel):
    """不含模板身份字段的纯编译结果。"""

    model_config = ConfigDict(extra="forbid")

    node_contracts: list[NodeContract]
    admission_gates: list[ContractCondition]
    resource_requirements: list[str]


def _fail(
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    **detail: Any,
) -> TemplateCompilationError:
    context = detail
    if node_id is not None:
        context = {"node_id": node_id, **context}
    return TemplateCompilationError(code, message, context)


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _condition(payload: object, node_id: str) -> ContractCondition:
    if not isinstance(payload, Mapping):
        raise _fail(
            "invalid_action_contract",
            "Action 前置条件必须是对象",
            node_id=node_id,
        )
    operator = payload.get("operator", "eq")
    if operator != "eq":
        raise _fail(
            "unsupported_condition_operator",
            "本阶段仅支持 eq 条件",
            node_id=node_id,
            operator=operator,
        )
    try:
        return ContractCondition.model_validate(
            {
                "source": payload.get("source", "opc"),
                "variable": payload.get("variable"),
                "operator": operator,
                "expected": payload.get("expected"),
            }
        )
    except ValueError as exc:
        raise _fail(
            "invalid_action_contract",
            "Action 前置条件结构非法",
            node_id=node_id,
            reason=str(exc),
        ) from exc


def _effect(payload: object, node_id: str) -> ContractEffect:
    if not isinstance(payload, Mapping):
        raise _fail(
            "invalid_action_contract",
            "Action effect 必须是对象",
            node_id=node_id,
        )
    try:
        return ContractEffect.model_validate(
            {
                "source": payload.get("source", "opc"),
                "variable": payload.get("variable"),
                "expected": payload.get("expected"),
            }
        )
    except ValueError as exc:
        raise _fail(
            "invalid_action_contract",
            "Action effect 结构非法",
            node_id=node_id,
            reason=str(exc),
        ) from exc


def _resources(payload: object, node_id: str) -> list[str]:
    if not isinstance(payload, list):
        raise _fail(
            "invalid_action_contract",
            "physical_resources 必须是列表",
            node_id=node_id,
        )
    result: list[str] = []
    for item in payload:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("resource_id"), str)
            or not item["resource_id"]
        ):
            raise _fail(
                "invalid_action_contract",
                "物理 resource id 必须是非空字符串",
                node_id=node_id,
            )
        if item["resource_id"] not in result:
            result.append(item["resource_id"])
    return result


def compile_template_contract(
    node_ids: Sequence[str],
    workflow_nodes: Sequence[WorkflowActionNode],
) -> CompiledTemplateContract:
    """按节点顺序解析契约、消解因果关系并汇总资源。

    返回值仅用于模板持久化；本函数不读取快照，也不执行运行时准入。
    """
    ordered_ids = list(node_ids)
    if len(ordered_ids) != len(set(ordered_ids)):
        raise _fail("duplicate_node_id", "模板 node_id 不得重复")

    nodes_by_id = {node.node_id: node for node in workflow_nodes}
    node_contracts: list[NodeContract] = []
    admission_gates: list[ContractCondition] = []
    admission_keys: set[tuple[str, str, str, str]] = set()
    resource_requirements: list[str] = []
    established: dict[tuple[str, str], Any] = {}

    for node_id in ordered_ids:
        node = nodes_by_id.get(node_id)
        if node is None:
            raise _fail(
                "workflow_node_not_found",
                "workflow 中不存在模板节点",
                node_id=node_id,
            )
        if not node.is_action or not node.device_id or not node.method:
            raise _fail(
                "workflow_node_has_no_action",
                "workflow 节点没有可执行 Action",
                node_id=node_id,
            )
        if node.action_contract is None:
            if node.device_id == "szlab_mixer_robot":
                raise _fail(
                    "required_action_contract_missing",
                    "可调度 S12 Action 缺少契约",
                    node_id=node_id,
                    device_id=node.device_id,
                    method=node.method,
                )
            node_contracts.append(
                NodeContract(
                    node_id=node_id,
                    contract_state="empty",
                    preconditions=[],
                    effects=[],
                    resources=[],
                )
            )
            continue

        try:
            resolved = resolve_action_contract(node.action_contract, node.params)
        except ContractResolutionError as exc:
            raise _fail(
                "contract_resolution_failed",
                "Action contract 解析失败",
                node_id=node_id,
                reason=str(exc),
            ) from exc

        conditions_payload = resolved.get("opc_conditions")
        effects_payload = resolved.get("effects")
        resources_payload = resolved.get("physical_resources")
        if not isinstance(conditions_payload, list) or not isinstance(effects_payload, list):
            raise _fail(
                "invalid_action_contract",
                "Action contract 条件和 effect 必须是列表",
                node_id=node_id,
            )
        preconditions = [_condition(item, node_id) for item in conditions_payload]
        effects = [_effect(item, node_id) for item in effects_payload]
        resources = _resources(resources_payload, node_id)

        for condition in preconditions:
            state_key = (condition.source, condition.variable)
            if state_key in established:
                current = established[state_key]
                if _json_key(current) == _json_key(condition.expected):
                    continue
                raise _fail(
                    "contradictory_precondition",
                    "前序 effect 与节点前置条件矛盾",
                    node_id=node_id,
                    source=condition.source,
                    variable=condition.variable,
                    expected=condition.expected,
                    established=current,
                )
            admission_key = (
                condition.source,
                condition.variable,
                condition.operator,
                _json_key(condition.expected),
            )
            if admission_key not in admission_keys:
                admission_keys.add(admission_key)
                admission_gates.append(condition)

        for effect in effects:
            established[(effect.source, effect.variable)] = effect.expected
        for resource in resources:
            if resource not in resource_requirements:
                resource_requirements.append(resource)
        node_contracts.append(
            NodeContract(
                node_id=node_id,
                contract_state="resolved",
                preconditions=preconditions,
                effects=effects,
                resources=resources,
            )
        )

    return CompiledTemplateContract(
        node_contracts=node_contracts,
        admission_gates=admission_gates,
        resource_requirements=resource_requirements,
    )
