# 安装、升级、卸载

v1.0.0 的入口只安装监控程序和 Fluidd 卡片，普通 Linux 与 FLYOS 共用同一套脚本。没有特定主板限制，也不会自动安装驱动依赖、刷 MCU、改变电流/细分/模式或重启服务。

## 安装前提

- Linux 上位机已有正常运行的 Klipper、Moonraker；装卡片还需 Fluidd。
- Bash、Python 3.8+；联网入口另需 curl 和访问 GitHub。
- 后台需要已有与 `vendor/lyx/PROVENANCE.json` 中 `patched_sha256` 一致的三份 LYX Python 模块。安装器严格核对，不覆盖其他版本；这避免监控依赖的原生读取和使能接口不匹配。
- MCU 已具备对应的 Modbus UART 接口，原生命令能读成功。文件检查不能证明 MCU 已刷对或 UART 稳定，见[依赖说明](DEPENDENCIES.md)。
- 以运行 Klipper 的用户安装。目标目录须有写权限；不要为方便默认切成 root，否则状态目录会落到另一个用户下。

先在原有控制台核对一颗实际配置的驱动：

```gcode
LYX_READ_REG STEPPER=stepper_z REGISTER=ALARM_CODE
```

`stepper_z` 换成实际对象名。此命令只读；不要为安装改变 EN 或运动配置。依赖缺失时安装预检会退出，不会自动补装 LYX、替换整个 Klipper 或尝试刷写。

## 一键入口

```bash
curl --fail --location --show-error \
  https://github.com/zhangmumu903-web/klipper-driver-monitor/releases/latest/download/install.sh \
  -o /tmp/klipper-driver-monitor-install.sh && \
bash /tmp/klipper-driver-monitor-install.sh
```

下载最新正式稳定 Release 的完整包并核对 SHA256，保存在当前用户 `~/.local/share/klipper-driver-monitor/packages/` 下的独立版本目录。解包拒绝目录穿越、链接和异常体积；校验失败不会安装。校验用于检查包与同一发布者的清单是否一致，不是独立签名。

向导填写本次实例的路径、主机名及网页地址，先展示计划，输入 `yes` 才写入。找到多个路径时必须选择，不能把 A 实例的 Klipper 和 B 实例的 Fluidd 混合。`--plan` 只预览目标改动，下载入口本身仍会下载包。

成功结果中的 `tools_dir` 是后续管理命令所在目录，`state` 是安装清单，`receipt` 是本次备份记录。保留这些目录，不要把备份放进 Fluidd 网页根目录。

## 普通 Linux 与 FLYOS 的命令区别

区别是实际路径，不是安装功能。以下为**示例**：`192.0.2.10` 是文档地址，`printer-example` 必须换成 `/printer/info` 返回的真实 `hostname`；`hostname` 命令可作线索，最终以 Moonraker 返回为准。

已经取得完整源码或 Release 包后，进入包目录执行。普通 Linux 常见路径：

```bash
bash install.sh \
  --klipper "$HOME/klipper" \
  --config "$HOME/printer_data/config/printer.cfg" \
  --fluidd "$HOME/fluidd" \
  --hostname printer-example \
  --origin http://192.0.2.10 \
  --api-url http://192.0.2.10:7125 \
  --state-dir "$HOME/.local/share/klipper-driver-monitor/printer-a"
```

FLYOS 常见路径（不同镜像需按实际调整）：

```bash
bash install.sh \
  --klipper /data/klipper \
  --config /usr/share/printer_data/config/printer.cfg \
  --fluidd /data/fluidd \
  --hostname printer-example \
  --origin http://192.0.2.10 \
  --api-url http://192.0.2.10:7125 \
  --state-dir "$HOME/.local/share/klipper-driver-monitor/printer-a"
```

