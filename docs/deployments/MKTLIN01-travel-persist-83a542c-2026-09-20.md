# MKTLIN01 行程标定持久化修复 — 2026-09-20

## 根因

现场“标定后页面正常，但端点后来消失”的原因不是重启和文件权限：

1. `/api/travel/record` 会把端点写入 `/var/lib/mctivity/mctivity_hmi_state.json`；
2. HMI 随后会定时保存速度、模式、滑块和传动参数到 `/api/ui_state`；
3. 普通保存的 payload 不包含运行时拥有的 `travel` 字段；
4. 后端原来用普通 payload 直接替换整轴状态，把已保存的左右端点覆盖掉了。

## 修复

- 提交：`83a542c`，本地分支 `feature/v1.4.1-axis-d-uservo-anti-sway`；未推送远端。
- `save_ui_state()` 改为只合并经过白名单校验的普通 UI 字段，保留已有的运行时
  `travel` 标定数据。
- 新增回归测试：先写入左右端点，再保存普通速度/模式设置，端点必须仍然存在。
- 本次不改变 PDO、motiond、驱动器参数或任何运动路径。

## 验证

- `scripts/mctivity-release-preflight.sh`：通过。
- HMI、行程运行时、双 Uservo、速度模式和 C 单元测试：通过。
- 目标机 HMI API 只读验收：
  - `endpoints_valid=true`；
  - 左端点 `76352`，右端点 `-88499`；
  - 当前位置 `-7337`，行程百分比约 `50.77%`；
  - `enabled=false`、`servo_request=false`、`moving=false`、`cw=0`；
  - HMI 与 motiond 均 active。

## 无运动部署

- release：`/opt/mctivity-releases/v1.4.1-axis-d-uservo-anti-sway-83a542c`。
- `/opt/mctivity` 已切换到该 release。
- 备份：`/var/backups/mctivity/pre-travel-persist-83a542c-20260920T183019`。
- 仅重启 `mctivity-hmi.service`；没有重启 motiond，也没有发送使能、模式、停止、
  复位或运动命令。
- 首次自动验收脚本错误地假设“当前应未标定”，读到真实双端点后自动回滚；修正
  验收条件后重新切换成功，旧 release 保留。

首次重新使能、点定位和防摇测试仍需操作者另行确认。
