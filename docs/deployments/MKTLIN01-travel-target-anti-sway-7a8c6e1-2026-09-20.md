# MKTLIN01 目标行程约束与防摇规划器阶段部署 — 2026-09-20

## 根因与修复结果

本轮先处理“端点标定后没有落盘”的问题。根因是普通 HMI 状态定时保存会用不含
`travel` 的 payload 覆盖整轴状态，把 `/api/travel/record` 已写入的左右端点擦掉；
不是文件权限、重启或 EtherCAT 通信问题。修复已在前一提交 `83a542c` 部署并验证，
本次部署没有改动这条持久化逻辑。

## 本轮实现

- 提交：`7a8c6e1`，本地分支 `feature/v1.4.1-axis-d-uservo-anti-sway`；按要求未推送。
- 已标定端点转换为 HMI 机械方向的目标滑块安全范围；D 轴 `position_direction=-1`。
- 拖动目标滑块只修改目标值，不自动下发运动；必须明确点击“移动到目标”。
- 后端仍以原始计数安全边界重复校验，浏览器范围不是唯一安全层。
- 新增纯逻辑 ZVD 防摇规划器和测试。初始周期按现场视频估计约 `1150 ms`，默认不启用，
  尚未接入 motiond 实时运动链路，HMI 防摇开关保持关闭。

## 验证

- `scripts/mctivity-release-preflight.sh`：通过。
- 行程运行时测试：12 项通过；防摇规划器测试：3 项通过。
- profile/HMI 回归、Python/JavaScript/shell 检查、C `-Wall -Wextra -Werror` 单元测试：通过。
- 远端源码包 SHA-256：
  `853f52114834b0ed26971350c82a67d7c39eacb2d6f01ad5fd7298089c4178f2`。
- 远端文件 SHA-256：
  - `mctivity_hmi/mctivity_hmi.py`：`e173f10618b7e64cd370b6244cda8d3d4148a1540095bffad787d0c1814c6300`
  - `mctivity_hmi/travel_runtime.py`：`bdf5ed47cffd95169237d3a54e8babe91759b17915b9898b36ed7970ec417d7f`
  - `mctivity_hmi/anti_sway.py`：`ee1b05a9b06dc621a29918761ce56a9823133285c077f8c7fc9cac3b2ae89abc`

## 无运动部署

- 目标 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-7a8c6e1`。
- 当前链接：`/opt/mctivity` 指向该 release。
- 切换前备份：`/var/backups/mctivity/pre-target-bounds-7a8c6e1-20260920T185500`。
- 只重启了 `mctivity-hmi.service`；`mctivity-motiond.service` 保持原进程，未重启。
- 第一次切换因源码包目录继承了本机 `700` 权限而由 readiness 检查自动回滚；修正 release
  为可读可进入后再次切换成功，旧 release 保留。
- 只读验收：HMI/motiond active；D 轴 OP/WC 正常；`enabled=false`、`servo_request=false`、
  `moving=false`、`fault=false`、`cw=0`；目标位置等于实际位置 `-7337`；端点仍为左 `76352`、
  右 `-88499`；`endpoints_valid=true`；防摇 `enabled=false`、`ready=false`。
- 当前现场环境中的 `MCTIVITY_COMMISSIONING_INHIBIT=0` 保持原样，本次没有修改，也没有发送
  使能、模式、停止、复位或运动命令。

下一阶段需要先把 ZVD 规划器接入 1 ms 实时轨迹执行器，再做无驱动器输出的仿真/边界验收；
实际使能、首次点定位和防摇运动测试仍需单独确认。
