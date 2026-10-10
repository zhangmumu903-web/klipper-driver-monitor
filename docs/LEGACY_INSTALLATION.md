# 历史安装方式（v1.0.0 之前）

本页保留早期低层安装器、驱动准备与 format-1 收据回退说明。当前一键安装使用[新手册](INSTALLATION.md)；下文旧 `install.sh` 向导已由新入口替代，需要原行为时直接运行 `python3 scripts/setup.py`。不要将旧入口参数混用到新版脚本。

# 安装、升级与卸载

本文在 **运行 Klipper 后台或托管 Fluidd 网页的 Linux 主机**上操作。安装器不限定 MCU 型号，支持只安装所需组件，不通过 SSH 控制其他机器。源码路径和网页路径都应指向本机实际服务使用的目录。

## 0. 通用交互入口

先按下一节下载当前交付分支，再在仓库根目录运行：

```bash
bash install.sh
# 或只看计划，结束后不安装：
bash install.sh --plan
```

需要 Bash、Python 3.8+ 及交互终端。向导选择安装内容，统一安装 LYX 监测；它列出本机常见 Klipper／Fluidd 路径供确认，多候选由你选择，也支持自定义目录。只有选了 Fluidd 才需要填写主机身份和页面／API 地址。计划展示后，只有明确输入 `yes` 才执行安装；`--plan` 不进入写入步骤。没有交互终端时，使用下面的直接命令，不向向导盲目管道输入答案。

| 选择 | 参数 | 需要的信息 | 安装结果 |
| --- | --- | --- | --- |
| 后台 + Fluidd | `--components all` | Klipper、Fluidd 路径及页面目标身份 | 后台模块与网页卡片 |
| 仅后台 | `--components backend` | Klipper 路径 | 后台模块，供已有或其他前端使用 |
| 仅 Fluidd | `--components fluidd` | Fluidd 路径及页面目标身份 | LYX 网页卡片；对应 Klipper 需已有监测后台 |

本版只显示 LYX，交互向导已移除纯 TMC 选项。直接命令的 `--drivers lyx` 保留为默认值；新的 `plan/install --drivers tmc` 明确拒绝，避免安装一个不会显示目标驱动的组件。机器可同时使用 TMC，但本组件不查询或展示其状态，也不修改原有 TMC 运动和保护。`--with-lyx` 仅用于安装后台时补齐配套 LYX 主机模块，不能与“仅 Fluidd”组合。

交互向导安装后台时会带上 `--with-lyx`，安装匹配的三份模块；已有匹配文件不重复更改，未知修改仍会被拒绝。仅前端不检查这些主机文件。旧版安装生成的收据仍按其原始记录回滚，不受新的 LYX 安装范围限制。

安装工具只写本地文件并创建备份，不联网、不修改 CFG、不重启或刷固件。向导不代替加载与验收：安装后台后执行第 4–6 节；仅更新网页时刷新页面即可，无须为此重启 Klipper。卡片适用范围仍是 Fluidd 同源网站根路径；未提供 Mainsail 卡片、子路径或跨域 API 支持。

### 直接命令：按需安装

`install.sh` 的 `plan`、`install`、`rollback` 子命令直接调用原 `scripts/install.py`，可以在没有交互终端时使用。下面以普通用户目录举例；FLYOS 可将 Klipper 路径替换为核实后的 `/data/klipper`，Fluidd 替换为 `/data/fluidd`。备份应放在网页目录之外。这里展示 `plan`；核对输出后将同一条命令中的 `plan` 改为 `install` 才实际写入，权限不足时仅给必要的操作加 `sudo`。

```bash
# 仅 LYX 后台：安装配套三份模块，不需要 Fluidd 目录或页面身份。
bash install.sh plan --components backend --drivers lyx --with-lyx \
  --klipper "$HOME/klipper" --backup-dir "$PWD/.install-backups"

# 仅 Fluidd：先将下面的占位替换为实际目标；不需要 --klipper。
bash install.sh plan --components fluidd \
  --fluidd "$HOME/fluidd" --hostname REPLACE_WITH_PRINTER_INFO_HOSTNAME \
  --origin 'http://fluidd.example' --api-url 'http://fluidd.example:7125' \
  --backup-dir "$PWD/.install-backups"
```