`--origin` 是浏览器页面的协议、主机与端口，没有路径或末尾斜杠，可重复。`--api-url` 是 Fluidd 保存的确切 API 地址，用于校验 `?printer=` 的实例 ID，可重复；不是跨域请求目的地。组件实际使用当前页面同源 `/printer/...` 接口，需要该部署的代理及认证方式兼容，参见[显示说明](DISPLAY.md#同源与鉴权边界)。安装器不会开放 Moonraker 权限或读取浏览器密码/令牌。

多实例必须使用不同 `--state-dir`。无此参数时默认为 `~/.local/share/klipper-driver-monitor/state`。创建后路径保存在清单里，后续命令只需沿用同一个状态目录。

## 选择组件与安装变更

```bash
bash install.sh --plan          # 仅预览，缺少参数时可交互填写
bash install.sh --frontend-only # 后台已有，只装 Fluidd
bash install.sh --backend-only  # 只装监控，不要求 Fluidd
```

非交互执行必须提供全部所需参数，并显式加 `--yes` 才执行已打印的计划。可先用相同参数加 `--plan` 检查。

后台安装只写 `klippy/extras/driver_monitor.py`。若当前有效 CFG 已有 `[driver_monitor]`，沿用该节和参数；否则创建独立 `driver-monitor.cfg`，在主 CFG 的 `SAVE_CONFIG` 自动保存块之前加受管 include。已有通配 include 会与新文件重复时停止，要求在用户已有包含文件中建立监控节后重试。

新生成配置：

```ini
[driver_monitor]
auto_start: true
shutdown_on_alarm: false
cycle_interval: 1.0
read_gap: 0.2
```

已有保护值保持。若需要使能后报警停机，按[参数说明](CONFIGURATION.md)设置 `shutdown_on_alarm: true`，这是用户的监控配置选择。安装器不更改 `[lyx9231 ...]`、`[stepper_...]` 或 MCU 节。

前端安装生成带内容版本号的独立资源目录，在 Fluidd `index.html` 只添加本组件脚本标记；首次创建 `driver-monitor-user/` 默认布局和样式。配置、身份与 URL 不硬编码进公共源码。

## 启用和验收

文件写入不等于已运行。新装/升级/卸载后台后，结果会提示 `restart_required:true`，须在机器空闲并确认现有启动钩子后通过原有方式重启 Klipper 主机进程。该重启会按现有 LYX CFG 重新初始化驱动；安装器自己不重启。

1. Klipper ready 后运行 `bash doctor.sh`（自定义状态目录仍加 `--state-dir`）。
2. `files_ok` 表示受管文件符合记录；`monitor_loaded:true` 才表示查询到了运行对象。401、连接失败或对象缺失会保留待验证状态。
3. 刷新 Fluidd，每个实际加载的 LYX 轴独立显示。TMC 不显示；速度、角误差和报警依次采集，多客户端只读同一个后台缓存。
4. 查看读数时间、失败提示、`klippy.log` 与统计。安装验收不包含主动使能、运动或制造报警。

`doctor` 的本地默认接口为 `http://127.0.0.1:7125`，其他实例可设置 `--moonraker-url`。诊断请求只读，不会发送 G-code。

## 更新、修复与自定义

在 `tools_dir` 内执行，多实例均加对应 `--state-dir`：

```bash
bash update.sh                 # 下载并验证最新稳定包再更新
bash update.sh --local         # 使用当前完整包，适合离线
bash repair.sh                 # Fluidd 自身更新后恢复卡片入口
bash repair.sh --reset-layout  # 明确恢复默认布局和 CSS
bash doctor.sh                 # 检查文件和运行状态
```

更新记录新的工具目录，保留用户监控 CFG、`driver-monitor-user/` 的布局、CSS、附加渲染器和电机参数。普通修复也保留自定义。受管 `driver-monitor.cfg` 被删除时，修复恢复默认模板；丢失的用户手改参数须从自己的备份恢复。受管程序遭到未知手改时拒绝覆盖，并报告具体文件；先保留自己的修改再决定处理。

Fluidd 更新若替换 `index.html`，运行 `repair.sh` 重加自己的标签，不恢复旧版整份 Fluidd 文件。卡片样式无需重启 Klipper，编辑后刷新浏览器即可；详见[自定义显示](CUSTOMIZATION.md)。

## 卸载与回滚

```bash
bash uninstall.sh --frontend-only # 只卸网页，后台采集和保护保留
bash uninstall.sh --backend-only  # 只卸后台，网页会显示未加载
bash uninstall.sh                 # 卸载清单中的所有组件
bash rollback.sh                  # 回退最近一次受管事务
```

卸载会先检查所有目标，再移除受管文件和标记。保留 LYX/TMC、运动配置、MCU、用户自定义网页文件、安装包和备份。它不是把旧备份整包覆盖回去。

若监控节由用户自行维护，先按提示移除该节及对应加载关系，才能卸后台，避免 Klipper 缺模块无法启动。安装器创建的监控 CFG 如果后来被手改，也会拒绝直接删除；先备份并处理提示文件。不能用 `--yes` 绕过未知改动保护。

完整卸载后台在 Klipper 重载后停止本组件的报警保护。只想换卡片时使用 `--frontend-only`。回滚会核对备份与现文件，保留配置/Fluidd 入口周边无关改动；未知并发修改会拒绝，失败事务保留 `pending.json`，必须先回滚再进行其他安装变更。

## 早期版本迁移

早期 `scripts/install.py`/`scripts/setup.py` 与本版状态格式不同。已有部署需要先预览：

```bash
bash install.sh --adopt-existing --plan
bash install.sh --adopt-existing
```

仅接管完全一致的后台文件和内容哈希符合名称的旧前端目录；未知版本拒绝覆盖。原监控 CFG 保持用户所有，原备份保留。旧收据回退使用[历史安装手册](LEGACY_INSTALLATION.md)，不要把旧 format-1 收据作为新 `--state-dir`。

该选项不会安装或修改 LYX 依赖。旧 `--with-lyx` 只属于手动准备依赖的低层工具，不属于 v1.0.0 一键监控安装。
