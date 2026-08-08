# Task Orchestration Sensor Gate 检查问题

## 问题描述

当前排程系统存在**两套 sensor gate 检查机制**，导致执行状态混乱：

1. **Task 级别检查**：在 Task 启动时检查 `input_triggers`（OPC 条件）
2. **Action 级别检查**：在每个节点执行前检查 sensor gate（设备驱动内部）

**问题**：Task 级别检查粒度太粗，只覆盖 Task 启动条件，不覆盖节点间条件。Action 级别检查在执行时才进行，但此时 cursor 已经推进，无法回退，导致状态混乱。

## 具体例子

### 场景：从 S072 取烧杯放到 S06

```
Task: [从S072取] → [放到S06]
```

**当前执行流程**：

1. Task 启动 → Task 级别检查通过（S072有料）
2. `claim_action("从S072取")` → 设备驱动检查 S072有料 ✅ → 执行成功 → cursor=1
3. `claim_action("放到S06")` → 设备驱动检查 S06为空 ❌ → **阻塞等待**
   - 但此时物料已在机械臂手上，cursor=1
   - 无法放回 S072（cursor 已推进）
   - 无法放到 S06（条件不满足）
   - **状态混乱**

### 问题根因

| 层级 | 检查时机 | 检查内容 | 问题 |
|------|---------|---------|------|
| **Task 级别** | Task 启动时（`plan()` 评估 `input_triggers`） | 只检查 Task 的启动条件 | 不检查节点间的条件 |
| **Action 级别** | 每个节点执行前（`_wait_sensor_conditions`） | 检查 sensor gate | 执行时才阻塞，状态已混乱 |

**根本原因**：
- Task 模板的 `input_triggers` 只定义 Task 启动条件，不定义节点间条件
- 设备驱动的 sensor gate 检查是阻塞式的，在执行时才进行
- `claim_action` 方法不检查 OPC 条件，直接允许设备认领动作

## 解决方案

### 方案 1：在 `claim_action` 时检查节点级条件（推荐）

把 Action 级别的 sensor gate 提升到 Task 级别，让 task-orchestration 在 `claim_action` 时就能检查节点间条件：

```python
def claim_action(self, workflow_path, expected_version, instance_id, node_id, execution_id, resources):
    # 1. 检查 workspace/instance 状态
    # 2. 检查 cursor 和 node_id 匹配
    # 3. ✅ 检查该节点的 sensor gate（OPC 快照）
    if not self._check_node_conditions(workspace, instance, node_id):
        raise WorkspaceServiceError("node_conditions_not_met", ...)
    # 4. 条件满足 → 记录 execution
```

**优点**：
- 在认领动作前就检查条件，避免执行时才阻塞
- 状态清晰：条件不满足就不认领，cursor 不推进
- 与现有架构兼容

**实现步骤**：
1. 在 Task Template 中为每个节点定义条件（`node_conditions`）
2. 在 `claim_action` 时检查对应节点的 OPC 条件
3. 条件不满足时返回 `WaitingReason`，允许重试

### 方案 2：拆分 Task（简单但不够灵活）

把每个 transport 拆成两个独立的 Task：

```
Task A: [从S072取] - input_triggers: S072有料
Task B: [放到S06]  - input_triggers: S06为空, TaskA完成
```

利用现有的 Task 级别 `input_triggers` 和 `order` 前驱机制。

**优点**：
- 不需要修改 task-orchestration 服务
- 利用现有机制

**缺点**：
- Task 数量增多，管理复杂
- 不够灵活，无法表达复杂的节点间依赖

### 方案 3：在 Task Template 中定义节点间条件

给每个节点加条件：

```python
Template(
    node_ids=["从S072取", "放到S06"],
    node_conditions={
        "从S072取": [
            Trigger(kind="opc", config={...S072有料...}),
            Trigger(kind="opc", config={...S06为空...}),  # 取之前就要确保S06空
        ],
        "放到S06": [
            Trigger(kind="opc", config={...S06为空...}),  # 放之前再确认
        ]
    }
)
```

在 `claim_action` 时检查对应节点的条件。

## 建议

**推荐方案 1 + 方案 3 结合**：

1. 在 Task Template 中为每个节点定义条件（`node_conditions`）
2. 在 `claim_action` 时检查对应节点的 OPC 条件
3. 条件不满足时返回 `WaitingReason`，允许重试

这样可以：
- 避免执行时才阻塞导致的状态混乱
- 保持 Task 的原子性（多个节点组成一个完整的 Task）
- 提供清晰的错误信息和重试机制

## 相关文件

- [task-orchestration/service.py](file:///Users/dp/Ming/softwares/unilab/Uni-Lab-OS/task-orchestration/src/task_orchestration/service.py) - `claim_action` 方法
- [robot.py](file:///Users/dp/Ming/softwares/unilab/Uni-Lab-OS/unilabos/devices/workstation/szlab_poly_studio/s12_robot/robot.py) - `_robot_sensor_requirements` 和 `_wait_sensor_conditions` 方法
- [main.tsx](file:///Users/dp/Ming/softwares/unilab/Uni-Lab-OS/unilabos_local_ui/src/main.tsx) - Task Template 创建逻辑
- [workflowDraft.ts](file:///Users/dp/Ming/softwares/unilab/Uni-Lab-OS/unilabos_local_ui/src/workflowDraft.ts) - Workflow 导出逻辑