`--origin` 和 `--api-url` 可重复指定已核实的入口。仅装 Fluidd 不会创建后台对象；显示 LYX 数据需要已有并加载 `[driver_monitor]` 的对应后台。没有 LYX 的机器不会显示 TMC 卡片。

下面第 1–6 节保留 **后台 + Fluidd、LYX 配套**的完整手动示例，默认参数等价于 `--components all --drivers lyx`。只装后台或只装网页可沿用上面的精简命令，分别跳过不适用的路径、服务或配置步骤。

## 1. 确认前提与目录

### 获取当前交付分支

当前软件在 `feat/initial-distribution` 分支，尚未合并到 `main`；此时 `main` 只有初始化 README。首次下载请显式选择下面的分支，再进入仓库。

```bash
git clone --branch feat/initial-distribution --single-branch https://github.com/zhangmumu903-web/klipper-driver-monitor.git
cd klipper-driver-monitor
git branch --show-current
git status --short
```

如果已经克隆，在现有仓库目录先只读核对远端、当前分支和未提交改动，不重复克隆到原目录，也不要盲目切换、覆盖或清理已有内容。确认远端是上述仓库、使用当前交付分支后继续。

```bash
git remote -v
git branch --show-current
git status --short
```

先按[依赖说明](DEPENDENCIES.md)确认匹配的 LYX 主机模块与 MCU 命令。已经稳定通信的机器不需要为安装监测卡片再次刷固件。

固件继续使用自己主板对应的 Klipper 编译流程。仓库另保留[C8P 可选构建工具](C8P_FIRMWARE.md)，不是通用监测安装的必经步骤；安装器不会调用它，也不会改动固件。

| 项目 | 常见目录示例 | FLYOS 已验证布局示例 |
| --- | --- | --- |
| Klipper 源码根 | `~/klipper` | `/data/klipper` |
| Fluidd 网页根 | `~/fluidd` | `/data/fluidd` |
| Klipper 配置 | `~/printer_data/config` | `/usr/share/printer_data/config` |

这些只是布局示例，必须以实际 service／nginx 配置为准。Klipper 目录须有 `klippy/extras`，Fluidd 目录须有 `index.html`。本安装器假设 Fluidd 位于网站根路径，并且网页同源 `/printer/*` 代理到该打印机的 Moonraker；子路径部署、跨域 API、多个机器共用一个代理、需要读取 Fluidd 内部 Bearer token 的部署不在本版范围内。

用浏览器或只读 HTTP 查看 `http://你的网页地址/printer/info`，记录返回的准确 `hostname`。`--origin` 指允许打开卡片的 Fluidd 页面来源，`--api-url` 指 Fluidd 保存的 API 地址。二者可能一个不带端口、另一个带 `:7125`。Fluidd `printer` 查询参数由 `MD5(apiUrl)` 派生，安装器会计算，无需从某一个浏览器复制 ID。

### 普通 Linux：普通用户目录

以下命令使用 **Bash**，在已经下载并核对版本的本仓库根目录执行。先选本节或下一节的路径设置，再继续第 2–6 节；不要把两套路径依次执行。这里安装的是本插件，前提是已有工作的 Klipper、Moonraker 和 Fluidd。

普通 Linux 示例由运行 Klipper 的普通用户执行，不要先 `sudo -i`，否则 `$HOME` 会变成 root 的家目录。服务名、源码位置、网页目录和配置文件都可能由安装者自定义；以下是待核对模板。

