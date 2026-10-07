# 来源、修改与许可

本项目以 GNU General Public License version 3 分发，完整文本见 [LICENSE](LICENSE)。保留附带源文件的原有声明；许可文本来自上游固定提交的 COPYING，未修改。

- 上游：<https://github.com/zylo117/klipper>，基础提交 `231c50815e385e3ecae671577a9815f6f5b5d4a9`。该项目派生自 Klipper。
- `vendor/lyx/{lyx.py,lyx9231.py,lyx_uart.py}`：上述上游 LYX 实现的修补版本，包含本项目的读写／参数／校验修复；不是官方 Klipper 发布版。原版与修补版文件哈希见同目录 PROVENANCE.json。
- `firmware/modbus_uart-r3.patch`：本项目对固定上游 MCU 软件 UART 的起始位轮询修补；原版／补丁／结果哈希在 firmware/PROVENANCE.json。
- `backend/driver_monitor.py`、前端卡片、安装工具、测试和手册：本项目的监测扩展与整理工作。后台运行逻辑来源于 2026-10-07 实机加载版本；目标配置和安装工具在新仓库单独验证。

寄存器解释依据用户提供的《LYX步进电机驱动芯片规格书 V6.0》和对应源码。原 PDF 及原厂桌面软件没有包含在仓库；寄存器支持范围以源码和配置手册为准。此项目与 Klipper、Fluidd 或驱动厂家没有官方背书关系。

请保留许可、来源和修改说明。分发对象是源代码及少量协议说明，不包含用户整机 CFG、日志、账号、凭据、私有经验库内容或板卡固件二进制。
