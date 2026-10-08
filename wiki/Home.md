# Klipper Driver Monitor 中文文档

本项目为已有 LYX9231 配置提供后台监测和 Fluidd 卡片：每个 LYX 驱动各显示一张卡片，读取由 Klipper 后台运行，关闭浏览器后仍继续。此组件不查询或显示 TMC 状态。

下面的文档对应软件功能分支 `feat/initial-distribution`，尚未合并到 `main`。参数和命令以链接中的仓库文档为准。

## 从一个例子开始

阅读[大型机器四 Z 电机配置与验收示范](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/docs/examples/LARGE_MACHINE_Z.md)，了解 `stepper_z / stepper_z1 / stepper_z2 / stepper_z3` 四颗 LYX 如何分别配置、逐项读取和显示，再按自己的硬件替换[四 Z 驱动 CFG 片段](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/config/large-machine-z.cfg.example)。这是通用示范，不是可直接覆盖整机配置的文件，也不代表任意主板已实测。

案例以大型机器 Z 轴为主，卡片仍显示所有已配置并被后台发现的 LYX。X、Y、Z 的附加电机及 `extruder` 等挤出机使用 LYX 时也分别显示，不限定轴名为 Z；TMC 继续隐藏。

## 安装与配置

| 要完成的工作 | 阅读入口 |
| --- | --- |
| 在普通 Linux 或 FLYOS 安装监测功能 | [安装、升级与卸载](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/docs/INSTALLATION.md) |
| 确认 LYX 主机模块、MCU 固件和单线 UART 前提 | [驱动与 MCU 依赖](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/docs/DEPENDENCIES.md) |
| 设置电流、细分、运行模式及监测开关 | [配置与命令](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/docs/CONFIGURATION.md) |
| 另需构建 C8P USB 或 USB 转 CAN 固件 | [C8P 可选构建工具](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/docs/C8P_FIRMWARE.md) |

通用监测安装不限定 C8P，也不会替你修改 CFG、重启服务或刷 MCU。其他主板继续采用各自主板的 Klipper 固件配置。

## 读取、显示与报警

- [后台实现与报警停机](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/docs/ARCHITECTURE.md)：完整 UART 事务逐项等待、缓存、日志、使能后的保护条件。
- [Fluidd 显示逻辑](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/docs/DISPLAY.md)：一驱动一卡片、曲线、数据时间和不同客户端查看。
- [故障排查](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/docs/TROUBLESHOOTING.md)：没有卡片、通信失败、参数与保护状态问题。
- [验证记录与边界](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/docs/VALIDATION.md)：离线检查、历史实机结果和尚未完成的硬件验证。

报警停机需要在 CFG 显式开启。其判断依据是同一已生效逻辑使能周期内的新有效非零报警；通信失败另行记录。具体配置、错误原因、触发条件和恢复说明均见上述文档，示范不能代替真实设备验收。

[项目源码与版本说明](https://github.com/zhangmumu903-web/klipper-driver-monitor/blob/feat/initial-distribution/README.md)