```bash
bash
set -euo pipefail
# 后续代码块在同一 Bash 会话中分段执行；任何检查失败都先停下核对。
KLIPPER_DIR="$HOME/klipper"
FLUIDD_DIR="$HOME/fluidd"
CONFIG_DIR="$HOME/printer_data/config"
MAIN_CONFIG="$CONFIG_DIR/printer.cfg"
KLIPPER_SERVICE='klipper.service'
INSTALL_RUN=()       # 源码和网页属于当前用户时无需 sudo。
CONFIG_RUN=()        # 配置属于当前用户时无需 sudo。
ADMIN=(sudo)        # systemd 操作使用管理员权限。

[[ $EUID -ne 0 ]] || { printf '请换成实际运行 Klipper 的普通用户。\n'; false; }
test -f scripts/install.py
test -d "$KLIPPER_DIR/klippy/extras"
test -f "$FLUIDD_DIR/index.html"
test -f "$MAIN_CONFIG"
systemctl --no-pager cat "$KLIPPER_SERVICE"
systemctl --no-pager show "$KLIPPER_SERVICE" -p User -p ExecStart -p ExecStartPre -p DropInPaths
ls -ld "$KLIPPER_DIR/klippy/extras" "$FLUIDD_DIR" "$CONFIG_DIR"
ls -l "$FLUIDD_DIR/index.html" "$MAIN_CONFIG"
```

核对 `ExecStart` 使用的源码和配置是否与变量一致，网页根目录以实际 nginx 等服务配置为准。发现路径或服务名不同，修改变量并重新检查。仅在目标文件确需管理员权限时分别改成 `INSTALL_RUN=(sudo)` 或 `CONFIG_RUN=(sudo)`；变量已经展开为该普通用户的绝对路径，不依赖 sudo 后的 `$HOME`。不要更改整套目录所有者。

### FLYOS：本项目使用过的目录布局

这套目录来自本项目已有布局，**不表示所有 FLYOS 版本都相同，也不证明当前登录的就是目标设备**。同样先在本仓库根目录打开 Bash，检查本机 service 和实际网页配置；`/usr/share/printer_data/config` 可能是链接，须核实它指向本机当前配置。

```bash
bash
set -euo pipefail
KLIPPER_DIR='/data/klipper'
FLUIDD_DIR='/data/fluidd'
CONFIG_DIR='/usr/share/printer_data/config'
MAIN_CONFIG="$CONFIG_DIR/printer.cfg"
KLIPPER_SERVICE='klipper.service'
ADMIN=()
if [[ $EUID -ne 0 ]]; then ADMIN=(sudo); fi
INSTALL_RUN=("${ADMIN[@]}")
CONFIG_RUN=("${ADMIN[@]}")

test -f scripts/install.py
test -d "$KLIPPER_DIR/klippy/extras"
test -f "$FLUIDD_DIR/index.html"
test -f "$MAIN_CONFIG"
readlink -f "$CONFIG_DIR"
systemctl --no-pager cat "$KLIPPER_SERVICE"
systemctl --no-pager show "$KLIPPER_SERVICE" -p User -p ExecStart -p ExecStartPre -p DropInPaths
ls -ld "$KLIPPER_DIR/klippy/extras" "$FLUIDD_DIR" "$CONFIG_DIR"
ls -l "$FLUIDD_DIR/index.html" "$MAIN_CONFIG"
```

FLYOS 除目录和权限不同，还必须核对 `ExecStartPre` 是否包含 `fly-flash`。安装命令本身不刷 MCU；直接重启厂商服务却可能触发它，重载必须使用第 5 节对应分支。

## 2. 预览安装计划

两种系统从这里使用相同命令，保留刚才选定的路径与权限变量。先填入当前目标的真实值；以下 `.example` 域名和主机名占位均不可直接使用，不包含凭据。URL 不带末尾 `/`。

```bash
PAGE_ORIGIN='http://fluidd.example'           # 浏览器打开 Fluidd 的来源。
API_URL='http://fluidd.example:7125'          # Fluidd 中保存的准确 API URL。
TARGET_HOSTNAME='REPLACE_WITH_PRINTER_INFO_HOSTNAME'

# 只读查看，hostname 取 result.hostname，不能用主机登录名或网页标题代替。
curl --fail --silent --show-error "$PAGE_ORIGIN/printer/info" | python3 -m json.tool
```

如果同源 GET（读取请求）要求登录，使用浏览器正常登录后查看同一 `/printer/info`；不要复制 Cookie、Bearer token 或 API key 到命令行、文档或求助输出。下面的 HTTP 验证同样适用。认证失败时先解决正常认证，不能据此判断插件安装失败。

