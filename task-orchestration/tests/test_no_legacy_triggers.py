"""旧手写 Trigger 生产入口必须彻底移除。"""

from task_orchestration import models
from task_orchestration.conditions import OpcConditionProvider


def test_legacy_trigger_model_and_runtime_evaluator_are_absent():
    assert not hasattr(models, "Trigger")
    assert not hasattr(OpcConditionProvider, "evaluate")
    assert not hasattr(OpcConditionProvider, "evaluate_states")
