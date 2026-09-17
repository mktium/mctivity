# MKTLIN01 单轴行程与防摇 UI 阶段部署 — 2026-09-17

## 范围

本次部署把单轴 D Uservo 的行程/防摇只读模型和 HMI 面板部署到现场。端点标定、驱动器原生回零调用和防摇运动轨迹尚未接入，不包含使能、回零或运动授权。

## 源码与构建

- 本地分支：`feature/v1.4.1-axis-d-uservo-anti-sway`；
- 本地提交：`a82ff64702f2f1a27097f62e2ece5dad085d0fec`；
- 远端推送：本次未推送；
- 源码包：`/tmp/mctivity-a82ff64.tar.gz`；
- 源码包 SHA-256：`d40289b77c1509dbabd3ac5f6496b8c06483def3e556e53398d92a6ffea323bd`；
- 目标编译使用真实 `/opt/etherlab`，参数为 `gcc -O2 -Wall -Wextra -Werror`；
- 目标 `mctivity_motiond` SHA-256：`fa9a17db7b92c8cb29e3b6a06f9abb8a49880ae637595ef840dbcb0336c31db9`。

## 现场 release

- 新 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-a82ff64`；
- active 链接：`/opt/mctivity` 已切换到上述 release；
- 切换前备份：`/var/backups/mctivity/pre-axis-d-uservo-anti-sway-a82ff64-20260917T084811Z`；
- 旧 release `v1.4.1-axis-d-uservo-anti-sway-6c372dc` 保留，未删除；
- `axis.env` 与 `hmi.env` 均保持 `MCTIVITY_COMMISSIONING_INHIBIT=1`；
- 为避免已知 EtherCAT 重启通信风险，本次只重启了 `mctivity-hmi.service` 和 `mctivity-kiosk.service`，未重启 `mctivity-motiond.service`。

## 无运动验收

现场只读检查通过：

- `mctivity-motiond.service`、`mctivity-hmi.service`、`mctivity-kiosk.service` 均 active；
- Axis D `operational=1`，WC `3/3`，`fault=false`；
- `commissioning_inhibit=true`；
- `enabled=false`、`servo_request=false`、`moving=false`；
- 控制字 `cw=0`，目标位置与实际位置均为 `0`；
- `communication_timing_fault=false`；
- HMI capability 返回 `linear_travel_control.available=true`，D 轴和 `10000 counts/rev` 正确；
- `/api/travel` 返回 `endpoints_valid=false`、`calibration_state=uncalibrated`、`calibration_actions_available=false`；
- HMI 页面包含“行程与防摇”只读面板，防摇开关保持禁用；
- 未发送复位、模式切换、使能、停止、回零、端点标定或运动命令。

## 后续

下一步仍需先在本地完成厂家原生回零对象语义和端点状态机的实现与测试。任何端点标定、驱动器对象写入或首次运动，都要另行明确授权。
