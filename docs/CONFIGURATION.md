# 配置与命令

配置分为三层：Klipper 步进运动配置、原生 LYX 驱动配置、监测模块配置。三者不能互相替代。本页示例使用通用轴名，不提供可直接套用到任意主板的引脚或电流建议。

## 监测配置

将[监测配置样例](../backend/driver-monitor.cfg.sample)复制到自己的配置目录并 include，或直接添加以下单实例配置：

```ini
[driver_monitor]
auto_start: true
# 默认关闭；确认逻辑使能与驱动通信适用后，才显式启用报警停机。
shutdown_on_alarm: false
# 全部驱动一轮结束后等待，最小 0.5 秒。
cycle_interval: 1
# 相邻完整查询之间等待，最小 0.01 秒，不约束原生内部重试。
read_gap: 0.2
```

| 字段 | 默认值 | 范围与行为 |
| --- | --- | --- |
| `auto_start` | `true` | ready 后在后台自动采集，无需网页打开 |
| `shutdown_on_alarm` | `false` | 为 true 时要求 auto_start=true，并禁止暂停监测 |
| `cycle_interval` | 1.0 秒 | ≥0.5；整轮结束后等待 |
| `read_gap` | 0.2 秒 | ≥0.01；所有监测入口共用项间等待 |

这里没有 UART、波特率、电流或细分配置。它发现已经加载的 `[lyx9231 stepper_x]` 等对象；没有 LYX 对象时不会凭空创建驱动。每个已发现的 LYX 驱动在 Fluidd 各显示一张卡片，无须选择轴；本组件不查询或展示 TMC 状态，不更改机器原有 TMC 配置、运动和保护机制。

### 启用使能后报警停机

需要该保护时，将现有监测节中的开关改为以下值，不重复新增监测节：

```ini
[driver_monitor]
auto_start: true
shutdown_on_alarm: true
cycle_interval: 1
read_gap: 0.2
```

Klipper 后台会持续采集；只有在该轴逻辑使能已生效后发起，并在同一使能周期内完成的有效非零 `ALARM_CODE` 才触发整台 Klipper 停机。旧缓存、使能前发起的读取、跨失能／再使能周期的返回不触发，通信失败单独记录。关闭浏览器不影响采集和保护；保护开启时不允许暂停监测。

