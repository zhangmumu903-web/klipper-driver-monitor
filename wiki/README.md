# Wiki 准备与维护

本目录保存 GitHub Wiki 的源稿。2026-10-08 核对时，仓库尚未启用 Wiki（`has_wiki=false`）；把这些文件上传到软件功能分支，不代表 GitHub Wiki 已发布。

当前交付分支为 `feat/initial-distribution`。本轮准备首页、侧栏和章节映射，参数、安装命令、实现细节仍以仓库 `docs/` 和 `config/` 为唯一事实来源，不另复制一套维护。

## 已准备内容

| 文件 | 用途 | 状态 |
| --- | --- | --- |
| [Home.md](Home.md) | Wiki 首页和阅读路线 | 源稿已准备，链接指向功能分支文档 |
| [_Sidebar.md](_Sidebar.md) | Wiki 公共侧栏 | 源稿已准备；采用 GitHub 识别的文件名 |
| 本文件 | 章节规划、发布步骤和同步规则 | 仓库维护说明，不作为 Wiki 页面发布 |

## 章节规划

先将 Wiki 做成文档入口。后续如需独立 Wiki 章节，按下表映射源文件，确定同步方式后再拆页。

| 阅读顺序 | 章节 | 权威来源 |
| --- | --- | --- |
| 1 | 大型机器四 Z 电机示例：配置、读取与验收 | [示范案例](../docs/examples/LARGE_MACHINE_Z.md)、[CFG 片段](../config/large-machine-z.cfg.example) |
| 2 | 安装、升级与回退 | [INSTALLATION.md](../docs/INSTALLATION.md) |
| 3 | 主机驱动与 MCU 前提 | [DEPENDENCIES.md](../docs/DEPENDENCIES.md) |
| 4 | CFG 参数与命令 | [CONFIGURATION.md](../docs/CONFIGURATION.md) |
| 5 | 后台读取、日志与报警停机 | [ARCHITECTURE.md](../docs/ARCHITECTURE.md) |
| 6 | Fluidd 卡片与曲线 | [DISPLAY.md](../docs/DISPLAY.md) |
| 7 | 故障排查 | [TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md) |
| 8 | 验证记录与未覆盖范围 | [VALIDATION.md](../docs/VALIDATION.md) |
| 可选 | C8P 固件构建 | [C8P_FIRMWARE.md](../docs/C8P_FIRMWARE.md)，不作为通用安装的必经步骤 |

## 后续发布步骤

以下是发布准备清单，本轮未执行仓库设置变更或 Wiki 推送。

1. 在仓库设置中启用 Wikis，并在 GitHub Wiki 中创建、保存首个页面。
2. 按 GitHub 提供的 Wiki 克隆地址取得独立仓库：`https://github.com/zhangmumu903-web/klipper-driver-monitor.wiki.git`。Wiki 使用独立 Git 历史，软件分支的提交不会自动出现在 Wiki；推送其默认分支才会对读者生效。[GitHub 页面维护说明](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages)
3. 选定已核对的软件稳定提交或版本，将首页和侧栏的功能分支链接统一改为该版本链接，记录对应的软件提交。当前功能分支链接会随开发继续变化，不作为固定版本快照。
4. 将 `Home.md` 和 `_Sidebar.md` 同步到 Wiki 仓库根目录，检查差异后提交、推送。GitHub 会识别 `_Sidebar.md` 为公共侧栏。[GitHub 侧栏说明](https://docs.github.com/en/communities/documenting-your-project-with-wikis/creating-a-footer-or-sidebar-for-your-wiki)
5. 回读实际 Wiki 页面，检查首页、侧栏、示例和安装链接，再记录发布提交及时间。完成后才把状态更新为“Wiki 已发布”。

## 避免两套文档漂移

参数和行为先在软件仓库修改并验证；Wiki 只同步导航和版本说明。后续如复制正文到独立 Wiki 页面，也必须记录源文件和源提交，每次随选定版本统一同步，不在两处各自修改参数。

主案例采用大型机器的 `stepper_z / stepper_z1 / stepper_z2 / stepper_z3` 四个 Z 电机；显示范围仍是全部已配置并被后台发现的 LYX，包括 X、Y、Z 附加电机和挤出机，不只显示 Z。TMC 继续不进入本组件的查询与卡片。

案例中的占位引脚和示例数值不能变成默认硬件推荐；离线验证、历史实机结果和新设备验收继续分别标注。首页与侧栏目前只链接软件文档，不链接尚未发布的 Wiki 子页面。
