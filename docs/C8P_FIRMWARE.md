# C8P：USB 与 USB 转 CAN 固件

本页针对 **FLY C8 Pro／STM32H723、128 KiB 引导偏移、25 MHz 晶振**。H618 是运行 Linux 的上位机；生成的 `klipper.bin` 是给 H723 MCU 的应用固件。改过引导程序、使用其他主板或其他晶振时，不适用这两个固定预设。

| 入口 | 主机与 MCU 的连接 | 驱动通信能力 |
| --- | --- | --- |
| `scripts/build-c8p-usb.sh` | 普通 USB，PA11／PA12 | LYX Modbus UART、TMC UART、SPI |
| `scripts/build-c8p-canbridge.sh` | USB 转 CAN 桥接；USB PA11／PA12，CAN RX PB8／TX PB9，1,000,000 bit/s | LYX Modbus UART、TMC UART、SPI |

两者共用 `scripts/build-c8p.py`。驱动侧单线 UART 与主机侧 USB／CAN 是不同链路；桥接版的 1M 不会把 LYX 的 UART 改成 1M。LYX 配套主机模块仍使用 38400、8N1。

脚本只构建，**不会刷写 MCU、重启服务、改运行中的源码或安装主机模块**。成功编译证明产物通过静态检查，不代表实机连接、驱动电流、运动或报警保护已经验证。

## 1. 获取工具与编译依赖

在 Linux 主机使用 Bash。下载当前交付分支；本分支尚未合入 `main`：

```bash
git clone --branch feat/initial-distribution --single-branch https://github.com/zhangmumu903-web/klipper-driver-monitor.git
cd klipper-driver-monitor
git branch --show-current
python3 scripts/build-c8p.py --help
```

Python 要求 3.8+，编译需要 GNU Make、C 预处理器和 ARM 交叉工具链。Debian／Ubuntu 可安装以下依赖；FLYOS 若已具备，可跳过安装，仅核对工具。依赖安装是系统包操作，构建脚本不会自动执行它。

```bash
# Debian／Ubuntu 普通用户；以 root 登录时去掉两行开头的 sudo。
sudo apt-get update
sudo apt-get install --no-install-recommends git python3 build-essential binutils \
  gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi

python3 --version
make --version
arm-none-eabi-gcc --version
command -v readelf arm-none-eabi-cpp arm-none-eabi-objcopy arm-none-eabi-objdump
```

