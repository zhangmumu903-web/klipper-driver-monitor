# Klipper Driver Monitor

通用 LYX9231 监控：Klipper 后台采集、Fluidd 每轴独立卡片、曲线与可选的使能后报警停机。普通 Linux 和 FLYOS 共用安装入口，适用于已有兼容 LYX 支持的 Klipper 主机，不限定主板型号。

**安装只负责监控后台、网页卡片及监控加载入口。** 不刷 MCU，不修改电流、细分、模式、引脚和运动配置，不替换 LYX/TMC 驱动。依赖缺失时说明原因，参见[依赖说明](docs/DEPENDENCIES.md)。

## 一键安装

在打印机 Linux 上位机终端执行，需要 Bash、Python 3.8+、curl：

```bash
curl --fail --location --show-error \
  https://github.com/zhangmumu903-web/klipper-driver-monitor/releases/latest/download/install.sh \
  -o /tmp/klipper-driver-monitor-install.sh && \
bash /tmp/klipper-driver-monitor-install.sh
```

入口下载正式稳定版本并核对 SHA256，向导识别路径、展示计划、备份安装。多实例须明确选择，下载失败不改变现有安装。已有完整源码/离线包时，在工具目录执行：

```bash
bash install.sh --plan             # 仅预览
bash install.sh                    # 后台和 Fluidd
bash install.sh --frontend-only    # 仅网页卡片
bash install.sh --backend-only     # 仅后台监控
```

安装结果会输出工具目录、备份和待重启状态。首次加载或更新后台需在空闲时重启 Klipper 主机进程；仅换卡片样式不用重启。安装器不自动重启、使能、运动或加热，原 LYX 驱动重启时仍按已有 CFG 初始化。详见[安装手册](docs/INSTALLATION.md)。

## 更新、检查和卸载

在安装输出的工具目录运行；多实例各自使用独立 `--state-dir`：

```bash
bash doctor.sh                    # 检查安装与运行状态
bash update.sh                    # 下载稳定版并更新
bash update.sh --local            # 用当前完整离线包更新
bash repair.sh                    # Fluidd 更新后修复入口
bash uninstall.sh --frontend-only # 只卸卡片，后台保护继续
bash uninstall.sh                 # 完整卸载监控组件
bash rollback.sh                  # 回退最近一次受管变更
```

升级保留自定义内容。卸载只移除本工具管理的文件和入口，保留驱动、电机配置、用户样式和备份。**完整卸载会停止本组件的采集与报警保护**；遇到用户自有的监控配置会明确提示处理。修复或卸载不会用旧版整份 `index.html` 覆盖 Fluidd。

## 自定义卡片

标准卡片、紧凑列表、Z 总览三种布局。Z 总览优先展示多 Z，其他已配置 LYX 轴继续显示；本组件不查询或显示 TMC。

| Fluidd 根目录下的文件 | 用途 |
| --- | --- |
| `driver-monitor-user/layout.json` | 布局、轴顺序、名称、数值/曲线/详情开关 |
| `driver-monitor-user/custom.css` | 字体、颜色、尺寸与间距，在卡片内部生效 |
| `driver-monitor-user/custom-renderer.js` | 高级附加显示区域，默认关闭 |

编辑后刷新页面，同机所有浏览器读取相同配置；升级保留原文件。无效配置/渲染器错误会提示并回退，基本报警与保护继续显示。[自定义手册](docs/CUSTOMIZATION.md)

## 后台逻辑

`驱动 → Klipper 后台串行采集 → Moonraker 缓存接口 → Fluidd 显示`

- 逐轴读取报警、转速、角度误差，等待每项完整事务返回；原生 UART 可能内部重试。
- 型号、电流启动时读取，失败后续补读；多开浏览器不增加自动 UART 采集器，关网页也继续采集。
- 新配置默认 `shutdown_on_alarm:false`，已有设置保持。开启后，仅同一已生效使能周期中的新鲜有效非零报警触发整台 Klipper 停机并记录中文原因；通信失败不冒充报警。
- 开启保护不能暂停监测。轮询不承诺固定一秒内响应，不代替驱动硬件保护。
- 曲线显示寄存器原值，电流设定不等于仪表实测相电流；短时回读与真实故障、长期运行分别验证。

## 文档

[安装升级卸载](docs/INSTALLATION.md) · [自定义显示](docs/CUSTOMIZATION.md) · [参数配置](docs/CONFIGURATION.md) · [后台实现](docs/ARCHITECTURE.md) · [四 Z 示例](docs/examples/LARGE_MACHINE_Z.md) · [排错](docs/TROUBLESHOOTING.md) · [验证范围](docs/VALIDATION.md) · [变更记录](CHANGELOG.md)

[GitHub Wiki](https://github.com/zhangmumu903-web/klipper-driver-monitor/wiki) 保留早期已发布手册，当前稳定版以仓库文档为准。`main` 用于稳定版本，安装入口使用正式 Release，开发分支不会自动分发。C8P 固件构建工具独立保留，安装器不会调用。[旧安装文档](docs/LEGACY_INSTALLATION.md)供早期收据回退参考。

## 开发

```bash
python3 -B backend/test_driver_monitor.py
python3 -B -m unittest discover -s tests -v
node --test frontend/*.test.mjs
python3 -B -m unittest discover -s preview -p 'test_*.py' -v
python3 preview/server.py
```

预览只监听本机、使用模拟数据。监控运行不需要 Node.js，前端测试需 Node.js 18+。

## 来源与许可

GNU GPLv3，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE.md)。依赖源于 [zylo117/klipper](https://github.com/zylo117/klipper)，随仓驱动材料含本项目修补，不能标作作者未修改版本。公开包不包含机器配置、私有日志、凭据和预编译主板固件。本项目不是官方 Klipper/Fluidd 插件。
