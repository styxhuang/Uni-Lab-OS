from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from unilabos.registry.ast_registry_scanner import (
    _parse_file,
    load_scan_cache,
    save_scan_cache,
    scan_directory,
)
from unilabos.registry.ast_types import ASTCall
from unilabos.registry.decorators import (
    ActionContract,
    ActionEffect,
    OPCCondition,
    ParameterResolver,
    PhysicalResource,
    action,
    get_action_meta,
)
from unilabos.registry.registry import Registry


def _contract() -> ActionContract:
    from unilabos.registry.decorators import ParameterBinding

    return ActionContract(
        opc_conditions=[
            OPCCondition(
                variable=ParameterResolver(
                    resolver_id="station_ready_variable",
                    arguments={
                        "station": ParameterBinding(parameter="station"),
                    },
                ),
                operator="eq",
                expected=True,
            )
        ],
        effects=[
            ActionEffect(
                variable="station_state",
                expected=ParameterBinding(parameter="target_state"),
            )
        ],
        physical_resources=[
            PhysicalResource(
                resource_id=ParameterBinding(parameter="resource_id"),
            )
        ],
    )


def test_action_contract_is_strict_and_json_serializable():
    payload = _contract().model_dump(mode="json")

    assert json.loads(json.dumps(payload)) == {
        "opc_conditions": [
            {
                "source": "opc",
                "variable": {
                    "kind": "resolver",
                    "resolver_id": "station_ready_variable",
                    "arguments": {
                        "station": {
                            "kind": "parameter",
                            "parameter": "station",
                            "path": [],
                        },
                    },
                },
                "operator": "eq",
                "expected": True,
            }
        ],
        "effects": [
            {
                "source": "opc",
                "variable": "station_state",
                "expected": {
                    "kind": "parameter",
                    "parameter": "target_state",
                    "path": [],
                },
            }
        ],
        "physical_resources": [
            {
                "resource_id": {
                    "kind": "parameter",
                    "parameter": "resource_id",
                    "path": [],
                },
            }
        ],
    }

    with pytest.raises(ValidationError):
        ParameterResolver(
            resolver_id="unsafe",
            arguments={"value": lambda value: value},
        )

    with pytest.raises(ValidationError):
        ActionEffect(variable="station_state", expected=lambda: "ready")

    with pytest.raises(ValidationError):
        OPCCondition(variable="ready", operator="ne", expected=True)


def test_action_decorator_exports_contract_metadata_and_allows_no_contract():
    contract = _contract()

    @action(contract=contract)
    def run(resource_id: str, target_state: str) -> None:
        pass

    @action()
    def plain() -> None:
        pass

    assert get_action_meta(run)["contract"] == contract.model_dump(mode="json")
    assert get_action_meta(plain)["contract"] is None


def test_ast_and_registry_export_same_action_contract(tmp_path):
    source = '''
from unilabos.registry.decorators import (
    ActionContract,
    ActionEffect,
    ParameterBinding,
    OPCCondition,
    ParameterResolver,
    PhysicalResource,
    action,
    device,
)

@device(id="contract_device", category=["test"])
class ContractDevice:
    @action(
        contract=ActionContract(
            opc_conditions=[
                OPCCondition(
                    variable=ParameterResolver(
                        resolver_id="station_ready_variable",
                        arguments={
                            "station": ParameterBinding(parameter="station"),
                        },
                    ),
                    operator="eq",
                    expected=True,
                ),
            ],
            effects=[
                ActionEffect(
                    variable="station_state",
                    expected=ParameterBinding(parameter="target_state"),
                ),
            ],
            physical_resources=[
                PhysicalResource(
                    resource_id=ParameterBinding(parameter="resource_id"),
                ),
            ],
        ),
    )
    def run(self, station: dict, target_state: str, resource_id: str) -> None:
        pass
'''
    module_file = tmp_path / "contract_device.py"
    module_file.write_text(source, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=1) as executor:
        scanned = scan_directory(
            tmp_path,
            python_path=tmp_path,
            executor=executor,
        )

    ast_meta = scanned["devices"]["contract_device"]
    ast_contract = ast_meta["actions"]["run"]["action_args"]["contract"]
    entry = Registry()._build_device_entry_from_ast("contract_device", ast_meta)
    registry_contract = entry["class"]["action_value_mappings"]["run"]["contract"]

    assert ast_contract == _contract().model_dump(mode="json")
    assert registry_contract == ast_contract