```bash
# 先确认上面的占位已替换，再组装一次参数供 plan 和 install 共用。
[[ "$TARGET_HOSTNAME" != 'REPLACE_WITH_PRINTER_INFO_HOSTNAME' ]]
[[ "$PAGE_ORIGIN" != 'http://fluidd.example' ]]
[[ "$API_URL" != 'http://fluidd.example:7125' ]]
INSTALL_ARGS=(
  --klipper "$KLIPPER_DIR"
  --fluidd "$FLUIDD_DIR"
  --hostname "$TARGET_HOSTNAME"
  --origin "$PAGE_ORIGIN"
  --api-url "$API_URL"
  --backup-dir "$PWD/.install-backups"
)
LYX_ARGS=()  # 已安装本仓库匹配的修补版 LYX 时保持为空。
```

如 Fluidd 有多个已核实的入口或 API URL，在执行计划前追加准确值，例如 `INSTALL_ARGS+=(--origin 'https://fluidd.example' --api-url 'https://fluidd.example')`；该行仍是须替换的模板。不要把未配置的 `:7125` 地址当成必需项加入。

`plan` 只读取与输出将创建／替换的文件，不创建备份、不写设备文件。需要允许域名或 HTTPS 入口时，再重复 `--origin` 与对应 `--api-url`；对应入口必须实际代理到相同主机。页面请求始终使用自己的同源 `/printer`，不会自动改连 `--api-url`。

安装后台时，要求已安装的三份 LYX 文件与 `vendor/lyx/PROVENANCE.json` 的修补版哈希一致。若要同时安装本仓库配套 LYX 主机模块，先执行下面的可选块；它只安装主机 Python 文件，不安装 MCU 固件。

```bash
# 可选：仅在已核实需安装或更新这三份 LYX 主机模块时执行。
LYX_ARGS=(--with-lyx)
```

选好后预览计划；以后修改路径、身份或 LYX 选项，都必须重新预览。

```bash
python3 scripts/install.py plan "${INSTALL_ARGS[@]}" "${LYX_ARGS[@]}"
```

仅允许从缺失文件、固定作者版本或本仓库修补版本安装；检测到未知修改会拒绝，需先人工比较，不提供强制覆盖选项。TMC 原有模块不被替换。

## 3. 执行安装

确认计划后，在同一 Bash 会话使用相同参数安装。普通 Linux 的 `INSTALL_RUN` 默认空数组；FLYOS 按登录用户选择 root 直接执行或 sudo。

```bash
"${INSTALL_RUN[@]}" python3 scripts/install.py install "${INSTALL_ARGS[@]}" "${LYX_ARGS[@]}"
# 立即记录输出中的 Backup and rollback receipt 路径，不能使用别次安装的收据。
```

若普通用户连计划所需文件都无法读取，先核对文件归属；确需权限时可用同一 `INSTALL_RUN` 前缀运行 `plan`。不要为了安装改变整套目录所有者。

安装器依次：

1. 执行时重新生成计划，写入每个文件前核对它与本次计划的原状态一致。单独运行 `plan` 不会保存锁定，预览后如有其他修改须重新核对。
2. 在 `.install-backups/时间-唯一编号/` 保存原文件、权限和 `receipt.json`；可用 `--backup-dir` 指定网页目录之外的位置。
3. 复制 `driver_monitor.py`；可选复制经过哈希检查的 LYX 三模块。
4. 生成版本化网页目录，包含 entry/core/config 三个 `.js` 及 CSS。新增静态目录明确为 0755、文件 0644；已有目录权限不变。
5. 最后在 `index.html` 的自有标记之间写入一个模块入口。已存在标记则更新，重复安装相同内容没有新改动。

它**不会**修改 `printer.cfg`、电机参数、nginx、service 定义或 MCU。遇到别的程序改动会拒绝覆盖。安装中断时按收据检查／回退；不要把“文件复制完成”直接当成 Klipper 已加载。

旧的手工安装若有未被本工具标记的 `driver-monitor` script，安装器会拒绝重复注入。先备份 `index.html`，只删除旧监测插件那一个入口标签，再重新 `plan`。旧资源目录可以保留，不影响新版本。

