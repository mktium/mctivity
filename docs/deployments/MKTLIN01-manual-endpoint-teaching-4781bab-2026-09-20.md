# MKTLIN01 D 轴手动端点示教部署记录

- 日期：2026-09-20（Asia/Shanghai）
- 主机：`MKTLIN01` (`192.168.1.201`)
- 分支：`feature/v1.4.1-axis-d-uservo-anti-sway`
- 源码提交：`4781bab`（完整提交号见 Git）
- 目标 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-4781bab`
- 活动链接：`/opt/mctivity -> /opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-4781bab`
- 源码包 SHA-256：`e59896487e0b97659a18abea786d13b3bc5198b643a4365a08ad7648fcdf194e`
- motiond SHA-256：`4eaaccb5a794f5a4f9367c36570dd1fbff710413cbf8ab546c7e989a0f77f8d7`
- 部署前备份：`/var/backups/mctivity/pre-manual-endpoint-4781bab-20260920`

## 本次变更

- 移除 D 轴行程标定对厂家原生 Homing/物理 0 点的依赖。
- HMI 增加手动记录左端点、右端点和清除标定；记录动作只读取停止轴状态并持久化编码器位置。
- 端点数据带版本号，校验左右顺序和安全余量；两端完成后才启用软件行程边界。
- 位置目标越过安全范围被拒绝；有边界的 CSP 点动到安全边缘后清除速度请求。
- 清除标定要求轴已停止且已失能；设置软件零点和转矩控制在端点有效时被拒绝，避免破坏坐标边界。
- 防摇开关仍保持关闭，等待后续单独实现和验证。

## 无运动切换与核验

本次切换前备份了 `/etc/mctivity` 和相关 systemd unit，并重启了必要服务。没有发送使能、模式切换、停止、寻相、端点记录或运动命令。

- `mctivity-motiond.service`：active
- `mctivity-hmi.service`：active
- `mctivity-kiosk.service`：active
- 逻辑轴：D；拓扑：`axis-d-uservo`
- commissioning inhibit：true
- enabled / servo_request / moving / fault：`false / false / false / false`
- 控制字：`0`
- 目标位置等于实际位置：`230460 / 230460`
- deadline miss / skipped periods：`0 / 0`
- 端点：未标定；左/右均为空；记录按钮因轴未使能而不可用
- native homing required：false

首次实际点动和端点示教仍属于物理操作；需要操作员另行确认机械区域安全后再进行。