def test_ast_preserves_user_json_call_key_without_collision(tmp_path):
    literal = {
        "_call": "user-json-value",
        "nested": {"_call": "nested-user-value"},
    }
    runtime_contract = ActionContract(
        effects=[ActionEffect(variable="state", expected=literal)]
    ).model_dump(mode="json")
    source = '''
from unilabos.registry.decorators import ActionContract, ActionEffect, action, device

@device(id="call_key_device", category=["test"])
class CallKeyDevice:
    @action(
        contract=ActionContract(
            effects=[
                ActionEffect(
                    variable="state",
                    expected={
                        "_call": "user-json-value",
                        "nested": {"_call": "nested-user-value"},
                    },
                ),
            ],
        ),
    )
    def run(self) -> None:
        pass
'''
    module_file = tmp_path / "call_key_device.py"
    module_file.write_text(source, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=1) as executor:
        scanned = scan_directory(
            tmp_path,
            python_path=tmp_path,
            executor=executor,
        )

    ast_contract = scanned["devices"]["call_key_device"]["actions"]["run"][
        "action_args"
    ]["contract"]
    assert ast_contract == runtime_contract
    assert ast_contract["effects"][0]["expected"] == literal


def test_ast_contract_rejects_callable_reference(tmp_path):
    source = '''
from unilabos.registry.decorators import ActionContract, ActionEffect, action, device

def resolve_state():
    return "ready"

@device(id="unsafe_contract_device", category=["test"])
class UnsafeContractDevice:
    @action(
        contract=ActionContract(
            effects=[
                ActionEffect(variable="state", expected=resolve_state),
            ],
        ),
    )
    def run(self) -> None:
        pass
'''
    module_file = tmp_path / "unsafe_contract_device.py"
    module_file.write_text(source, encoding="utf-8")

    with pytest.raises(ValueError, match="不允许 callable"):
        _parse_file(module_file, tmp_path)


def test_scan_directory_supports_contract_constructor_aliases(tmp_path):
    source = '''
from unilabos.registry.decorators import (
    ActionContract as AC,
    ActionEffect as AE,
    action,
    device,
)

@device(id="alias_contract_device", category=["test"])
class AliasContractDevice:
    @action(contract=AC(effects=[AE(variable="state", expected="ready")]))
    def run(self) -> None:
        pass
'''
    module_file = tmp_path / "alias_contract_device.py"
    module_file.write_text(source, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=1) as executor:
        scanned = scan_directory(
            tmp_path,
            python_path=tmp_path,
            executor=executor,
        )

    assert scanned["devices"]["alias_contract_device"]["actions"]["run"][
        "action_args"
    ]["contract"]["effects"] == [
        {"source": "opc", "variable": "state", "expected": "ready"}
    ]


def test_ast_recognizes_registry_action_import_alias(tmp_path):
    source = '''
from unilabos.registry.decorators import (
    ActionContract,
    ActionEffect,
    action as act,
    device,
)

@device(id="action_alias_device", category=["test"])
class ActionAliasDevice:
    @act(
        contract=ActionContract(
            effects=[ActionEffect(variable="state", expected="ready")],
        ),
    )
    def run(self) -> None:
        pass
'''
    module_file = tmp_path / "action_alias_device.py"
    module_file.write_text(source, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=1) as executor:
        scanned = scan_directory(
            tmp_path,
            python_path=tmp_path,
            executor=executor,
        )

    device_meta = scanned["devices"]["action_alias_device"]
    assert "run" not in device_meta["auto_methods"]
    assert device_meta["actions"]["run"]["action_args"]["contract"]["effects"] == [
        {"source": "opc", "variable": "state", "expected": "ready"}
    ]


