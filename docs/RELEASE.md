# 稳定版本制作

维护者先在功能分支完成回归和文档审查，再合入 `main`。用户安装只下载正式 Release，不跟随开发分支。

1. 更新 `VERSION`、`CHANGELOG.md` 和验证范围。
2. 修改发行下载器后运行 `python3 scripts/package-release.py --write-bootstrap`，保持根 `install.sh` 与源码一致。
3. 执行 README 中四组测试、Shell 语法检查、文档链接检查；用离线浏览器核对三布局和故障显示。
4. 检查 Git 跟踪清单，确保没有设备配置、凭据、日志、数据库或本地备份，再提交。
5. 在确定的发行源码上执行 `python3 scripts/package-release.py`。默认输出 `dist/release/`，只打包已跟踪普通文件，保留必要可执行权限。重复构建应产生相同 SHA256。
6. 在相同源码的 `main` 提交创建 `v<版本>` 标签及正式 Release，上传 `install.sh`、`klipper-driver-monitor.tar.gz`、`SHA256SUMS` 三个附件。
7. 匿名下载三个附件，核对完整包 SHA256、VERSION 和文件内容，再运行临时目标目录的安装/卸载冒烟测试；不得用生产打印机替代发行包测试。

发行包包含可重现源码与测试，不含 `.git`、忽略文件及固件二进制。Python 安装工具和静态网页直接运行，目标主机无需 Node.js；Node 仅用于开发测试。

SHA256SUMS 用于一致性验证，不是独立可信签名。稳定标签不应重复移动；修复已发布版本应发布新补丁版本。
