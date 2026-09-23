# MKTLIN01 单轴 D Uservo CSP 轨迹诊断 — 2026-09-23

## 目的

为当前单轴 D Uservo CSP 位置模式增加只读轨迹诊断，用于定位厂家 PV/PP
安静而 EtherCAT CSP 有噪音的问题。诊断不改变 CSP 轨迹、控制字、模式、
EtherCAT 周期或使能/运动门禁。

## 实现与提交

- 分支：`feature/v1.4.1-axis-d-uservo-anti-sway`
- 诊断提交：`5b45fd6`
- 启动伪影修复提交：`33dc621`
- 诊断字段通过现有 HMI `/api/status?device=mctivity` 返回：
  `csp_diag_target_step_counts`、目标速度/加速度/加加速度、实际位置差分估计、
  目标保持/更新计数和最大目标导数。
- 诊断目标导数只在 D 轴已使能且 `servo_request=true` 时统计，启动阶段和失能
  时的实际位置钉住不会污染运动数据。

本地 C 单元测试、`git diff --check`、远端 `/opt/etherlab` 工具链的
`-O2 -Wall -Wextra -Werror` 编译和动态库链接均通过。release preflight 的
功能测试全部通过；最终清洁检查仅被工作区既有的 `.DS_Store` 阻断，未删除该用户文件。

## 部署与回滚

- 最终 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-csp-diag-33dc621`
- 当前 `/opt/mctivity` 已指向最终 release。
- 旧 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-68ca401`
- 诊断上一版 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-csp-diag-5b45fd6`
- 最终部署前备份：`/var/backups/mctivity/csp-diag-33dc621-20260923T071540Z`
- 首次部署因 2 秒采样时仍处于 `AL=2/OP=0/WC=0` 自动回滚；备份：
  `/var/backups/mctivity/csp-diag-5b45fd6-20260923T071132Z`
- 首次回滚后旧 release 恢复到 `AL=8/OP=1/WC=3`，没有删除任何 release。
- 源码归档 SHA-256：`edcb964be9693ea0f35a7f3a3720c48774be93c15227d843b910cdecab29884d`
- 现场 `mctivity_motiond` SHA-256：
  `526317cca2847193bc3f24577ec15c15a9cde65fed04d8a9f96f07d50c2691aa`
- 现场源码 `mctivity_motiond.c` SHA-256：
  `58cccddc94bd3b38359a7bd3ec932bab007e67a12be006659750eb73a87e184a`
- `libethercat.so.1`、`libm.so.6`、`libc.so.6` 动态依赖解析成功。
- `/etc/mctivity/axis.env` SHA-256 前后均为
  `51e653ba14828d75e9b07dba82b29ab317d10a5ab285ff44d50d3c4704108552`。
- `/etc/mctivity/hmi.env` SHA-256 前后均为
  `4f761b82f13e45e7088ba6502a85d5b0ba4bae1fc0368b135f6096e525bbea15`。

## 无运动验收

本次只受控重启 `mctivity-motiond.service`，没有重启 HMI；没有发送使能、失能、
模式切换、复位、标定或运动命令。

最终 60 秒只读采样结果：

- 60/60 样本通过，坏样本 0；
- topology：`axis-d-uservo-combined`；AL=`8`、OP=`1`、WC=`3`、WC complete；
- `enabled=false`、`servo_request=false`、`moving=false`、`fault=false`；
- 控制字 `cw=0`、`commanded_mode=0`；驱动器的 `mode` 显示值保留为 `8`，这是
  6061 的历史显示值，不代表 motiond 正在发模式切换；
- `target_raw == pos_raw`；
- `rt_deadline_miss_count`、`rt_skipped_periods`、`wc_change_count` 和
  `communication_timing_fault` 在 60 秒内均未增加；
- 诊断目标步长、目标速度、目标加速度、目标加加速度、保持/更新计数和最大目标
  加加速度均为 0，未使能状态没有产生伪影。

当前 `MCTIVITY_COMMISSIONING_INHIBIT=0` 是部署前既有值，本次保持不变；本记录
不代表已完成任何使能或运动验收。下一阶段需另行确认后，使用低速短行程采集 CSP
运动诊断，再与 PV 和厂家 PP 结果对比。
