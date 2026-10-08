# 示范案例：大型机器四 Z 与全轴 LYX 监测

本例以大型机器的四个 Z 电机为主：`stepper_z`、`stepper_z1`、`stepper_z2`、`stepper_z3` 各使用一颗 LYX9231，后台依次读取报警、转速和角度误差，Fluidd 自动展示四张独立卡片，并在满足使能条件的新报警出现时停机。

这是**通用配置示范，不是四 Z 驱动实机验收报告**。适用于已有 Klipper 的 Linux／FLYOS 主机，不限定 C8P；使用其他主板时，MCU 固件、引脚和模块接线仍以实际硬件为准。配套文件为 [large-machine-z.cfg.example](../../config/large-machine-z.cfg.example)。


## 主要用于 Z，但显示不限制轴名

后台发现全部已加载的 `[lyx9231 ...]` 对象；前端为每个对象生成独立卡片，没有只允许 X/Y/Z 三个名称的列表。例如：

| 现有运动对象 | 对应驱动配置节 | 显示 |
| --- | --- | --- |
| `stepper_z`、`stepper_z1`、`stepper_z2`、`stepper_z3` | 各自的 `[lyx9231 stepper_z...]` | 四个 Z 电机各一张卡片 |
| `stepper_x`、`stepper_y` | `[lyx9231 stepper_x]`、`[lyx9231 stepper_y]` | X/Y 各自一张卡片 |
| `extruder`、`extruder1` | `[lyx9231 extruder]`、`[lyx9231 extruder1]` | 各挤出电机分别显示 |

前提是对应运动对象确实存在、该驱动被 Klipper 正常加载；仅复制名称不创建新轴或改变运动学。可以只装一颗 Z，也可以在四 Z 外再加 X/Y/挤出机。TMC 仍按项目范围隐藏。“全轴”指所有已配置的 **LYX** 轴，不是恢复 TMC 卡片。

多个 Z 的归零、同步和调平仍由已有 Klipper 运动配置负责；本模块不会新增 `z_tilt`、`quad_gantry_level` 或独立移动逻辑。报警触发的是整台 Klipper shutdown，不是只停故障 Z 电机，也不是机械抱闸或防坠控制。大型 Z 启用停机保护前，应核实失能后的载荷保持措施；共享 EN 时也不能把单轴逻辑状态当作每颗电机的物理使能反馈。

## 1. 示例机器与前提

| 项目 | 本例假设 | 使用时处理 |
| --- | --- | --- |
| 轴对象 | `stepper_z`、`stepper_z1`、`stepper_z2`、`stepper_z3` | 必须是已有、完整的运动配置节 |
| 驱动 | 四颗 LYX9231 | 已有匹配的 LYX 主机模块与 MCU UART 命令 |
| UART | 四条独立单线，四个不同 GPIO | 分别填写 `<Z_UART_PIN>`、`<Z1_UART_PIN>`、`<Z2_UART_PIN>`、`<Z3_UART_PIN>` |
| 模块通信设置 | 四颗分别为 38400、地址 1 | 先确认模块本身的设置；CFG 不会给模块改地址 |
| 电机与细分 | 200 整步／转，16 细分 | 运动节与驱动节必须一致 |
| 采样电阻 | 四颗均为 0.050 Ω | 本例假设值，查具体模块资料，不能仅凭芯片型号认定 |
| 电流与模式 | 运行 1.0 A、保持比例对应 0.5 A、模式 2 | 仅说明格式，1.0 A 不是大型 Z 电机推荐值，须换成已核实参数 |

保留自己的 STEP／DIR／EN 极性、`rotation_distance`、归零和机械行程。本例不提供通用的 EN 极性，也不替换完整 `printer.cfg`。同一个轴由 LYX 配置接管时，不能同时保留该轴原有 TMC 驱动节；其他轴仍可继续使用 TMC，本组件不显示其卡片。

通信拓扑示意只表示信号归属，不是焊线图：

```text
MCU 的 Z_UART GPIO  ── Z 模块已确认的单线接口（地址 1）
MCU 的 Z1_UART GPIO ── Z1 模块已确认的单线接口（地址 1）
MCU 的 Z2_UART GPIO ── Z2 模块已确认的单线接口（地址 1）
MCU 的 Z3_UART GPIO ── Z3 模块已确认的单线接口（地址 1）
```

