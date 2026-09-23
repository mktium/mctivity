# MKTLIN01 单轴 D Uservo CSP 平滑轨迹修复 — 2026-09-23

## 目的

修复单轴 D Uservo EtherCAT CSP 定位的两个问题：定位过程中目标轨迹使用硬梯形更新，
以及多次运行之间 CSP 诊断基线未重置导致目标跳变和 `int32` 溢出。PV 速度控制、PDO
映射、profile 和 `/etc/mctivity` 配置保持不变。

## 实现

- 提交：`2432359 Smooth CSP position moves and reset diagnostics`
- `move_abs`、`move_rel` 在 CSP 位置路径使用平滑加速度包络；防摇关闭时不启用 ZVD，
  仍保持 1 ms `0x607A` 更新和原有行程限制。
- 每次新 CSP 定位建立独立 `csp_diag_motion_id`，目标重新对齐当前位置的首个采样只作
  基线，不计为运动跳变。
- CSP 目标/实际位置差分、加速度和 jerk 改为有饱和保护的 64 位状态和 JSON 字段，
  不再以 `INT32_MIN` 表示溢出。
- `move_rel` 明确使用位置模式，不再以 jog 标签承载位置目标。

## 验证

- 本地 C 单元测试全部通过。
- release preflight 的 profile、JSON、Python、JavaScript 和 C 检查通过；最终清洁门仅
  被工作区既有 `.DS_Store` 阻断，未删除该用户文件。
- 工控机真实 `/opt/etherlab` 环境通过：
  `gcc -I/opt/etherlab/include -O2 -Wall -Wextra -Werror` 语法检查和链接。
- 目标二进制 SHA-256：
  `d5107d83806ac10ad6d98c13fd14787c192c801b98c624decf52678f1fc99ea1`
- 目标源码 SHA-256：
  `20dde63433dd737e890b18a654aeeb7387b83d0c0182354f6459986086d0bd78`
- 源码归档 SHA-256：
  `8516b2f00e6a5c01edff25e3ddead1ac2e3ea1daa45fe26e276edcbc45e1c691`
- `libethercat.so.1`、`libm.so.6`、`libc.so.6` 动态依赖解析成功。

## 部署

- 当前 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-csp-smooth-2432359`
- `/opt/mctivity` 已切换到当前 release。
- 切换前 release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-csp-diag-33dc621`
- 部署备份：`/var/backups/mctivity/csp-smooth-2432359-20260923T105357Z`
- 仅受控重启 `mctivity-motiond.service`；没有重启 HMI，没有发送使能、模式切换、复位、
  标定或运动命令。
- `/etc/mctivity/axis.env`、`/etc/mctivity/hmi.env` 和相关 systemd 配置前后校验一致。
- HMI 首次访问行程 API 时按既有逻辑恢复持久化软件零点；该操作不改变原始编码器位置、
  不使能、不运动。恢复后 motiond 与 HMI 的当前位置/目标位置一致。

## 无运动验收

连续 60 秒只读采样 60/60 通过、坏样本 0：

- 服务 `active/running`，`NRestarts=0`；
- AL=`8`、OP=`1`、WC=`3`、WC complete；
- `enabled=false`、`servo_request=false`、`moving=false`、`fault=false`；
- `cw=0`、`commanded_mode=0`、`target_raw=pos_raw`；
- `rt_deadline_miss_count=0`、`rt_skipped_periods=0`、通信时序故障为 false；
- 新诊断 `csp_diag_motion_id=0`，所有目标导数和最大值为 0。

本记录不代表运动验收已经完成。下一步低速 CSP 实测必须另行确认后进行。
