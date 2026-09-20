# MKTLIN01 单轴 Uservo CSP/PV 混合速度模式 — 2026-09-20

## 目的

为当前只有 D 轴 Uservo 驱动器、原电机的 MKTLIN01 增加可直接设定速度的
HMI 模式，同时保留 CSP 位置控制。电子齿轮和双轴 D/E 功能不在本次单轴
profile 中启用；防摇仍未启用，首次运动测试另行授权。

## 实现

- profile：`axis-d-uservo-combined`；D 轴对应 `mctivity`、EtherCAT 物理位置 0。
- 位置模式：CiA 402 CSP，目标位置 `0x607A`，实际位置 `0x6064`。
- 速度模式：原生 CiA 402 PV，目标速度 `0x60FF`，实际速度 `0x606C`，模式码 3。
- 默认仍为 CSP 位置模式；HMI 速度范围为 `1–999 rpm`，默认 `222 rpm`。
- 混合 PDO：RxPDO `0x1600` = `6040/6060/607A/60FF/60FE:01`；
  TxPDO `0x1A00` = `6041/6061/6064/606C/60FD`。
- 未加入虚拟轴；HMI 只显示实际 D 轴。

## 源码、构建和 release

- 分支：`feature/v1.4.1-axis-d-uservo-anti-sway`。
- 功能提交：`95cb0c4`；启动器修复提交：`86cb81d`；界面文案提交：`e230a1e`。
- 源码归档：`/tmp/mctivity-build-e230a1e.tar`。
- 源码归档 SHA-256：`1afebf4da113bced7b492bd2656b464a72acc5801b344f4bd82aab1a7e2cc05e`。
- 目标 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-e230a1e`。
- 目标 motiond SHA-256：`def767613145077760422babcb09ab35acc2946811c84b525a61e894130a1343`。
- 目标使用真实 `/opt/etherlab`，以 `gcc -O2 -Wall -Wextra -Werror` 编译通过。
- 未推送远端；本地分支和提交保留，待用户明确要求后再处理远端同步。

## 配置和回滚

- 当前 `/opt/mctivity` 指向上述 release。
- `/etc/mctivity/axis.env` 和 `hmi.env` 已切换到 `axis-d-uservo-combined`。
- `MCTIVITY_COMMISSIONING_INHIBIT=0` 按操作者此前明确要求保持解除；本次没有发送使能、模式切换、复位、停止或运动命令。
- 首次部署前备份：`/var/backups/mctivity/pre-native-pv-csp-95cb0c4-20260920`。
- 修复部署前备份：`/var/backups/mctivity/pre-native-pv-csp-86cb81d-20260920`。
- 最终 HMI release 切换前备份：`/var/backups/mctivity/pre-native-pv-csp-e230a1e-20260920`。
- 旧 release 未删除，可通过备份中的 `active-release.txt` 找回旧链接；失败时保持当前门禁值，恢复旧链接和配置后再重启服务。

## 无运动验收

部署后 motiond、HMI、kiosk 均为 active。对 HMI status 做 60 秒、每秒一次的
只读采样：

- 60/60 样本通过，坏样本 0；
- D 轴 topology `axis-d-uservo-combined`，`al_state=8`、`operational=1`、`wc=3`、`wc_complete=true`；
- `enabled=false`、`servo_request=false`、`moving=false`、`fault=false`、`cw=0`；
- `target_raw == pos_raw`，`rt_deadline_miss_count=0`、`rt_skipped_periods=0`，通信时序故障为 false；
- HMI profile 正确，`axis.mode.velocity.execute` 已暴露，线性行程/手动端点状态可读。

## HMI 手动端点按钮刷新修复

现场复核发现：D 轴已经保持使能且停止时，远端 `/api/travel` 已返回
`recording_available=true`，但 HMI 的“记录左/右端点”仍显示为禁用。原因是
300 ms 状态轮询更新了轴状态，却没有同步更新缓存的行程模型，页面继续使用上一次
失能状态的 `recording_available=false`。

修复内容：行程卡在渲染按钮时同时依据最新轴状态判断“已使能、已停止、通信正常、无故障”，
后端记录接口仍会再次执行完整安全门禁。这样不会放宽运动或记录条件，只修正按钮显示滞后。
相关回归测试覆盖了该前端判定；本次部署不发送任何驱动控制命令。

本记录不代表已经完成首次使能、速度运行或防摇效果验收；这些操作必须由操作者另行确认后执行。
