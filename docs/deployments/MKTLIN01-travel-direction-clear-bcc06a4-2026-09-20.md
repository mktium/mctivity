# MKTLIN01 端点方向与清除标定修复 — 2026-09-20

## 范围

本次修复针对单轴 D Uservo 的手动行程标定，不改变 EtherCAT PDO、motiond
实时进程或驱动器参数。现场现象是机械方向已经反向后，物理左端可能对应更大的
原始编码器计数，导致“记录左端点”被旧的 `left < right` 校验拒绝；同时 HMI
的“清除标定”按钮可能受快速状态缓存影响显示为不可用。

## 实现

- 提交：`bcc06a4`，本地分支 `feature/v1.4.1-axis-d-uservo-anti-sway`；未推送远端。
- D 轴行程配置增加 `position_direction=-1`，物理左右端点按机械方向校验。
- 端点仍保存为驱动器原始坐标，安全范围和百分比按方向计算，传给 motiond 的
  `min_pos/max_pos` 始终为数值升序。
- HMI 与后端共用同一方向配置，不再由前端单独硬编码。
- “清除标定”保持后端硬门禁：轴必须静止、失能且没有 servo request；按钮不再
  因快速状态缓存滞后而变灰，若条件不满足由后端返回明确错误。
- 错误弹窗现在同时显示 `reason/detail`，便于区分使能门禁和端点顺序校验。

## 本地验证

- `scripts/mctivity-release-preflight.sh`：通过。
- HMI Python 单元测试、双 Uservo 回归测试、行程运行时测试：全部通过。
- Python 编译、JSON 校验、JavaScript/shell 语法检查：通过。
- 本次没有发送使能、失能、模式切换、停止、复位或运动命令。

## 无运动部署

- 目标主机：`192.168.1.201`。
- 目标 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-bcc06a4`。
- 当前链接：`/opt/mctivity` 指向上述 release。
- 切换前备份：`/var/backups/mctivity/pre-travel-direction-clear-bcc06a4-20260920T180857`。
- 仅重启 `mctivity-hmi.service`；`mctivity-motiond.service` 未重启，保持 active。
- 首次切换检查因 HMI 尚未完成监听而自动回滚；等待启动完成后再次切换成功，旧
  release 未删除。
- 远端文件 SHA-256：
  - `mctivity_hmi/mctivity_hmi.py`：`f50ff339bf66aa0710e16e893bc417f8a831c5a90b416a417760d454a5588d38`
  - `mctivity_hmi/travel_runtime.py`：`b363df8ea6b9dfc4ccb982b0bc6fddedcd44990ce3255c26cb1cdea9f2340493`
  - `modules/axis/device/uservo/combined/single/module.json`：`446b3fe525c8dd8cff7dda8f450fdfaafc879be2bc905f3819da9ea83fd0d08c`

## 只读验收

HMI 恢复监听后通过 GET 接口检查：

- `position_direction=-1`；
- `clear_available=true`；
- `endpoints_valid=false`，当前仍为未标定；
- `enabled=false`、`servo_request=false`、`moving=false`、`cw=0`；
- HMI 和 motiond 均为 active。

目标机原有 `MCTIVITY_COMMISSIONING_INHIBIT=false` 保持不变，本次没有修改门禁。
首次重新使能、端点记录和运动测试仍需操作者单独确认。
