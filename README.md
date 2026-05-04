# mctivity 1.0

`mctivity` 是一套面向 EtherCAT 伺服控制场景的双轴运动控制软件，包含运动守护进程、Web HMI 和命令行控制工具。

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
- `POST /api/command`

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