四个地址 1 位于四条不同总线，不冲突。若改用一条共享单线，需另外确认模块支持、电气连接和不同的实际从站地址，不能照抄本例的四个地址 1。源码 TX/RX 共用 GPIO 不等于任意模块 TX/RX/NC 都可直接短接；共地、逻辑电平及短接位置须按模块厂商说明。详见[依赖与单线 UART 边界](../DEPENDENCIES.md)。

## 2. 安装监测文件

在运行 Klipper 的主机上，首次安装可用以下入口；已经克隆过的仓库先核对分支和本地改动，不重复覆盖：

```bash
git clone --branch feat/initial-distribution --single-branch https://github.com/zhangmumu903-web/klipper-driver-monitor.git
cd klipper-driver-monitor
bash install.sh --plan
```

选择“后台 + Fluidd”，核对源码目录、网页目录、`/printer/info` 的 hostname 及 Fluidd 页面/API 地址。确认计划后执行 `bash install.sh`，检查计划并输入 `yes` 才写入。两种系统使用同一入口，常见路径区别如下：

| 项目 | 普通 Linux 常见值 | FLYOS 已使用过的布局 |
| --- | --- | --- |
| Klipper | `~/klipper` | `/data/klipper` |
| Fluidd | `~/fluidd` | `/data/fluidd` |
| CFG 目录 | `~/printer_data/config` | `/usr/share/printer_data/config` |

这些不是路径检测结果，必须与本机实际服务一致。安装工具不会改 CFG、重启或刷 MCU。完整安装、权限及回退步骤见[安装手册](../INSTALLATION.md)。已稳定运行匹配 LYX 固件的机器不必为增加卡片再次刷写。

## 3. 配置四颗 Z 驱动与一个后台

打开[带注释模板](../../config/large-machine-z.cfg.example)，替换四处引脚占位符，逐轴核对电流、电阻、模式、细分和整步数。配置结构是：

```text
已有 stepper_z / stepper_z1 / stepper_z2 / stepper_z3
        ↓ 每个运动节与同名 LYX 驱动节关联
lyx9231 stepper_z / stepper_z1 / stepper_z2 / stepper_z3
        ↓ 全部共用一个后台
[driver_monitor]
```

将核对后的新增片段保存为本机配置目录中的 `lyx-monitor.cfg`，主配置只 include 一次：

```ini
[include lyx-monitor.cfg]
```

已有同名 LYX 或监测节时编辑原节，不再重复 include。示例中 `microstep: 16` 要与各自运动节的 `microsteps: 16` 相等；`driver_motor_type: 1` 对应 `full_steps_per_rotation: 200`。不要把局部示例当成完整运动配置。

本例在唯一的 `[driver_monitor]` 中显式使用：

```ini
[driver_monitor]
auto_start: true
shutdown_on_alarm: true
cycle_interval: 1
read_gap: 0.2
```

这是对模板内容的说明，不是让你再添加第二个监测节。`true` 是本例主动开启保护，软件默认仍为 `false`。半流示例 `driver_half_cur_en: 0` 表示没有开启自动半流，不能把 `hold_current` 当成已测得的保持电流。参数限制与可写范围见[配置手册](../CONFIGURATION.md)。

