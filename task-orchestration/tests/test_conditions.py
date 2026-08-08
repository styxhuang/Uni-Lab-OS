"""OPC 快照状态管理测试；本阶段不执行 admission 判定。"""

from task_orchestration.conditions import OpcConditionProvider


def test_snapshot_updates_merge_and_reject_stale_sequences():
    provider = OpcConditionProvider(clock=lambda: 100.0)

    assert provider.update("demo.json", "line-1", 1, {"ready": True})
    assert provider.update("demo.json", "line-1", 2, {"temperature": 25})
    assert not provider.update("demo.json", "line-1", 2, {"ready": False})

    assert provider.export_state("demo.json", "line-1") == {
        "plc_device_id": "line-1",
        "sequence": 2,
        "values": {"ready": True, "temperature": 25},
        "updated_at_by_variable": {
            "ready": 100.0,
            "temperature": 100.0,
        },
    }


def test_snapshot_state_can_restore_and_clear_without_runtime_evaluation():
    provider = OpcConditionProvider()
    provider.restore_state(
        "demo.json",
        {
            "plc_device_id": "line-1",
            "sequence": 3,
            "values": {"ready": True},
            "updated_at_by_variable": {"ready": 10.0},
        },
    )

    assert not provider.can_update("demo.json", "line-1", 3)
    provider.clear_workflow("demo.json")
    assert provider.export_state("demo.json", "line-1") is None
