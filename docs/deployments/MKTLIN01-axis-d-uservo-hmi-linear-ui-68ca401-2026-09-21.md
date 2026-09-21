# MKTLIN01 单轴 Uservo 线性定位 HMI 改版部署 — 2026-09-21

## 部署范围

- 主机：`MKTLIN01`（`192.168.1.201`）
- 本地提交：`68ca401`（`Redesign linear position and anti-sway HMI`）
- 目标 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-68ca401`
- 活动链接：`/opt/mctivity` 已切换到目标 release
- 切换前 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-df54c6e`
- 切换前备份：`/var/backups/mctivity/pre-hmi-ui-68ca401-20260921T062520Z`
- 源码归档 SHA-256：`f8b5a6cd0d0020a796e4d10070c83e9f4f19527d9931fa1270950e2f782df26b`
- 目标 HMI 文件 SHA-256：`7eb7ada5007c48d47a0d134a36b8381c4d7f65e9622a6a41b12cf8763b326111`

## HMI 改动

- 线性 D 轴的目标位置、端点、当前位置和安全范围按 `mm` 显示；编码器计数保留为辅助信息。
- 按当前机构参数使用 `1 motor rev = 40.0 mm`。
- 速度和加速度控件改为横向布局。
- 摆动周期和防摇开关固定在行程卡片内，短屏不再隐藏防摇开关。
- 未改变 motiond、PDO、运动命令、行程保护或防摇运行逻辑。

## 无运动切换记录

- 只切换了 `/opt/mctivity` 并重启 `mctivity-hmi.service`。
- `mctivity-motiond.service` 未重启；部署前后保持 active。
- motiond 部署前启动时间：`2026-09-21 14:11:28 CST`。
- HMI 部署后启动时间：`2026-09-21 14:25:31 CST`。
- 页面只读检查确认包含 `rate-sliders`、`travel-tuning` 和 `PRIMARY_AXIS_LINEAR_MM_PER_REV = 40.0`。
- D 轴状态：`enabled=false`、`servo_request=false`、`moving=false`、`fault=false`、`cw=0`。
- 本次没有发送使能、模式、停止、复位、寻相或运动命令。
- 现场原有 `MCTIVITY_COMMISSIONING_INHIBIT=false` 保持不变；本次不是运动验收，且 EtherCAT 当时仍未进入 OP/WC 完整状态。

## 回滚

旧 release 未删除。发生 HMI 页面或服务异常时，恢复备份中的 active-release 目标并只重启
`mctivity-hmi.service`；不重启 motiond，不修改现场门禁配置。
