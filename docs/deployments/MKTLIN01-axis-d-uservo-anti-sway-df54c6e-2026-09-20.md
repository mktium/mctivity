# MKTLIN01 单轴 Uservo 实时防摇部署记录 — 2026-09-20

## 范围

- 主机：`MKTLIN01` (`192.168.1.201`)
- 分支：`feature/v1.4.1-axis-d-uservo-anti-sway`
- 本地提交：`df54c6e9b48e6ff7aa75f08988ddf21bb8bcf030`
- 远端活动 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-df54c6e`
- 活动链接：`/opt/mctivity -> /opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-df54c6e`
- 切换前旧 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-94a4987`
- 切换前备份：`/var/backups/mctivity/pre-zero-snapshot-df54c6e-20260920T120833`
- 源码包 SHA-256：`39f6850ac64e8215990f9c80456ff2c89ea5480567a8b1e4e1752640dc131f3f`
- 目标机 motiond SHA-256：`21754b9236fe449124c6e56160c389498030e1df1d597e3cf1de4b637d68f161`

## 实现

- motiond 的 1 ms CSP 轨迹加入实时 ZVD 输入整形：名义 S 曲线按 `0 / T/2 / T` 三个采样延迟形成防摇目标，`T` 为 HMI 配置的摆动周期，默认 `1150 ms`。
- 新增 `move_shaped_abs`；端点安全范围仍由 HMI 和 motiond 双重校验。
- 防摇默认关闭。只有端点有效、周期已保存且操作者明确打开 HMI 开关后，位置绝对/相对动作才会走整形轨迹；速度/PV 和普通点动路径不改行为。
- motiond 重启后，HMI 在轴健康、停止、失能时恢复已持久化的软件零点；该动作只改坐标换算，不改原始目标，不发送使能、模式或运动命令。

## 构建与测试

- 本地 `scripts/mctivity-release-preflight.sh`：通过。
- profile、Python、HMI、JavaScript、JSON、shell 语法检查：通过。
- C 逻辑单元测试（含 ZVD）：通过。
- MKTLIN01 真实 EtherLab 编译：通过 `cc -I/opt/etherlab/include -O2 -Wall -Wextra -Werror -L/opt/etherlab/lib ... -lethercat -lm`。
- 本轮没有开启防摇，没有执行使能、模式切换、故障复位、端点记录、点定位或运动测试。

## 无运动验收

部署时 D 轴保持：

- `axis-d-uservo-combined`，物理位置 0，OP，Domain0 WC `3/3`；
- `enabled=false`、`servo_request=false`、`moving=false`、`fault=false`、控制字 `0`；
- 原始位置 `238891`，软件零点 `246228`，显示位置 `-7337`，目标位置 `-7337`；
- `communication_timing_fault=false`、deadline miss `0`、skipped periods `0`；
- 端点仍为左 `76352`、右 `-88499`，行程 guard ready，防摇 `enabled=false`；
- HMI 和 motiond 均 active。

随后连续约 60 秒只读轮询 12 次：位置和目标保持 `-7337/-7337`，两轴失能状态、控制字 0、无故障、通信健康、端点有效和防摇关闭断言全部通过。

现场 `MCTIVITY_COMMISSIONING_INHIBIT=0` 保持原样；本次没有修改环境门禁，也没有向驱动器发送运动相关命令。首次启用防摇和实际运动验证仍需操作员明确确认，并另行记录周期调参结果。

## 回滚

保留旧 release；若后续无运动状态、HMI、PDO/WC 或零输出条件异常，恢复上述旧链接并重启 `mctivity-motiond.service`、`mctivity-hmi.service`，不删除任何 release。
