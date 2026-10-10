# 卡片布局与自定义显示

文件位于安装清单记录的 Fluidd 根目录下 `driver-monitor-user/`。普通 Linux 常见 `~/fluidd/driver-monitor-user/`，FLYOS 常见 `/data/fluidd/driver-monitor-user/`，以实际路径为准。修改后刷新页面，不重启 Klipper。

这些文件由同一台机器的各浏览器共同读取，不依赖 localStorage。升级及卸载默认保留；机器身份校验、现有网页访问权限不改变。

## layout.json

```json
{
  "schema_version": 1,
  "mode": "z-overview",
  "axis_order": ["stepper_z", "stepper_z1", "stepper_z2", "stepper_z3"],
  "axis_names": {"stepper_z": "Z 主电机", "stepper_z1": "Z1 电机"},
  "show": {"speed": true, "angle": true, "trends": true, "driver_details": true},
  "custom_renderer": {"enabled": false}
}
```

| 字段 | 含义 |
| --- | --- |
| `mode: cards` | 标准独立卡片 |
| `mode: compact` | 紧凑布局 |
| `mode: z-overview` | Z 系列优先分组，其余 LYX 轴继续展示 |
| `axis_order` | 优先顺序，未列出的实际驱动仍显示 |
| `axis_names` | 显示别名，不改 Klipper 对象名或指令目标 |
| `show` | 速度、角误差、曲线和详情开关 |
| `custom_renderer.enabled` | 显式启用管理员编写的附加渲染器 |

JSON不提供隐藏报警和保护信息的选项。非法配置会回退并提示；不会把配置错误当成驱动报警。驱动列表来自后台实际加载的 LYX 对象，不按布局配置伪造硬件。

## custom.css

样式加载到卡片的 Shadow DOM（独立样式区域）中，因此能实际作用于内部元素。

```css
.dm-card { border-radius: 14px; }
.dm-number { font-size: 2rem; }
.dm-header h3 { font-weight: 700; }
.dm-reading[data-tone="alarm"] { background: #6b2020; }
```

避免通过 CSS 隐藏报警、断线或保护状态。CSS 和 JavaScript 都是管理员可信内容，不是限制恶意自定义代码的隔离机制。

## 高级附加渲染器

编辑固定路径 `custom-renderer.js`，并将 `custom_renderer.enabled` 设为 `true`。签名和实际字段见[随包示例](../frontend/customization/custom-renderer.mjs.example)。

渲染器只能通过给定接口定制附加显示区域，框架不给它控制器或写寄存器接口。传入的数据为只读快照；`status` 包含 `connected`、`trusted`、`message`，断线或目标校验失败时 `readings` 为 `null`，附加区会清除旧读数并重绘；模块加载或执行失败会保留标准卡片并提示。JSON不接受任意远程代码地址。

管理员JavaScript仍以网页权限运行，只使用自己审阅过的代码。网页资源不得保存密码、API key或私有日志。

## 恢复默认

在工具目录执行，先预览改动再确认：

```bash
bash repair.sh --reset-layout
```

恢复默认 `layout.json` 和 `custom.css`，默认关闭高级渲染器；其代码文件保留，后台采集和保护不变。普通 `repair.sh` 保留自定义内容。