## 4. 添加监测配置

先检查主配置的 include（包含配置）链。若其他配置已有 `[driver_monitor]`、或通配 include 已会加载 `driver-monitor.cfg`，不要重复添加。下面是**首次安装且尚无监测配置**的操作：用唯一备份保存主配置，不覆盖已有 `driver-monitor.cfg`。

```bash
# 只读查找线索；请结合主配置的真实 include 链确认，备份和未加载文件也可能被列出。
"${CONFIG_RUN[@]}" grep -RIn --include='*.cfg' -E '^\[include |^\[driver_monitor\]' "$CONFIG_DIR" || [[ $? -eq 1 ]]

# 确认尚无监测配置后再执行；目标已存在时 install 不应继续。
if "${CONFIG_RUN[@]}" test -e "$CONFIG_DIR/driver-monitor.cfg" || "${CONFIG_RUN[@]}" test -L "$CONFIG_DIR/driver-monitor.cfg"; then
  printf '已有 driver-monitor.cfg：先比较并沿用现有配置，不运行下面的首次复制命令。\n'
else
  CONFIG_BACKUP=$("${CONFIG_RUN[@]}" mktemp "${MAIN_CONFIG}.before-driver-monitor.XXXXXXXX")
  "${CONFIG_RUN[@]}" cp -p -- "$MAIN_CONFIG" "$CONFIG_BACKUP"
  "${CONFIG_RUN[@]}" install -m 0644 backend/driver-monitor.cfg.sample "$CONFIG_DIR/driver-monitor.cfg"
  printf '主配置备份：%s\n' "$CONFIG_BACKUP"
fi
```

使用自己的编辑器打开 `"$MAIN_CONFIG"`；有权限需求时用 `sudoedit "$MAIN_CONFIG"`。仅在它还不会通过现有 include 链加载监测文件时添加一行：

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

先在 Fluidd 核对是目标打印机且已空闲，再读取当前服务定义。不要在打印、加热、运动或其他维护任务中执行重启。下面只展示重启主机服务，不包含使能、运动、加热或刷 MCU 命令；重启时仍会进行上文所述的现有驱动初始化。

```bash
systemctl --no-pager cat "$KLIPPER_SERVICE"
systemctl --no-pager show "$KLIPPER_SERVICE" -p ExecStart -p ExecStartPre -p ExecStartPost -p ExecStop -p ExecStopPost -p DropInPaths
```

核对 `ExecStart` 及各前后置命令确为本机正常流程；未知脚本先停下调查。确认无刷写或其他不允许的设备动作后，在下面两种方案中**只执行一种**。

### 普通 Linux：无启动前任务时

以下命令拒绝存在任何 `ExecStartPre` 的服务，不会据此猜测前置任务安全。若普通 Linux 同样有厂商自动刷写，请按下一节逐项核对；有其他已知前置任务时按本机经核实的维护流程处理。

```bash
PRE_ACTIONS=$(systemctl --no-pager show "$KLIPPER_SERVICE" -p ExecStartPre --value)
if [[ -z "$PRE_ACTIONS" ]]; then
  "${ADMIN[@]}" systemctl restart "$KLIPPER_SERVICE"
  systemctl is-active "$KLIPPER_SERVICE"
else
  printf '检测到 ExecStartPre，未重启。先检查服务定义及前置任务。\n'
fi
```

### FLYOS：临时跳过已核实的 `fly-flash`

**部分 FLYOS 的 `ExecStartPre` 含 `fly-flash`，直接重启可能刷写 MCU。** 下面仅适用于已确认刷写来自 `ExecStartPre`，且其中每一项都是本次可省略的已知步骤的服务。若有未知或不可省略的前置任务，或刷写藏在 `ExecStart`、其他钩子或依赖服务里，停止使用本模板，先查清实际调用链。本仓库安装器不会自动改 service 或重启。