按[安装手册第 5 节](../INSTALLATION.md#5-重新加载主机代码)的实际服务分支重新加载；FLYOS 尤其先检查 `ExecStartPre` 是否包含 `fly-flash`，不要直接套用服务重启命令。LYX 原生模块重新连接时会根据 CFG 写入并回读初始化寄存器；后台采集只读不等于重新加载过程没有写入。

## 4. 查看和逐项验收

Klipper ready 后先让自动采集完成，Fluidd 仪表板应出现 `stepper_z`、`stepper_z1`、`stepper_z2`、`stepper_z3` 四张 LYX 卡片。每张卡片有独立报警、转速原值、角度误差原值和两条曲线，没有驱动选择框。插上模块但未配置 LYX 对象，不会凭空出现卡片。

只配置本例四 Z 时，正常动态轮次按驱动名称排序，逐项等待返回；另有 X/Y/挤出机等 LYX 时也会加入同一个串行轮次：

```text
Z 报警 → Z 转速 → Z 误差
→ Z1 报警 → Z1 转速 → Z1 误差
→ Z2 报警 → Z2 转速 → Z2 误差
→ Z3 报警 → Z3 转速 → Z3 误差
→ 等轮间隔 → 下一轮
```

相邻完整查询之间遵守 `read_gap`；`cycle_interval` 从全部驱动处理完才开始计时。保护开启时首轮还会在每轴动态三项后尝试型号和电流设定，后续动态轮次不反复读取它们。通信重试可能延长一轮，不能把 `cycle_interval: 1` 理解成一秒更新全部数值。

需要手动核对时，**每次只发一条，等结果再继续**。后台忙碌会拒绝手动读取，此时先看卡片缓存，等空闲后再手动操作，不循环重发：

```gcode
# 仅现场读 Z 报警，并更新监测缓存；符合保护条件时同样可以触发停机。
DRIVER_MONITOR_READ STEPPER=stepper_z REGISTER=ALARM_CODE
# 对 Z1 做一次报警 → 转速 → 误差的完整串行刷新。
DRIVER_MONITOR_REFRESH STEPPER=stepper_z1
```

CFG 初始化值的现场回读使用原生命令，不会直接更新卡片缓存；下面以 Z/Z1 为例逐条执行，Z2/Z3 使用相同命令并换成对应轴名：

```gcode
LYX_READ_REG STEPPER=stepper_z REGISTER=RUN_CURRENT
LYX_READ_REG STEPPER=stepper_z REGISTER=MICROSTEP_RATIO
LYX_READ_REG STEPPER=stepper_z REGISTER=OP_MODE
LYX_READ_REG STEPPER=stepper_z1 REGISTER=RUN_CURRENT
LYX_READ_REG STEPPER=stepper_z1 REGISTER=MICROSTEP_RATIO
LYX_READ_REG STEPPER=stepper_z1 REGISTER=OP_MODE
```

仅当实际使用本例假设参数时，按当前源码换算的预期值如下；这是计算结果，不是新实机回读记录：

| 寄存器 | 本例预期原值 | 依据 |
| --- | --- | --- |
| `RUN_CURRENT` | 640 | `scale=(0.025/0.050)×6.4/2048`，`round(1.0/scale)` |
| `MICROSTEP_RATIO` | 1600 | `25600/16` |
| `OP_MODE` | 2 | 本例 `driver_op_mode: 2` |

寄存器回读匹配只证明配置值读取一致，不证明实测相电流、机构精度或闭环效果。速度和误差目前显示原始无符号寄存器数值，不标作 RPM 或度。

## 5. 报警与日志应该怎样表现

| 情况 | 本例启用保护后的预期行为 |
| --- | --- |
| 轴未使能，读到有效非零报警 | 显示/记录报警，不因该读数触发本保护停机 |
| 使能已经生效，在同一次使能周期中新读到有效非零报警 | 整个 Klipper 停机，记录轴、报警码、中文原因和时间 |
| 使能前旧缓存，或读取跨越失能/再次使能 | 不拿该结果触发停机，等待符合条件的新读取 |
| CRC 错误、无有效回复、查询耗尽 | 记录通信失败，不把失败里的数字当报警码 |
| 打开多台电脑/手机，或关闭网页 | 仍由同一个 Klipper 后台采集与判定；客户端只读缓存 |

报警 1/2/3/4/5 分别为过流、电机未连接、线圈异常、超差、堵转；其他有效非零值按未知报警处理。日志通常在 `klippy.log`，最终以 Klipper 服务实际日志路径为准。示意消息如下，**不是本例触发的现场日志**：

```text
LYX 驱动报警停机：轴 stepper_z1，原因：堵转，ALARM_CODE=5 (0x0005)，unix=<时间> monotonic=<时间>。驱动报警需断电重新上电清除；Klipper 重启不代表驱动报警已清除。
```

据使用者提供的驱动行为，报警需驱动断电重新上电清除；软件不自动清码，也不自动恢复运动。使能来自 Klipper 逻辑和计划生效时间，不是 EN 引脚反馈；轮询没有硬性一秒停机保证。完整条件见[实现说明](../ARCHITECTURE.md#使能后报警停机)。本示范不要求制造堵转、断线或过流来验收。

## 6. 本例完成标准与验证边界

- 配置层：四个已加载的 Z 轴 LYX 对象、一个监测对象，各轴参数回读与自己的 CFG 相符。
- 显示层：四张独立 LYX 卡片，读取时间分别更新；失败显示失败，不补零；TMC 不生成卡片。
- 后台层：关闭网页后仍采集，重新打开可看到保留窗口内的历史；开启保护时暂停采集按钮不可用。
- 保护层：以后台源码与既有离线测试说明判定条件，真实故障停机仍待单独实机验证。

本例的 CFG 字段、寄存器换算和链接经过离线核对；多驱动模拟显示与历史单机结果见[验证记录](../VALIDATION.md)。本轮没有部署模板、使能或移动电机、刷写固件。更多轴可按同样方式追加实际 LYX 驱动节，无须复制监测节或前端卡片代码。
