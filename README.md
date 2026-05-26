# mctivity

`mctivity` 是一套面向 EtherCAT 伺服控制场景的双轴运动控制软件，包含运动守护进程、Web HMI 和命令行控制工具。

当前版本：`v1.1.0`，增加 Absolute Positioning 相关能力。

说明：本仓库目录名仍为 `1.0`，用于延续原始 1.0 代码和部署结构；版本状态以 Git 标签为准。

## 版本记录

- `v1.1.0`：增加 Absolute Positioning、传动设定、软限位、速度/加速度运动曲线、HMI 状态持久化。
- `v1.0.0`：Initial open-source 1.0 release。

## 项目组成

`mctivity` 目前由三部分组成：

- C 语言运动控制守护进程，负责 EtherCAT 和伺服通信
- Python Web HMI，负责浏览器界面和 HTTP API
- 命令行工具，负责直接向守护进程发送控制命令

## 目录结构

- `mctivity_pdo_monitor/`
  运动控制侧源码和编译入口 `Makefile`
- `mctivity_hmi/`
  Web HMI 和命令行工具
- `systemd/`
  Linux 部署示例服务文件

## 架构概览

数据流如下：

`浏览器 -> mctivity_hmi.py -> TCP 127.0.0.1:10001 -> mctivity_motiond -> EtherCAT 从站`

默认运行参数：

- 运动守护进程地址：`127.0.0.1`
- 运动守护进程端口：`10001`
- HMI 监听地址：`0.0.0.0`
- HMI 监听端口：`2015`

主要接口：

- `GET /`
- `GET /api/status?device=mctivity`
- `GET /api/status?device=fv3`
- `GET /api/ui_state`
- `POST /api/ui_state`
- `POST /api/command`

## v1.1.0 - Absolute Positioning（2026-05-22）

本版本在保留 `mctivity_*` 命名和原有部署结构的基础上，最小化合入了传动设定、软限位和速度/加速度运动曲线相关能力。

### 运动守护进程

文件：

```text
mctivity_pdo_monitor/mctivity_motiond.c
```

本次新增和调整：

- 增加 `clear_motion()`，统一清理运动状态，避免不同命令路径残留旧运动参数。
- 增加 `profile_active` 运动状态，用于按 `speed_rpm` 和 `acceleration_rpm_s` 做速度/加速度约束移动。
- `move_abs` 和 `move_rel` 支持以下附加字段：
  - `speed_rpm`
  - `acceleration_rpm_s`
  - `min_pos`
  - `max_pos`
- `min_pos/max_pos` 是传给 motion daemon 的软件目标限位，守护进程会在执行前夹紧目标位置。
- `home` 和 `set_zero` 行为已拆开：
  - `home` 会切到 `homing` 模式，并记录 `last_command=home`
  - `set_zero` 只将当前位置记录为软件零点，并记录 `last_command=set_zero`

### HMI

文件：

```text
mctivity_hmi/mctivity_hmi.py
```

本次新增和调整：

- 新增传动设定弹窗。
- 支持旋转/直线两类负载单位换算。
- 支持周期/往返两类行程模式。
- HMI 根据传动设定计算绝对位置滑条软限位，并在发送运动命令时同步带上 `min_pos/max_pos`。
- 直线模式下会显示当前位置绿色箭头。
- 新增界面状态持久化接口：
  - `GET /api/ui_state`
  - `POST /api/ui_state`

默认界面状态文件为：

```text
mctivity_hmi/mctivity_hmi_state.json
```

可通过环境变量覆盖：

```bash
MCTIVITY_UI_STATE_PATH=/path/to/state.json
```

### 接手注意

- 这次没有把任何 `mctivity_*` 文件、服务或设备名改成其他项目名。
- HMI 里的显示坐标和 motion daemon 的电机坐标方向不同，软限位下发前已经做了方向换算；后续修改运动目标相关逻辑时要保留这一点。
- 如果要回滚到 2026-05-22 更新前的现场版本，优先使用项目工作纪要中记录的远端备份目录。

## 依赖

### 运动守护进程

- Linux
- `gcc`
- EtherCAT 主站相关头文件和库
- `libethercat`

这部分依赖真实 EtherCAT 运行环境。没有对应硬件和主站环境时，通常只能阅读和编译源码，不能完成真实控制。

### HMI

- Python 3
- 仅使用标准库

## 编译

```bash
cd mctivity_pdo_monitor
make all
```

`Makefile` 是这套 C 程序原本就在使用的编译文件，用来统一生成几个可执行程序，适合继续保留在公开仓库里。

单独编译运动守护进程：

```bash
cd mctivity_pdo_monitor
make mctivity_motiond
```

没有完整 `make` 环境时，也可以用：

```bash
cc -O2 -Wall -Wextra -o mctivity_motiond mctivity_motiond.c -lethercat
```

## 手工运行

先启动运动守护进程：

```bash
cd mctivity_pdo_monitor
./mctivity_motiond
```

再启动 HMI：

```bash
cd mctivity_hmi
python3 mctivity_hmi.py
```

浏览器访问：

```text
http://<目标机器IP>:2015/
```

## 可选运行参数

`mctivity_hmi.py` 和 `mctivity_ctl.py` 支持环境变量或命令行参数覆盖默认地址和端口。

命令行工具示例：

```bash
python3 mctivity_ctl.py --host 127.0.0.1 --port 10001 status
```

## systemd 示例

`systemd/` 目录中的服务文件默认按下面这个部署路径示例编写：

```text
/opt/mctivity
```

如果你的目标机目录不同，改一下服务文件里的 `WorkingDirectory` 和 `ExecStart` 即可。

如果你希望像当前示例一样使用非 `root` 用户运行 `mctivity_motiond`，还需要让该用户具备访问 `/dev/EtherCAT0` 的权限。仓库中提供了一份示例规则：

```text
systemd/99-ethercat-iiru.rules
```

可按目标机器情况调整用户或组名后安装到：

```text
/etc/udev/rules.d/
```

## 许可证

本仓库使用 `GPL-3.0`。

## 作者

作者：iiru  
项目名称：mctivity
