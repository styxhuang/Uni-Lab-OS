"""声明式 ActionContract 的安全解析与执行工具。"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from threading import RLock
from typing import Any

JsonResult = str | int | float | bool | None | list["JsonResult"] | dict[str, "JsonResult"]
ContractResolver = Callable[..., JsonResult]


class ContractResolutionError(ValueError):
    """契约引用无法安全解析。"""


_RESOLVERS: dict[str, ContractResolver] = {}
_RESOLVER_LOCK = RLock()


def register_contract_resolver(
    resolver_id: str,
    resolver: ContractResolver,
    *,
    replace: bool = False,
) -> None:
    """显式注册纯函数解析器；不接受 import string 或其他动态加载方式。"""
    if not isinstance(resolver_id, str) or not resolver_id:
        raise ValueError("resolver_id 必须是非空字符串")
    if not callable(resolver):
        raise TypeError("resolver 必须是 callable")
    with _RESOLVER_LOCK:
        if resolver_id in _RESOLVERS and not replace:
            raise ValueError(f"resolver_id 已注册: {resolver_id}")
        _RESOLVERS[resolver_id] = resolver


def _parameter_value(reference: Mapping[str, Any], parameters: Mapping[str, Any]) -> Any:
    parameter = reference.get("parameter")
    if not isinstance(parameter, str) or parameter not in parameters:
        raise ContractResolutionError(f"缺少 Action 参数: {parameter!r}")
    value = parameters[parameter]
    path = reference.get("path", [])
    if not isinstance(path, list):
        raise ContractResolutionError("参数 path 必须是列表")
    for segment in path:
        try:
            if isinstance(segment, int) and isinstance(value, (list, tuple)):
                value = value[segment]
            elif isinstance(segment, str) and isinstance(value, Mapping):
                value = value[segment]
            else:
                raise KeyError(segment)
        except (IndexError, KeyError, TypeError) as exc:
            raise ContractResolutionError(
                f"Action 参数路径不存在: {parameter!r} {path!r}"
            ) from exc
    return value


def _ensure_json_result(resolver_id: str, value: Any) -> JsonResult:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContractResolutionError(
            f"Resolver {resolver_id!r} 返回非法结果: {type(value).__name__}"
        ) from exc
    return value


def resolve_contract_value(value: Any, parameters: Mapping[str, Any]) -> Any:
    """递归解析参数绑定和白名单 resolver。"""
    if isinstance(value, list):
        return [resolve_contract_value(item, parameters) for item in value]
    if not isinstance(value, Mapping):
        return value
    kind = value.get("kind")
    if kind == "parameter":
        return _parameter_value(value, parameters)
    if kind == "constant":
        return value.get("value")
    if kind == "resolver":
        resolver_id = value.get("resolver_id")
        if not isinstance(resolver_id, str):
            raise ContractResolutionError("Resolver ID 必须是字符串")
        with _RESOLVER_LOCK:
            resolver = _RESOLVERS.get(resolver_id)
        if resolver is None:
            raise ContractResolutionError(f"Resolver ID 未注册: {resolver_id}")
        raw_arguments = value.get("arguments", {})
        if not isinstance(raw_arguments, Mapping):
            raise ContractResolutionError("Resolver arguments 必须是对象")
        arguments = {
            name: resolve_contract_value(binding, parameters)
            for name, binding in raw_arguments.items()
        }
        try:
            inspect.signature(resolver).bind(**arguments)
            result = resolver(**arguments)
        except TypeError as exc:
            raise ContractResolutionError(
                f"Resolver {resolver_id!r} 参数错误: {exc}"
            ) from exc
        except (ValueError, KeyError) as exc:
            raise ContractResolutionError(
                f"Resolver {resolver_id!r} 解析失败: {exc}"
            ) from exc
        return _ensure_json_result(resolver_id, result)
    return {
        str(key): resolve_contract_value(item, parameters)
        for key, item in value.items()
    }


def resolve_action_contract(
    contract: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """将 wire contract 按本次 Action 参数解析为具体变量和资源。"""
    if not isinstance(contract, Mapping):
        raise ContractResolutionError("Action contract 必须是对象")
    resolved = resolve_contract_value(contract, parameters)
    if not isinstance(resolved, dict):
        raise ContractResolutionError("Action contract 解析结果必须是对象")
    return resolved