报警 1／2／3／4／5 对应过流／电机未连接／线圈异常／超差／堵转，其他有效非零值保留为未知报警码。停机日志包含轴、原值、中文原因和时间，网页显示同一停机原因。重新加载 Klipper 只重置主机锁存，不等同于给驱动断电清码，也不会自动恢复运动。完整条件和日志格式见[使能后报警停机](ARCHITECTURE.md#使能后报警停机)。

## 步进与 LYX 配置

完整的驱动节模板见 [lyx9231.cfg.example](../config/lyx9231.cfg.example)。保留机器原有 `[stepper_x]` 的机械范围、归零与引脚，核对以下相关项；不要创建重复的同名节。

```ini
[stepper_x]
# 这里只列关联项，其余运动配置沿用并核实自己的机器。
microsteps: 16
full_steps_per_rotation: 200

[lyx9231 stepper_x]
uart_pin: <UART_PIN>
uart_address: 1
sense_resistor: 0.050
run_current: 1.0
hold_current: 0.5
microstep: 16
driver_motor_type: 1
driver_op_mode: 2
driver_half_cur_en: 0
driver_half_cur_time: 3000
driver_boost_level: 1
driver_noise_en: 0
```

`<UART_PIN>` 必须替换为已核实的引脚；代码只证明 MCU 的 TX/RX 共用一个 GPIO，不能证明任意驱动模块 TX/RX 可以直接短接。接线与电压须依据具体模块。示例电流仅用于说明格式，不是适合所有电机的推荐值。

当前依赖驱动接受的配置为：

| 字段 | 默认值 | 限制与作用 |
| --- | --- | --- |
| `uart_pin` | 必填 | 与已支持 Modbus UART 的 MCU 引脚关联，可使用 MCU 名称前缀 |
| `uart_address` | 1 | 1–247；同共享引脚不可重复地址 |
| `sense_resistor` | 0.050 | 必须 >0，单位欧姆；应以具体模块为依据 |
| `run_current` | 1.4 | 必须 >0，名义安培；会换算并限制到寄存器 50–1900 |
| `hold_current` | 运行电流的一半 | ≥0；转换为 0–128 的比例，超过运行电流会限制为 128 |
| `microstep` | 对应 stepper 的 microsteps | 必须与其一致，且满足下述寄存器换算 |
| `driver_motor_type` | 由整步数派生 | 200 整步为 1，400 整步为 2；不能不匹配 |
| `driver_op_mode` | 2 | 当前写入校验接受 0–2；代码标签为开环／普通闭环／增强闭环，不说明特定模块全部模式已实测 |
| `driver_half_cur_en` | 0 | 0 或 1，控制半流功能开关 |
| `driver_half_cur_time` | 3000 | 当前校验 3000–30000；保留寄存器单位，不在此推定为毫秒 |
| `driver_boost_level` | 1 | 当前校验 0–8 |
| `driver_noise_en` | 0 | 0 或 1 |

波特率在依赖源码中固定为 38400，没有 `baudrate:` 配置项。`driver_run_current` 和 `driver_half_cur_ratio` 被明确拒绝，应使用 `run_current / hold_current`。并非寄存器表中每个字段都能随意写成 `driver_*` 配置；仅上表的初始化字段由当前代码读取。

### 电流、半流和细分的限制

电流换算为：

```text
scale = (0.025 / sense_resistor) × 6.4 / 2048
RUN_CURRENT = int(run_current / scale + 0.5)，限制在 50–1900
HALF_CUR_RATIO = int(hold_current / run_current × 128 + 0.5)，限制在 0–128
MICROSTEP_RATIO = 25600 / microsteps
```

细分必须整除 25600，结果在 128–25600，且 `microstep` 与运动节 `microsteps` 相等；因此当前不支持 256 细分。改变细分必须一起改两处关联配置并重新加载，不能只在线改驱动导致 Klipper 步距失配。整步数仅接受 200 或 400。

`hold_current` 只是半流比例配置；当 `driver_half_cur_en:0` 时，缓存显示的 hold_current 不能证明驱动实际进入该保持电流。即便启用半流，实际切换时序、物理相电流和电机温升仍需分别验证。当前算法和缓存不区分实测 RMS／峰值，不能据数值自行作这种解释。

已有配置加载时原 LYX 驱动会写入九项初始化寄存器并校验回读。监测模块只读，并不意味着安装重启过程零写入。本文不使用保存到芯片非易失存储的命令，也不声称配置重启验证等同于驱动断电保存。

## 监测命令

```gcode
# 读取一次完整事务，更新本模块缓存。
DRIVER_MONITOR_READ STEPPER=stepper_x REGISTER=ALARM_CODE

# 固定顺序等待报警、转速、角度误差逐项完成。
DRIVER_MONITOR_REFRESH STEPPER=stepper_x

# 仅 shutdown_on_alarm:false 时允许暂停；关闭网页不是暂停。
DRIVER_MONITOR_AUTO ENABLE=0
DRIVER_MONITOR_AUTO ENABLE=1
```

READ 的寄存器仅允许 `CHIP_MODEL / RUN_CURRENT / ALARM_CODE / MOTOR_SPEED / ERROR_ANGLE`。没有 `ATTEMPTS` 或 `GAP_MS` 参数；原生内部重试与 `read_gap` 的区别见[实现说明](ARCHITECTURE.md)。Klipper 必须 ready，模块和总线不能忙；打印、暂停或逻辑使能状态本身不禁止读取。

返回结果以 `outcome` 为准。批量读取可能仅部分完成，已完成项已缓存，其他项保留旧采样时间。HTTP 超时不意味着后台取消，客户端不应自动重发。使能后有效非零报警即使来自手动监测命令，也会按同一保护条件停机。

## 原生 LYX 命令

这些命令来自依赖驱动，不是监测模块。读取不会更新 monitor 的缓存，写入也不会自动保存 CFG；下一次加载会以 CFG 初始化。不要将下面的写入示例整段执行。

```gcode
# 原生现场读取；内部可能重试，输出原始寄存器值。
LYX_READ_REG STEPPER=stepper_x REGISTER=RUN_CURRENT
LYX_READ_REG STEPPER=stepper_x REGISTER=MICROSTEP_RATIO
LYX_READ_REG STEPPER=stepper_x REGISTER=OP_MODE

# 输出缓存写入字段，并现场读取型号、报警、速度、误差。
DUMP_LYX STEPPER=stepper_x

# 省略 CURRENT/HOLDCURRENT：只显示驱动对象缓存的设定，不现场测电流。
SET_LYX_CURRENT STEPPER=stepper_x
# 省略 MICROSTEP：显示缓存细分。
SET_LYX_MICROSTEP STEPPER=stepper_x
```

显式写入接口示例：

```gcode
# 按名义安培换算并写 RUN_CURRENT、HALF_CUR_RATIO；例值需自行确认。
SET_LYX_CURRENT STEPPER=stepper_x CURRENT=1.0 HOLDCURRENT=0.5
# 只能写与已加载 stepper 配置一致的细分，此例要求 microsteps=16。
SET_LYX_MICROSTEP STEPPER=stepper_x MICROSTEP=16
# 两者都经过写入范围校验，并要求有效写回复与回读匹配。
LYX_WRITE_REG STEPPER=stepper_x REGISTER=OP_MODE VALUE=0
SET_LYX_FIELD STEPPER=stepper_x FIELD=op_mode VALUE=0
```

在线写入白名单及范围由 `vendor/lyx/lyx9231.py` 的 `WriteRanges` 定义；`SAVE_PARAM / BAUDRATE / COMM_ADDR / MS_PIN_FUNC` 不在白名单中。`SAVE_PARAM` 也不能读取。没有自动清报警命令。读写原值与运动标定必须保持一致。

## 状态查询与显示

Moonraker 查询对象 `driver_monitor` 只取得缓存。五项值都是寄存器原值，`RUN_CURRENT=960` 不是实测 960 A；速度和误差当前未转换为 RPM、角度或位置。原驱动 DUMP 可进行有符号字段格式化，不能将其格式化结果与 monitor 的原始无符号值混为同一显示约定。

每个字段有独立结果和采样时间。`value:null` 应显示读取失败，不能把旧成功值当成新结果，也不能把通信异常里的数字当报警码。保护状态 `enabled=false` 表示逻辑未使能；`enabled=true/armed=false` 表示还未达到保护生效条件；触发后 `alarm_shutdown` 保存中文原因及时间。
