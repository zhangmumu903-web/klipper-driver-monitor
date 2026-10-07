# 安装、升级与卸载

本文在 **运行 Klipper 和 Fluidd 的 Linux 主机**上操作。安装器支持路径和目标身份配置，不通过 SSH 控制其他机器。不要在自己的电脑上把桌面目录误认为打印机的 Klipper 或网页目录。

## 1. 确认前提与目录

先按[依赖说明](DEPENDENCIES.md)确认匹配的 LYX 主机模块与 MCU 命令。已经稳定通信的机器不需要为安装监测卡片再次刷固件。

| 项目 | 常见目录示例 | FLYOS 已验证布局示例 |
| --- | --- | --- |
| Klipper 源码根 | `~/klipper` | `/data/klipper` |
| Fluidd 网页根 | `~/fluidd` | `/data/fluidd` |
| Klipper 配置 | `~/printer_data/config` | `/usr/share/printer_data/config` |

这些只是布局示例，必须以实际 service／nginx 配置为准。Klipper 目录须有 `klippy/extras`，Fluidd 目录须有 `index.html`。本安装器假设 Fluidd 位于网站根路径，并且网页同源 `/printer/*` 代理到该打印机的 Moonraker；子路径部署、跨域 API、多个机器共用一个代理、需要读取 Fluidd 内部 Bearer token 的部署不在本版范围内。

用浏览器或只读 HTTP 查看 `http://你的网页地址/printer/info`，记录返回的准确 `hostname`。`--origin` 指允许打开卡片的 Fluidd 页面来源，`--api-url` 指 Fluidd 保存的 API 地址。二者可能一个不带端口、另一个带 `:7125`。Fluidd `printer` 查询参数由 `MD5(apiUrl)` 派生，安装器会计算，无需从某一个浏览器复制 ID。

## 2. 预览安装计划

以下为文档用虚构地址，修改后执行。参数可使用绝对路径，网页地址不带末尾 `/`。

```sh
python3 scripts/install.py plan \
  --klipper "$HOME/klipper" \
  --fluidd "$HOME/fluidd" \
  --hostname printer-demo \
  --origin http://192.0.2.10 \
  --api-url http://192.0.2.10 \
  --api-url http://192.0.2.10:7125
```

`plan` 只读取与输出将创建／替换的文件，不创建备份、不写设备文件。需要允许域名或 HTTPS 入口时，再重复 `--origin` 与对应 `--api-url`；对应入口必须实际代理到相同主机。页面请求始终使用自己的同源 `/printer`，不会自动改连 `--api-url`。

默认要求已安装的三份 LYX 文件与 `vendor/lyx/PROVENANCE.json` 的修补版哈希一致。若要同时安装本仓库配套 LYX 主机模块，追加 `--with-lyx`，并重新检查计划。仅允许从缺失文件、固定作者版本或本仓库修补版本安装；检测到未知修改会拒绝，需先人工比较，不提供强制覆盖选项。TMC 原有模块不被替换。

## 3. 执行安装

确认计划后，将相同命令中的 `plan` 改为 `install`。目录需要管理员权限时，用 `sudo python3`，保留相同参数；不要为了安装改变整套目录所有者。

安装器依次：

1. 再核对目标文件与计划时一致。
2. 在 `.install-backups/时间-唯一编号/` 保存原文件、权限和 `receipt.json`；可用 `--backup-dir` 指定网页目录之外的位置。
3. 复制 `driver_monitor.py`；可选复制经过哈希检查的 LYX 三模块。
4. 生成版本化网页目录，包含 entry/core/config 三个 `.js` 及 CSS。新增静态目录明确为 0755、文件 0644；已有目录权限不变。
5. 最后在 `index.html` 的自有标记之间写入一个模块入口。已存在标记则更新，重复安装相同内容没有新改动。

它**不会**修改 `printer.cfg`、电机参数、nginx、service 定义或 MCU。遇到别的程序改动会拒绝覆盖。安装中断时按收据检查／回退；不要把“文件复制完成”直接当成 Klipper 已加载。

旧的手工安装若有未被本工具标记的 `driver-monitor` script，安装器会拒绝重复注入。先备份 `index.html`，只删除旧监测插件那一个入口标签，再重新 `plan`。旧资源目录可以保留，不影响新版本。

## 4. 添加监测配置

先备份自己的配置文件，将 `backend/driver-monitor.cfg.sample` 复制为配置目录中的 `driver-monitor.cfg`，在主配置添加一次：

