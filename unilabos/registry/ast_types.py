"""Registry AST 扫描使用的内部值类型。"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ASTCall(Mapping[str, Any]):
    """与用户 JSON 字典隔离的 AST 构造器调用。"""

    target: str
    values: Dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


def to_cache_wire(value: Any) -> Dict[str, Any]:
    """将 AST 缓存值编码为无歧义 JSON wire 节点。"""
    if isinstance(value, ASTCall):
        return {
            "type": "ast_call",
            "target": value.target,
            "values": to_cache_wire(value.values),
        }
    if isinstance(value, dict):
        return {
            "type": "dict",
            "items": [
                [to_cache_wire(key), to_cache_wire(item)]
                for key, item in value.items()
            ],
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "items": [to_cache_wire(item) for item in value],
        }
    if isinstance(value, tuple):
        return {
            "type": "tuple",
            "items": [to_cache_wire(item) for item in value],
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return {"type": "scalar", "value": value}
    raise TypeError(f"AST 缓存包含不可序列化类型: {type(value).__name__}")


def from_cache_wire(node: Any) -> Any:
    """从 JSON wire 节点严格恢复 AST 缓存值。"""
    if not isinstance(node, dict):
        raise ValueError("AST 缓存 wire 节点必须是对象")
    node_type = node.get("type")
    if node_type == "scalar":
        return node.get("value")
    if node_type == "list":
        return [from_cache_wire(item) for item in node.get("items", [])]
    if node_type == "tuple":
        return tuple(from_cache_wire(item) for item in node.get("items", []))
    if node_type == "dict":
        return {
            from_cache_wire(key): from_cache_wire(value)
            for key, value in node.get("items", [])
        }
    if node_type == "ast_call":
        values = from_cache_wire(node.get("values"))
        if not isinstance(values, dict):
            raise ValueError("ASTCall wire values 必须恢复为对象")
        target = node.get("target")
        if not isinstance(target, str):
            raise ValueError("ASTCall wire target 必须是字符串")
        return ASTCall(target=target, values=values)
    raise ValueError(f"未知 AST 缓存 wire 类型: {node_type!r}")
