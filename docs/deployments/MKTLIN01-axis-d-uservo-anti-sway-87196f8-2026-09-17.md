# MKTLIN01 D 轴 Uservo 防摇：87196f8 部署记录

- 日期：2026-09-17（Asia/Shanghai）
- 主机：`MKTLIN01` (`192.168.1.201`)
- 分支：`feature/v1.4.1-axis-d-uservo-anti-sway`
- 源码提交：`87196f826f5c1bf77199863f3ac41a636e914b02`
- 目标 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-87196f8`
- 活动链接：`/opt/mctivity -> /opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-87196f8`
- motiond SHA-256：`0b0c616c11948cdb538ace59c1740eda91398f204573f54605e6b8837bc1a193`
- 源码包 SHA-256：`9a65bbb54dd796843a0b6b03d312cb4154571e956b3dee2a28020dc368f609e7`
- 部署前备份：`/var/backups/mctivity/pre-axis-d-uservo-anti-sway-87196f8-20260917T103949Z`

## 操作范围

已在保持调试门禁的条件下切换 release，并仅重启 `mctivity-motiond.service`。没有发送使能、模式切换、寻相、齿轮或运动命令。

本记录对应的 native homing 代码仅为过渡候选；后续手动端点示教改造已明确不使用厂家原生 Homing。实际端点流程只记录已停止轴的编码器位置，不发送驱动命令。

## 重启后只读核验

- `mctivity-motiond.service`：active
- `mctivity-hmi.service`：active
- `mctivity-kiosk.service`：active
- 逻辑轴：D；物理位置：0；OP：是；WC：3/3
- commissioning inhibit：true
- enabled / servo_request / moving / gear_running：`false / false / false / false`
- fault：false；控制字：0；目标位置与实际位置：均为 0
- homing_active / homing_attained / homing_error：`false / false / false`
- deadline miss / skipped periods：`0 / 0`
- 齿轮会话与安全锁存：均未激活

## 后续边界

首次真实寻相/端点标定属于物理动作。继续前必须由操作员确认机械区域安全、低速点动条件和端点接触策略；本次部署未执行该动作。
