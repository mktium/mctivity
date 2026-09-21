# MKTLIN01 motiond 构建产物缺失修复 — 2026-09-21

## 修复前

- 当前 release：`/opt/mctivity -> /opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-68ca401`
- `mctivity-motiond.service` 按约 2 秒周期自动重启，ExecStart 退出码为 `1`。
- 当前 release 有 `mctivity_motiond.c` 和 `Makefile`，但没有
  `mctivity_pdo_monitor/mctivity_motiond`。
- HMI 的 `/api/status` 和 `/api/travel` 因 `127.0.0.1:10001` 未监听而返回连接拒绝。
- 修复前备份：`/var/backups/mctivity/motiond-repair-20260921T071734Z`。

## 构建与部署

- 在当前 release 的 `mctivity_pdo_monitor` 目录执行：

  ```text
  make -C /opt/mctivity/mctivity_pdo_monitor mctivity_motiond CC=gcc CFLAGS='-O2 -Wall -Wextra -Werror'
  ```

- 编译使用当前 release 的源码和 Makefile，并链接目标机 `/opt/etherlab/lib/libethercat.so.1`；没有复制旧 release 的二进制。
- 编译产物：`/opt/mctivity/mctivity_pdo_monitor/mctivity_motiond`
- 源码 SHA-256：
  - `mctivity_motiond.c`：`d24bd1a39efa014589335d9e1f847aa560335302365c1fa24421d5f2a022ff08`
  - `Makefile`：`9e967e258dc410916d7d0493de34c1f35e2e15bfb35ae20be1cf4bf7a322ac23`
- 二进制 SHA-256：`21754b9236fe449124c6e56160c389498030e1df1d597e3cf1de4b637d68f161`
- 二进制：x86-64 PIE，`root:root`，权限 `755`。
- 动态库依赖均解析成功：`libethercat.so.1`、`libm.so.6`、`libc.so.6`。
- 受控执行一次 `systemctl restart mctivity-motiond.service`；没有切换 release。

## 配置与只读验收

- `axis.env` 构建前后 SHA-256 均为
  `51e653ba14828d75e9b07dba82b29ab317d10a5ab285ff44d50d3c4704108552`。
- `hmi.env` 构建前后 SHA-256 均为
  `4f761b82f13e45e7088ba6502a85d5b0ba4bae1fc0368b135f6096e525bbea15`。
- 未修改 EtherCAT PDO、profile、axis.env 或 HMI env。
- 服务：`active (running)`，`Result=success`，`ExecMainStatus=0`，`NRestarts=0`，PID `22474`。
- 服务监听：`127.0.0.1:10001`。
- EtherCAT/运行时只读状态：`AL=8`、`OP=1`、`WC=3/3`、`communication_timing_fault=false`、deadline miss `0`、skip `0`。
- 驱动器状态：`enabled=false`、`servo_request=false`、`moving=false`、`fault=false`、控制字 `0`。
- HMI `/api/status` 和 `/api/travel` 均恢复；行程当前为未标定，防摇关闭，未执行标定。
- 现场既有 `commissioning_inhibit=false` 保持不变；本次没有修改门禁状态。
- 没有点击标定、没有使能、没有发送模式切换、停止、复位、寻相或运动命令。

## 回滚

本次没有切换旧 release，也没有删除任何 release。若后续需要回滚，使用备份目录中的服务、配置和 active-release 记录；先保持驱动器失能，再按只读状态确认后处理，不自动复位故障。