ARM 软件包名称可对照[固定作者版本的 Ubuntu 安装脚本](https://github.com/zylo117/klipper/blob/231c50815e385e3ecae671577a9815f6f5b5d4a9/scripts/install-ubuntu-22.04.sh)。这里只取构建所需依赖，不运行那个完整安装脚本；完整脚本还会创建主机环境和服务。非 Debian 系统使用自己的包管理器安装等价工具。

## 2. 选择输入源码

两个入口均要求 `--source`。它应指向**可信的 Klipper 源码根目录**，不是固件输出目录，也不是 `printer_data/config`。构建会执行源码中的 Makefile 与脚本。若路径经过软链接，先用 `readlink -f` 查明实际目录，再把确认后的实际路径传入；脚本拒绝软链接。工作目录和工具链路径不能包含空格或 Make／Shell 特殊字符。

### 普通 Linux：优先使用本机源码

常见源码目录为 `~/klipper`；实际以 Klipper 服务的启动路径为准。已在使用厂商分支时也沿用它，不仅凭系统名称换成另一套源码。

```bash
KLIPPER_SOURCE="$HOME/klipper"
test -f "$KLIPPER_SOURCE/Makefile"
test -f "$KLIPPER_SOURCE/src/Kconfig"
test -d "$KLIPPER_SOURCE/klippy"
```

如果没有现有源码、明确要用原作者基础，可在一个**不存在的新目录**下载固定版本。此处只准备编译输入，不替换正在运行的 Klipper：

```bash
KLIPPER_SOURCE="$HOME/klipper-lyx-c8p-source"
test ! -e "$KLIPPER_SOURCE"
git clone https://github.com/zylo117/klipper.git "$KLIPPER_SOURCE"
git -C "$KLIPPER_SOURCE" checkout --detach 231c50815e385e3ecae671577a9815f6f5b5d4a9
git -C "$KLIPPER_SOURCE" rev-parse HEAD
```

该固定作者版本和另一套 Klipper 主机不一定兼容。使用者还需核对运行主机版本、厂商扩展和 MCU 命令；本仓库的三个 LYX 主机模块不会替换整套 Klipper 基础版本。

### FLYOS：保留本机厂商接口

本项目使用过的源码目录为 `/data/klipper`。先核对当前服务是否确实从这里启动；不同 FLYOS 版本可能不同。

```bash
KLIPPER_SOURCE='/data/klipper'
test -f "$KLIPPER_SOURCE/Makefile"
test -f "$KLIPPER_SOURCE/src/Kconfig"
test -d "$KLIPPER_SOURCE/klippy"
systemctl --no-pager show klipper.service -p ExecStart -p ExecStartPre
```

以它作为 `--source`，构建脚本在独立副本内添加 LYX 接入，保留输入源码的其他厂商接口。不会清除 `/data/klipper/.config`、`out/` 或更换原服务源码。**不要为了编译运行 `systemctl restart klipper`**；部分 FLYOS 的启动前任务会自动刷写固件。

## 3. 选择一个构建入口

在本仓库根目录、同一 Bash 会话中执行。每次构建使用一个不存在的新目录；保留旧产物便于核对和回退，不复用旧目录覆盖。

```bash
# 普通 USB。KLIPPER_SOURCE 来自上一节所选的那一种设置。
bash scripts/build-c8p-usb.sh --source "$KLIPPER_SOURCE" \
  --work-dir "$HOME/c8p-usb-build-$(date +%Y%m%d-%H%M%S)" --jobs 4
```

或：

```bash
# USB 转 CAN 桥接，CAN 速率固定为 1,000,000 bit/s。
bash scripts/build-c8p-canbridge.sh --source "$KLIPPER_SOURCE" \
  --work-dir "$HOME/c8p-canbridge-build-$(date +%Y%m%d-%H%M%S)" --jobs 4
```

省略 `--work-dir` 时脚本创建临时构建目录，并输出实际路径。想长期保存固件时，建议明确传入自己的新目录。内存较少时改成 `--jobs 1`；脚本不自动安装缺失工具。

已有单独安装的工具链时，可以追加 `--cross-prefix /实际工具链目录/bin/arm-none-eabi-`，前缀包含最后的连字符。省略时使用 `PATH` 中的 `arm-none-eabi-` 工具；不会为了切换工具链改动系统安装。

只想检查源码接入、不编译时，增加 `--prepare`，同样使用新的目录：

```bash
bash scripts/build-c8p-usb.sh --source "$KLIPPER_SOURCE" \
  --work-dir "$HOME/c8p-usb-prepare-$(date +%Y%m%d-%H%M%S)" --prepare
```

`--prepare` 仅复制、集成及生成配置种子，状态为 `prepared-not-built`，没有可刷写产物。公共 Python 入口的等价形式为 `python3 scripts/build-c8p.py --mode usb ...` 或 `--mode canbridge ...`。

## 4. 构建过程与产物

脚本复制源码白名单：`Makefile`、`COPYING`、`src/`、`lib/`、`scripts/`、`klippy/`。不复制 `.git`、旧 `.config`、旧 `out/` 或整机配置／日志；复制范围中的文件按实际内容记录哈希，因此输入源码中的未提交修改也属于本次输入。拒绝软链接及特殊文件，未知的 Modbus 实现或构建接入需人工核对，不能以强制覆盖解决。

随后在副本中集成固定来源的 r3 Modbus UART，生成 C8P 配置，执行配置展开及编译，检查：

- H723、128 KiB 偏移／应用地址 `0x08020000`、25 MHz、所选 USB／桥接模式。
- LYX Modbus UART、TMC UART、SPI 所需配置和产物中的命令。
- 固件地址／向量、容量和文件哈希。

构建完成的状态为 `built-not-flashed`。目录结构如下：

```text
本次构建目录/
  source/          独立源码副本及本次编译结果
  artifacts/       klipper.bin、klipper.elf、klipper.dict、.config、SHA256SUMS
  build.log        本次构建输出
  manifest.json    来源、配置、检查结果和产物哈希
```

读取脚本最后输出的实际路径，核对 `manifest.json` 中模式及检查结果；在 `artifacts/` 内执行 `sha256sum -c SHA256SUMS` 检查复制或下载后的文件。失败目录和日志可保留用于排错；不要把已有同名 `klipper.bin` 当成本次成功。

`source/` 和 `manifest.json` 记录本次本地输入，分享前自行检查其中路径及本机源码内容；本仓库不会自动上传它们。固件中的源码版本信息仅用于辨识来源，不能代替构建清单或运行验证。

## 5. 配套主机模块与 CFG

MCU 固件只负责底层协议；`[lyx9231 ...]`、电流／细分／模式命令以及网页监测由主机模块提供。构建副本保留输入源码中的 `klippy/`，其中既有 LYX 文件可能仍是原版；不会把本仓库 `vendor/lyx/` 三份修补版安装到运行中的主机或构建副本。准备驱动依赖时可审阅下面的历史低层 `--with-lyx` 工具单独安装匹配模块；已经装好本仓库匹配版本的机器无需重复更新。

按照[历史低层安装手册第 1–3 节](LEGACY_INSTALLATION.md)设置本机路径、目标主机和 Fluidd 地址；需要首次安装或更新 LYX 三模块时，在预览之前选择 `LYX_ARGS=(--with-lyx)`。继续使用同一组参数：

```bash
# 先按安装手册设置 INSTALL_ARGS、INSTALL_RUN；这里不另造目标身份。
LYX_ARGS=(--with-lyx)
python3 scripts/install.py plan "${INSTALL_ARGS[@]}" "${LYX_ARGS[@]}"
"${INSTALL_RUN[@]}" python3 scripts/install.py install "${INSTALL_ARGS[@]}" "${LYX_ARGS[@]}"
```

安装器会拒绝覆盖未知版本的 LYX 主机模块，保留已有 TMC 模块；具体备份、重载和回退流程见安装手册。电机引脚和参数按[配置说明](CONFIGURATION.md)写入 CFG，不把 DRIVER7 或其他人的引脚当成所有 C8P 的默认配置。

之后的刷写、连接切换和验证由使用者另行安排：USB 模式使用该 MCU 实际枚举的串口路径；桥接模式要求主机 CAN 网络及总线使用 1M，并使用实际 MCU 的 CAN UUID。脚本不会改 `[mcu]` 或建立 `can0`。具体刷写入口以本机引导程序和 [FLY C8P 固件文档](https://mellow.klipper.cn/docs/ProductDoc/MainBoard/fly-c/fly-c8p/firmware/compile)为准。

已有匹配固件并稳定通信的机器，仅安装监测插件不需要再刷。构建通过、刷写成功、Klipper ready、UART 读取成功与真实报警停机是分别需要证据的阶段。