def test_ast_does_not_recognize_foreign_action_import_alias(tmp_path):
    (tmp_path / "foreign_decorators.py").write_text(
        "def action(**kwargs):\n"
        "    def decorate(func):\n"
        "        return func\n"
        "    return decorate\n",
        encoding="utf-8",
    )
    source = '''
from foreign_decorators import action as act
from unilabos.registry.decorators import device

@device(id="foreign_action_alias_device", category=["test"])
class ForeignActionAliasDevice:
    @act(label="not-a-registry-action")
    def run(self) -> None:
        pass
'''
    module_file = tmp_path / "foreign_action_alias_device.py"
    module_file.write_text(source, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=1) as executor:
        scanned = scan_directory(
            tmp_path,
            python_path=tmp_path,
            executor=executor,
        )

    device_meta = scanned["devices"]["foreign_action_alias_device"]
    assert "run" not in device_meta["actions"]
    assert "run" in device_meta["auto_methods"]


def test_ast_rejects_all_registry_decorators_from_evil_module(tmp_path):
    evil_source = '''
from unilabos.registry.decorators_evil import (
    device,
    resource,
)

@device(id="evil_device", category=["test"])
class EvilDevice:
    pass

@resource(id="evil_resource", category=["test"])
class EvilResource:
    pass
'''
    real_source = '''
from unilabos.registry.decorators import device
from unilabos.registry.decorators_evil import always_free, not_action, topic_config

@device(id="real_device", category=["test"])
class RealDevice:
    @topic_config()
    def observe(self) -> str:
        return "value"

    @not_action
    def hidden(self) -> None:
        pass

    @always_free
    def run(self) -> None:
        pass
'''
    (tmp_path / "evil_registry_decorators.py").write_text(
        evil_source,
        encoding="utf-8",
    )
    (tmp_path / "real_device_with_evil_methods.py").write_text(
        real_source,
        encoding="utf-8",
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        scanned = scan_directory(
            tmp_path,
            python_path=tmp_path,
            executor=executor,
        )

    assert set(scanned["devices"]) == {"real_device"}
    assert scanned["resources"] == {}
    real_meta = scanned["devices"]["real_device"]
    assert "observe" in real_meta["auto_methods"]
    assert "hidden" in real_meta["auto_methods"]
    assert real_meta["auto_methods"]["run"].get("always_free") is None


def test_scan_directory_reports_invalid_contract(tmp_path):
    source = '''
from unilabos.registry.decorators import ActionContract, ActionEffect, action, device

def unsafe_value():
    return "ready"

@device(id="invalid_contract_device", category=["test"])
class InvalidContractDevice:
    @action(
        contract=ActionContract(
            effects=[ActionEffect(variable="state", expected=unsafe_value)],
        ),
    )
    def run(self) -> None:
        pass
'''
    module_file = tmp_path / "invalid_contract_device.py"
    module_file.write_text(source, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError, match="invalid_contract_device.py"):
            scan_directory(
                tmp_path,
                python_path=tmp_path,
                executor=executor,
            )


def test_scan_directory_can_return_visible_errors_without_raising(tmp_path):
    module_file = tmp_path / "broken_contract_device.py"
    module_file.write_text(
        "from unilabos.registry.decorators import action\n"
        "class Broken:\n"
        "    @action(contract=\n",
        encoding="utf-8",
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        scanned = scan_directory(
            tmp_path,
            python_path=tmp_path,
            executor=executor,
            raise_on_error=False,
        )

    assert scanned["devices"] == {}
    assert len(scanned["_errors"]) == 1
    assert "broken_contract_device.py" in scanned["_errors"][0]


def test_json_scan_cache_roundtrips_ast_call(tmp_path):
    source = '''
from unilabos.registry.decorators import InputHandle, device

@device(
    id="cache_ast_call_device",
    category=["test"],
    handles=[
        InputHandle(key="input", data_type="fluid", label="Input"),
    ],
)
class CacheASTCallDevice:
    pass
'''
    module_file = tmp_path / "cache_ast_call_device.py"
    module_file.write_text(source, encoding="utf-8")
    cache = load_scan_cache(None)

    with ThreadPoolExecutor(max_workers=1) as executor:
        scan_directory(
            tmp_path,
            python_path=tmp_path,
            executor=executor,
            cache=cache,
        )

    cached_handle = next(iter(cache["files"].values()))["devices"][0][
        "handles"
    ][0]
    assert isinstance(cached_handle, ASTCall)

    cache_path = tmp_path / "ast_scan_cache.json"
    save_scan_cache(cache_path, cache)
    loaded = load_scan_cache(cache_path)
    loaded_handle = next(iter(loaded["files"].values()))["devices"][0][
        "handles"
    ][0]

    assert isinstance(loaded_handle, ASTCall)
    assert loaded_handle == cached_handle


def test_json_scan_cache_write_failure_is_visible(tmp_path):
    cache_path = tmp_path / "occupied"
    cache_path.mkdir()

    with pytest.raises(OSError):
        save_scan_cache(cache_path, load_scan_cache(None))


def test_ast_contract_rejects_constructor_alias_from_other_module(tmp_path):
    (tmp_path / "foreign_models.py").write_text(
        "class ActionContract:\n"
        "    def __init__(self, **kwargs):\n"
        "        self.kwargs = kwargs\n",
        encoding="utf-8",
    )
    source = '''
from foreign_models import ActionContract as AC
from unilabos.registry.decorators import action, device

@device(id="foreign_contract_device", category=["test"])
class ForeignContractDevice:
    @action(contract=AC())
    def run(self) -> None:
        pass
'''
    module_file = tmp_path / "foreign_contract_device.py"
    module_file.write_text(source, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError, match="不允许 callable"):
            scan_directory(
                tmp_path,
                python_path=tmp_path,
                executor=executor,
            )


def test_ast_contract_rejects_decorators_evil_module_prefix(tmp_path):
    source = '''
from unilabos.registry.decorators_evil import ActionContract as AC
from unilabos.registry.decorators import action, device

@device(id="evil_prefix_contract_device", category=["test"])
class EvilPrefixContractDevice:
    @action(contract=AC())
    def run(self) -> None:
        pass
'''
    module_file = tmp_path / "evil_prefix_contract_device.py"
    module_file.write_text(source, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError, match="不允许 callable"):
            scan_directory(
                tmp_path,
                python_path=tmp_path,
                executor=executor,
            )


def test_ast_contract_rejects_non_json_set_literal(tmp_path):
    source = '''
from unilabos.registry.decorators import ActionContract, ActionEffect, action, device

@device(id="set_contract_device", category=["test"])
class SetContractDevice:
    @action(
        contract=ActionContract(
            effects=[ActionEffect(variable="state", expected={"ready"})],
        ),
    )
    def run(self) -> None:
        pass
'''
    module_file = tmp_path / "set_contract_device.py"
    module_file.write_text(source, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError, match="JSON 字面量"):
            scan_directory(
                tmp_path,
                python_path=tmp_path,
                executor=executor,
            )


def test_complete_registry_keeps_auto_prefixed_contract(tmp_path, monkeypatch):
    source = '''
from unilabos.registry.decorators import ActionContract, ActionEffect, action, device

@device(id="complete_contract_device", category=["test"])
class CompleteContractDevice:
    @action(
        auto_prefix=True,
        contract=ActionContract(
            effects=[ActionEffect(variable="state", expected="ready")],
        ),
    )
    def run(self) -> None:
        pass
'''
    module_file = tmp_path / "complete_contract_device.py"
    module_file.write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    yaml_file = tmp_path / "complete_contract_device.yaml"
    yaml_file.write_text(
        """
complete_contract_device:
  category: [test]
  class:
    module: complete_contract_device:CompleteContractDevice
    type: python
    status_types: {}
    action_value_mappings: {}
""",
        encoding="utf-8",
    )

    registry = Registry()
    registry.device_type_registry = {}
    _, complete_data, is_valid, _ = registry._load_single_device_file(
        yaml_file,
        complete_registry=True,
    )

    assert is_valid is True
    action_entry = complete_data["complete_contract_device"]["class"][
        "action_value_mappings"
    ]["auto-run"]
    assert action_entry["contract"]["effects"] == [
        {"source": "opc", "variable": "state", "expected": "ready"}
    ]


def test_complete_registry_preserves_explicit_action_entry_metadata(
    tmp_path,
    monkeypatch,
):
    source = '''
from unilabos.registry.decorators import ActionContract, ActionEffect, action, device

@device(id="preserved_action_device", category=["test"])
class PreservedActionDevice:
    @action(
        goal={"request": "payload"},
        feedback={"progress": "progress"},
        result={"success": "success"},
        goal_default={"request": "default"},
        placeholder_keys={"request": "unilabos_resources"},
        feedback_interval=2.5,
        contract=ActionContract(
            effects=[ActionEffect(variable="state", expected="ready")],
        ),
    )
    def run(self, payload: str = "method-default") -> None:
        pass
'''
    module_file = tmp_path / "preserved_action_device.py"
    module_file.write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    yaml_file = tmp_path / "preserved_action_device.yaml"
    yaml_file.write_text(
        """
preserved_action_device:
  category: [test]
  class:
    module: preserved_action_device:PreservedActionDevice
    type: python
    status_types: {}
    action_value_mappings:
      run:
        type: UniLabJsonCommandAsync
        goal: {request: payload}
        feedback: {progress: progress}
        result: {success: success}
        schema: {description: preserved schema}
        goal_default: {request: default}
        handles:
          input:
            - {handler_key: sample, data_type: resource, label: Sample}
        placeholder_keys: {request: unilabos_resources}
        feedback_interval: 2.5
        node_type: manual_confirm
        always_free: true
        extension_field: preserved
""",
        encoding="utf-8",
    )

    registry = Registry()
    registry.device_type_registry = {}
    _, complete_data, is_valid, _ = registry._load_single_device_file(
        yaml_file,
        complete_registry=True,
    )

    assert is_valid is True
    action_entry = complete_data["preserved_action_device"]["class"][
        "action_value_mappings"
    ]["run"]
    assert action_entry["type"] == "UniLabJsonCommandAsync"
    assert action_entry["goal"] == {"request": "payload"}
    assert action_entry["feedback"] == {"progress": "progress"}
    assert action_entry["result"] == {"success": "success"}
    assert action_entry["goal_default"] == {"request": "default"}
    assert action_entry["handles"]["input"][0]["handler_key"] == "sample"
    assert action_entry["placeholder_keys"] == {
        "request": "unilabos_resources"
    }
    assert action_entry["feedback_interval"] == 2.5
    assert action_entry["node_type"] == "manual_confirm"
    assert action_entry["always_free"] is True
    assert action_entry["extension_field"] == "preserved"
    assert action_entry["contract"]["effects"] == [
        {"source": "opc", "variable": "state", "expected": "ready"}
    ]


def test_complete_registry_builds_full_action_metadata_without_old_entry(
    tmp_path,
    monkeypatch,
):
    source = '''
from unilabos.registry.decorators import (
    ActionContract,
    ActionEffect,
    ActionInputHandle,
    NodeType,
    action,
    device,
)

@device(id="new_action_metadata_device", category=["test"])
class NewActionMetadataDevice:
    @action(
        goal={"request": "payload"},
        feedback={"progress": "progress"},
        result={"success": "success"},
        goal_default={"request": "default"},
        handles=[
            ActionInputHandle(
                key="sample",
                data_type="resource",
                label="Sample",
            ),
        ],
        placeholder_keys={"request": "unilabos_resources"},
        feedback_interval=2.5,
        node_type=NodeType.MANUAL_CONFIRM,
        always_free=True,
        description="完整动作元数据",
        contract=ActionContract(
            effects=[ActionEffect(variable="state", expected="ready")],
        ),
    )
    def run(self, payload: str = "method-default") -> None:
        pass
'''
    module_file = tmp_path / "new_action_metadata_device.py"
    module_file.write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    yaml_file = tmp_path / "new_action_metadata_device.yaml"
    yaml_file.write_text(
        """
new_action_metadata_device:
  category: [test]
  class:
    module: new_action_metadata_device:NewActionMetadataDevice
    type: python
    status_types: {}
    action_value_mappings: {}
""",
        encoding="utf-8",
    )

    registry = Registry()
    registry.device_type_registry = {}
    _, complete_data, is_valid, _ = registry._load_single_device_file(
        yaml_file,
        complete_registry=True,
    )

    assert is_valid is True
    action_entry = complete_data["new_action_metadata_device"]["class"][
        "action_value_mappings"
    ]["run"]
    assert action_entry["type"] == "UniLabJsonCommand"
    assert action_entry["goal"] == {"request": "payload"}
    assert action_entry["feedback"] == {"progress": "progress"}
    assert action_entry["result"] == {"success": "success"}
    assert action_entry["goal_default"]["request"] == "default"
    assert action_entry["handles"]["input"][0] == {
        "handler_key": "sample",
        "data_type": "resource",
        "label": "Sample",
        "io_type": "source",
    }
    assert action_entry["placeholder_keys"] == {
        "request": "unilabos_resources"
    }
    assert action_entry["feedback_interval"] == 2.5
    assert action_entry["node_type"] == "manual_confirm"
    assert action_entry["always_free"] is True
    assert action_entry["schema"]["description"] == "完整动作元数据"
    assert action_entry["contract"]["effects"] == [
        {"source": "opc", "variable": "state", "expected": "ready"}
    ]


def test_parameter_resolver_serializes_multi_parameter_bindings():
    from unilabos.registry.decorators import ConstantBinding, ParameterBinding

    resolver = ParameterResolver(
        resolver_id="product_slot_sensor",
        arguments={
            "product_type": ParameterBinding(parameter="product_type"),
            "position": ParameterBinding(parameter="position"),
            "used": ConstantBinding(value=False),
        },
    )

    payload = resolver.model_dump(mode="json")
    assert json.loads(json.dumps(payload, allow_nan=False)) == {
        "kind": "resolver",
        "resolver_id": "product_slot_sensor",
        "arguments": {
            "product_type": {
                "kind": "parameter",
                "parameter": "product_type",
                "path": [],
            },
            "position": {
                "kind": "parameter",
                "parameter": "position",
                "path": [],
            },
            "used": {"kind": "constant", "value": False},
        },
    }


def test_contract_references_are_explicit_and_json_objects_remain_literal():
    from unilabos.registry.decorators import ConstantBinding, ParameterBinding

    literal = {
        "parameter": "ordinary-json",
        "resolver_id": "also-ordinary-json",
    }
    effect = ActionEffect(variable="state", expected=literal)
    resolver = ParameterResolver(
        resolver_id="lookup",
        arguments={
            "key": ParameterBinding(parameter="key"),
            "table": ConstantBinding(value=literal),
        },
    )

    assert effect.model_dump(mode="json")["expected"] == literal
    assert resolver.model_dump(mode="json") == {
        "kind": "resolver",
        "resolver_id": "lookup",
        "arguments": {
            "key": {
                "kind": "parameter",
                "parameter": "key",
                "path": [],
            },
            "table": {
                "kind": "constant",
                "value": literal,
            },
        },
    }


@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_action_contract_rejects_non_standard_json_numbers(non_finite):
    with pytest.raises(ValidationError):
        ActionContract(
            effects=[
                ActionEffect(variable="state", expected=non_finite),
            ]
        )