本模板创建唯一的临时 runtime drop-in（运行时服务补充配置），只清空 `ExecStartPre`，不覆盖原 `override.conf`。只有 systemd 报告 `ExecStartPre` 已为空才重启。退出时只删除本次文件，执行 `daemon-reload` 恢复原服务定义并核对，**不再次重启**；现有永久或运行时配置都保留。

空赋值重置命令列表及 drop-in 合并顺序依据 [systemd 服务配置说明](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml)与[unit 配置说明](https://github.com/systemd/systemd/blob/main/man/systemd.unit.xml)。文件名前缀不能替代实际生效检查，因此模板还会读取 `ExecStartPre` 确认结果。

```bash
# 在同一 Bash 会话中定义并调用；仅在上述人工核对完成且机器空闲后执行。
restart_klipper_without_pre() (
  set -euo pipefail
  [[ "$KLIPPER_SERVICE" =~ ^[A-Za-z0-9_.@:-]+\.service$ ]]
  AUDIT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/driver-monitor-reload.XXXXXXXX")
  systemctl --no-pager cat "$KLIPPER_SERVICE" > "$AUDIT_DIR/service.before"
  systemctl --no-pager show "$KLIPPER_SERVICE" -p ExecStartPre -p DropInPaths > "$AUDIT_DIR/properties.before"
  RUNTIME_DIR="/run/systemd/system/${KLIPPER_SERVICE}.d"
  "${ADMIN[@]}" mkdir -p -m 0755 -- "$RUNTIME_DIR"
  DROPIN=$("${ADMIN[@]}" mktemp "$RUNTIME_DIR/zzzz-driver-monitor-no-flash-XXXXXXXX.conf")
  printf '%s\n' "$DROPIN" > "$AUDIT_DIR/dropin.path"
  printf '本次临时文件：%s\n核对记录：%s\n' "$DROPIN" "$AUDIT_DIR"

  restore_definition() {
    result=$?
    trap - EXIT
    # 仅删除刚才 mktemp 创建的这一个文件，不删除目录或其他 override。
    if ! "${ADMIN[@]}" rm -- "$DROPIN"; then
      printf '临时文件清理失败，请按上面记录的准确路径处理。\n' >&2
      exit 1
    fi
    "${ADMIN[@]}" systemctl daemon-reload || exit 1
    systemctl --no-pager cat "$KLIPPER_SERVICE" > "$AUDIT_DIR/service.after" || exit 1
    systemctl --no-pager show "$KLIPPER_SERVICE" -p ExecStartPre -p DropInPaths
    if ! cmp -s "$AUDIT_DIR/service.before" "$AUDIT_DIR/service.after"; then
      printf '服务定义与重启前不同，请检查并发修改；这里不会再次重启。\n' >&2
      exit 1
    fi
    printf '原服务定义已恢复；未再次重启。\n'
    exit "$result"
  }
  trap restore_definition EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP

  printf '[Service]\nExecStartPre=\n' | "${ADMIN[@]}" tee "$DROPIN" >/dev/null
  "${ADMIN[@]}" systemctl daemon-reload
  systemctl --no-pager show "$KLIPPER_SERVICE" -p ExecStartPre -p DropInPaths
  PRE_ACTIONS=$(systemctl --no-pager show "$KLIPPER_SERVICE" -p ExecStartPre --value)
  if [[ -n "$PRE_ACTIONS" ]]; then
    printf 'ExecStartPre 未清空，取消重启并恢复定义。\n' >&2
    exit 1
  fi
  "${ADMIN[@]}" systemctl restart "$KLIPPER_SERVICE"
  systemctl is-active "$KLIPPER_SERVICE"
)
restart_klipper_without_pre
```

此流程只抑制本次重启的前置任务，恢复定义后，未来的普通重启仍可能执行原 `fly-flash`。shell 被强制杀死等情况可能来不及执行清理；依据输出的 `dropin.path` 核实并删除本次那个文件，再运行 `systemctl daemon-reload`，不要使用通配删除、不删除其他 override，也不要为恢复定义再重启。保留核对记录以便检查。

不需要用 `FIRMWARE_RESTART` 代替源文件安装，也不要因为网页未刷新就重刷 MCU。

## 6. 验收顺序

先执行只读验证。以下 GET 只读 Moonraker 和监测缓存，不触发手工 UART 读取；它们也不代替运动、报警或长期稳定性验证。

```bash
systemctl is-active "$KLIPPER_SERVICE"
curl --fail --silent --show-error "$PAGE_ORIGIN/printer/info" | python3 -m json.tool
curl --fail --silent --show-error "$PAGE_ORIGIN/printer/objects/list" | python3 -m json.tool
curl --fail --silent --show-error "$PAGE_ORIGIN/printer/objects/query?driver_monitor" | python3 -m json.tool
# 几秒后再读，核对采样时间是否推进；不发送 gcode/script。
```

1. Klipper 恢复 ready，无配置／未知 MCU 命令错误。
2. `/printer/objects/list` 存在 `driver_monitor`；`/printer/objects/query?driver_monitor` 能看到驱动列表与不断更新的采样。GET 只查缓存，不触发 UART。
3. 如另行安排了通信验证，可在确认的目标轴执行一次 `DRIVER_MONITOR_READ STEPPER=stepper_x REGISTER=ALARM_CODE`，等待完整返回；这是主动 UART 读取，不属于上述只读 HTTP 验收，也不是安装必要步骤。不要用旧控制台输出判断本次成功。
4. Fluidd 首页显示每个已配置并被后台发现的 LYX 的独立卡片；三项各有采样时间，TMC 不显示也不加入本组件状态查询。离开首页停止前端查询，后台继续。
5. 开启保护时显示“使能后报警停机”，未使能待命，暂停按钮禁用。这只证明状态呈现；实际故障停机需另行安排具体的硬件验证。
6. 用另一客户端打开同一已配置页面入口，检查来源／主机校验、读数和时间。切勿通过放开任意目标来绕过配置不匹配。

只需要重新载入网页即可加载新前端资源。若发生未授权提示，本实现只沿用同源 Cookie，不提取 Fluidd token；需调整兼容的代理与正常认证方式，不能靠移除身份校验解决。

## 升级

更新本仓库后，先重新 `plan`。新前端版本目录由资源内容哈希生成；旧资源保留，最后切换入口。新的备份收据可回退本次升级。Fluidd 官方升级可能替换 `index.html`，之后重新运行计划和安装恢复入口；如果页面结构变化，参照[显示手册](DISPLAY.md)核对挂点。Klipper 更新可能覆盖 extras，请检查本模块和配套驱动，不承诺升级后自动保留。

## 卸载与回退

完整卸载前先移除自己的 `[include driver-monitor.cfg]` 或内联监测节，避免模块删除后配置仍引用它；如果它由通配 include 加载，也须移出匹配范围。可对照第 4 节的主配置备份恢复本次那一行修改，不要覆盖之后新增的其他配置。回退一次升级、且旧版模块仍保留时，通常保留原监测配置，先核对计划。保留原 LYX 电机配置；若带 `--with-lyx` 的首次安装收据会删除这些新模块，须先恢复本机原有可用驱动/配置，不能在仍引用缺失模块时重启。回退只恢复被该收据记录的文件，不会替你编辑配置或重启服务。

```bash
# 两种系统相同。使用本次安装输出的真实收据，不把示例路径直接执行。
RECEIPT='/path/to/the-recorded/receipt.json'
"${INSTALL_RUN[@]}" test -f "$RECEIPT"
# 先预览；沿用安装时的权限前缀，root 创建的收据可能只有 root 能读取。
"${INSTALL_RUN[@]}" python3 scripts/install.py rollback --receipt "$RECEIPT"
# 核对后执行。
"${INSTALL_RUN[@]}" python3 scripts/install.py rollback --receipt "$RECEIPT" --apply
```

回退前会核对当前内容与安装结果完全一致；发现用户后续修改或备份损坏即拒绝。只恢复原字节和权限，删除本次新建且仍与原安装内容相同的文件；不会递归清空目录。残留空版本目录和备份可保留。随后按上面的主机重载流程加载恢复后的代码，再刷新网页。

连续升级应按时间逆序回退。保管好备份和收据；收据含目标绝对路径，应仅使用本机可信安装产生的文件。