```ini
[include driver-monitor.cfg]
```

样例默认 `shutdown_on_alarm:false`。需要报警停机时，在确认正确轴、逻辑使能和通信后改为：

```ini
[driver_monitor]
auto_start: true
shutdown_on_alarm: true
cycle_interval: 1
read_gap: 0.2
```

电流、细分和模式仍放 `[lyx9231 stepper_x]`，详见[配置手册](CONFIGURATION.md)。不要用监测节声明 UART 引脚，也不要粘贴示例覆盖整套机器运动配置。

## 5. 重新加载主机代码

新增或更新 Python 模块后应重新启动 **Klipper 主机进程**，使 Python 代码重新导入；单纯修改 CFG 则依设备正常的配置重载流程。选择机器空闲且允许重新初始化驱动的时机。连接初始化会按现有 LYX CFG 写寄存器，可能改变驱动设置。

在主机进程重启前查看真实服务：

```sh
systemctl cat klipper.service
systemctl show klipper.service -p ExecStartPre -p DropInPaths
```

普通部署核对没有额外设备动作后，可用 `sudo systemctl restart klipper.service`。**部分 FlyOS 的 `ExecStartPre` 含 `fly-flash`，直接重启可能执行刷写。** 如果存在该项，先按本机维护流程抑制那次自动刷写，再重启，并在完成后恢复定义；不得盲目清空其他未知的启动前任务。本仓库安装器不自动重启，因而不会隐式触发这类厂商脚本。历史验证采用临时 `/run/systemd/system/klipper.service.d/` 覆盖，确认生效后重启，最后只移除本次覆盖并重新加载服务定义，不再次重启。

不需要用 `FIRMWARE_RESTART` 代替源文件安装，也不要因为网页未刷新就重刷 MCU。

## 6. 验收顺序

1. Klipper 恢复 ready，无配置／未知 MCU 命令错误。
2. `/printer/objects/list` 存在 `driver_monitor`；`/printer/objects/query?driver_monitor` 能看到驱动列表与不断更新的采样。GET 只查缓存，不触发 UART。
3. 控制台执行一次 `DRIVER_MONITOR_READ STEPPER=stepper_x REGISTER=ALARM_CODE`，等待完整返回。不要用旧控制台输出判断本次成功。
4. Fluidd 首页显示每个已配置驱动的独立卡片；LYX 三项各有采样时间，TMC 标明缓存。离开首页停止前端查询，后台继续。
5. 开启保护时显示“使能后报警停机”，未使能待命，暂停按钮禁用。这只证明状态呈现；实际故障停机需另行安排具体的硬件验证。
6. 用另一客户端打开同一已配置页面入口，检查来源／主机校验、读数和时间。切勿通过放开任意目标来绕过配置不匹配。

只需要重新载入网页即可加载新前端资源。若发生未授权提示，本实现只沿用同源 Cookie，不提取 Fluidd token；需调整兼容的代理与正常认证方式，不能靠移除身份校验解决。

## 升级

更新本仓库后，先重新 `plan`。新前端版本目录由资源内容哈希生成；旧资源保留，最后切换入口。新的备份收据可回退本次升级。Fluidd 官方升级可能替换 `index.html`，之后重新运行计划和安装恢复入口；如果页面结构变化，参照[显示手册](DISPLAY.md)核对挂点。Klipper 更新可能覆盖 extras，请检查本模块和配套驱动，不承诺升级后自动保留。

## 卸载与回退

先移除自己的 `[include driver-monitor.cfg]` 或内联监测节，避免模块删除后配置仍引用它。保留原 LYX 电机配置，除非准备同时卸载配套驱动。回退只恢复被该收据记录的文件，不会替你编辑配置或重启服务。

```sh
# 先预览。将路径替换为安装时输出的真实收据路径。
python3 scripts/install.py rollback --receipt /path/to/receipt.json
# 核对后执行。
python3 scripts/install.py rollback --receipt /path/to/receipt.json --apply
```

回退前会核对当前内容与安装结果完全一致；发现用户后续修改或备份损坏即拒绝。只恢复原字节和权限，删除本次新建且仍与原安装内容相同的文件；不会递归清空目录。残留空版本目录和备份可保留。随后按上面的主机重载流程加载恢复后的代码，再刷新网页。

连续升级应按时间逆序回退。保管好备份和收据；收据含目标绝对路径，应仅使用本机可信安装产生的文件。
