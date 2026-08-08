# SZLab Robot Testing

STATION=S03 TASK=pick PRODUCT_TYPE=1 POSITION=1 CONFIRM=YES \
unilabos/devices/workstation/szlab_poly_studio/s12_robot/robot_arm_step_test.sh

⬆️测试代码

ActionContract 的实体 OPC 安全断言不可通过旧 debug 环境变量跳过。

我更新了上位机通讯和robot_only,主要是新增了S08倒料产品选择，需要给1和2作为250ml和500ml robot_only.xlsx 上位机通讯.csv
