# Klipper Driver Monitor

Klipper 驱动监测扩展：LYX9231 后台采集、使能后报警停机，以及 Fluidd 每驱动独立卡片。TMC 卡片展示 Klipper 已有缓存；网页关闭后，LYX 采集与保护继续由 Klipper 运行。

本项目由已在 FLY C8 Pro 上使用的实现整理而来。LYX 主动读取报警、转速和角度误差；TMC 卡片展示 Klipper 已有缓存。它不是原厂调参上位机的完整替代品，也不是官方 Klipper／Fluidd 插件。

## 通用安装

安装入口适用于已有 Klipper 的 Linux 主机，普通 Linux 与 FLYOS 使用同一个脚本，**不限定 C8P 或 MCU 型号**。需要 Bash 和 Python 3.8+。当前交付在 `feat/initial-distribution`，尚未合入 `main`：

```sh
git clone --branch feat/initial-distribution --single-branch https://github.com/zhangmumu903-web/klipper-driver-monitor.git
cd klipper-driver-monitor
bash install.sh
```

向导先选择“后台 + Fluidd／仅后台／仅 Fluidd”；安装后台时再选择“LYX（可同时使用 TMC）／纯 TMC”。它列出本机常见路径供确认，也可填写自定义目录；多个候选不会自动替你决定。计划展示后，输入明确的 `yes` 才写文件并备份。

```sh
# 只生成安装计划；不执行安装。
bash install.sh --plan
# 无交互终端或需要明确参数时，使用直接命令行。
bash install.sh plan --help
```

下载仓库需要网络；安装工具本身不联网、不改 CFG、不重启服务、不刷 MCU。文件安装后，按[安装手册](docs/INSTALLATION.md)加载后台配置和检查页面。纯 TMC 安装不要求 LYX 模块；使用其他前端时可只装后台，本仓库的卡片仅支持 Fluidd 的同源网站根路径部署。

## 先读这里

1. **只安装监测功能**：使用上面的通用入口；也可按[安装、升级与卸载](docs/INSTALLATION.md)分别安装后台和 Fluidd。
2. **普通 Klipper 尚不认识 LYX**：先看[驱动与 MCU 依赖](docs/DEPENDENCIES.md)。仅复制 `driver_monitor.py` 不会增加 MCU 的 Modbus UART 命令。
3. **可选的 C8P 固件工具**：仅在另需构建 C8P 固件时使用[C8P 构建脚本](docs/C8P_FIRMWARE.md)。安装监测脚本不要求使用这两个入口，其他主板仍按自己的 Klipper 编译流程处理。
4. **只想了解原理**：看[后台实现](docs/ARCHITECTURE.md)与[网页显示逻辑](docs/DISPLAY.md)。

## 中文手册

| 章节 | 内容 |
| --- | --- |
| [安装、升级与卸载](docs/INSTALLATION.md) | 路径、目标身份、预览改动、备份安装、配置加载、验收、回退 |
| [驱动与 MCU 依赖](docs/DEPENDENCIES.md) | 作者仓库来源、本项目修补、MCU 固件前提、单线 UART 边界 |
| [C8P 固件构建](docs/C8P_FIRMWARE.md) | 普通 Linux／FLYOS、USB／USB 转 CAN 1M、隔离源码、产物与配套主机安装 |
| [配置与命令](docs/CONFIGURATION.md) | CFG 三层配置、电流／细分／模式、监测命令与原生读写命令 |
| [后台实现](docs/ARCHITECTURE.md) | 调度流程图、完整 UART 事务、共享锁、缓存、日志、报警保护 |
| [网页显示逻辑](docs/DISPLAY.md) | 独立卡片、三秒缓存查询、曲线、时间、按钮、跨客户端目标校验 |
| [故障排查](docs/TROUBLESHOOTING.md) | 未发现驱动、配置或通信错误、页面无数据、保护状态 |
| [验证范围](docs/VALIDATION.md) | 当前离线检查、历史实机结果、尚未覆盖的硬件验证 |

## 功能与边界

- 后台逐驱动串行读取 `ALARM_CODE → MOTOR_SPEED → ERROR_ANGLE`，每项等完整事务结束再读下一项。
- 型号、电流设定启动尝试一次；动态历史在内存保留最多 10 分钟／每系列 600 点。
- 可选 `shutdown_on_alarm:true`：同一已生效的逻辑使能周期内读到有效非零报警，调用 Klipper shutdown，并记录中文原因。保护开启时不能暂停采集。
- Fluidd 每个已配置 LYX/TMC 一张卡片；速度／角度误差各一张最近 5 分钟曲线，失败断线、不补零。
- 网页只定时读取缓存；打开更多浏览器不会新增更多 UART 自动采集器。
- 电流、细分、运行模式放 CFG。转速／误差当前显示寄存器原值，电流设定不是实测相电流。

保护默认关闭，需要在自己的机器核实后显式开启。它依赖软件轮询和 Klipper 逻辑使能，不能代替驱动自身硬件保护。通信失败不冒充报警码；没有硬性一秒内停机保证。真实故障停机尚未实机验证，详见验证章节。

## 安装选项

直接命令支持 `--components all|backend|fluidd` 和 `--drivers lyx|tmc`，默认分别为 `all` 和 `lyx`。`--drivers` 只选择安装依赖，不隐藏运行时已有驱动。安装器保留 `plan`、`install`、`rollback`；只有 `--with-lyx` 才会安装配套三份 LYX 主机模块，并拒绝覆盖未知修改。只装后台不要求网页路径／地址；只装 Fluidd 不要求 Klipper 源码路径，也不会安装 LYX 模块。纯 TMC 可只装 Fluidd，使用现有缓存，无须新增后台配置。完整命令示例见[安装手册](docs/INSTALLATION.md)。

## 开发与离线预览

```sh
python3 -B backend/test_driver_monitor.py
python3 -B -m unittest discover -s tests -v
node --test frontend/driver-monitor.test.mjs
python3 -B -m unittest discover -s preview -p 'test_*.py' -v
python3 preview/server.py
```

Python 3.8+ 用于安装／预览工具，Node.js 18+ 用于前端测试；监测模块与配套 LYX 文件另通过 Python 3.7 语法检查。预览只监听 `127.0.0.1:18763`，明确显示模拟数据，不连接打印机。运行中的监测模块不依赖 Node.js。

## 目录

```text
backend/        Klipper 扩展、样例配置与 60 项后端测试
frontend/       无构建依赖的卡片、显示控制器及目标配置
vendor/lyx/     固定来源的三份修补版主机模块及哈希
firmware/       固定来源的 r3 UART 补丁与构建材料，不含通用固件
scripts/        本机安装工具、C8P USB／USB-CAN 隔离构建脚本
install.sh      通用交互安装入口
config/         带注释的驱动 CFG 模板
preview/        不连接打印机的模拟服务器
tests/          安装与恢复测试
docs/           中文手册
```

## 来源与许可

依赖 [zylo117/klipper](https://github.com/zylo117/klipper) 的 LYX 实现，固定基础提交 `231c50815e385e3ecae671577a9815f6f5b5d4a9`。本仓库附带的 LYX 主机模块为经过本项目修补的版本，不能标作作者未修改原版。按 GNU GPLv3 分发，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE.md)。没有包含整机配置、私有日志、凭据或预编译板卡固件。
