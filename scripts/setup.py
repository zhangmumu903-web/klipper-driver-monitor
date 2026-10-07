#!/usr/bin/env python3
"""Interactive host installer. No network, CFG changes, service or MCU actions."""
import argparse
import importlib.util
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('driver_monitor_installer', ROOT / 'scripts/install.py')
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)


def valid_directory(kind, value):
    path = Path(value).expanduser().resolve(strict=True)
    if kind == 'klipper':
        valid = (path / 'klippy/extras').is_dir()
    elif kind == 'fluidd':
        valid = (path / 'index.html').is_file()
    else:
        raise ValueError('Unknown path kind: ' + kind)
    if not valid:
        marker = 'klippy/extras' if kind == 'klipper' else 'index.html'
        raise ValueError('目录中没有 ' + marker)
    return path


def discover_paths(kind, homes=None):
    """Inspect a bounded set of common paths, never crawl configs or services."""
    if kind not in ('klipper', 'fluidd'):
        raise ValueError('Unknown path kind: ' + kind)
    if homes is None:
        # Do not enumerate /home: it may be an automount or a remote share.
        # Installations owned by another user remain selectable by explicit path.
        homes = [Path.home()]
    candidates = [Path(home) / kind for home in homes]
    candidates.extend([Path('/data') / kind, Path('/opt') / kind,
                       Path('/srv') / kind])
    if kind == 'fluidd':
        candidates.extend([Path('/var/www/fluidd'), Path('/data/www/fluidd')])
    found = set()
    for candidate in candidates:
        try:
            found.add(valid_directory(kind, candidate))
        except (OSError, RuntimeError, ValueError):
            continue
    return sorted(found, key=str)


def choose_path(kind, candidates, input_fn=input, output=print):
    label = 'Klipper 源码目录' if kind == 'klipper' else 'Fluidd 网页根目录'
    output('\n' + label + '（请核对实际使用的实例）：')
    for number, path in enumerate(candidates, 1):
        output('  %d. %s' % (number, path))
    if not candidates:
        output('  未在常见路径找到；请输入自己的路径。')
    default = '1' if len(candidates) == 1 else ''
    prompt = '输入序号或目录路径' + (' [1]' if default else '') + '：'
    while True:
        value = input_fn(prompt).strip() or default
        if value.isdigit() and 1 <= int(value) <= len(candidates):
            value = str(candidates[int(value) - 1])
        if not value:
            output('需要明确选择目录；多个候选不会自动选择。')
            continue
        try:
            return str(valid_directory(kind, value))
        except (OSError, RuntimeError, ValueError) as exc:
            output('路径不可用：%s' % exc)


def choose_value(prompt, choices, default, input_fn, output):
    while True:
        value = input_fn(prompt).strip() or default
        if value in choices:
            return choices[value]
        output('请输入列出的选项序号。')


def collect_options(input_fn=None, output=None):
    input_fn = input if input_fn is None else input_fn
    output = print if output is None else output
    output('Klipper Driver Monitor 通用主机安装')
    output('在运行 Klipper 的 Linux 上位机执行；按本机路径安装，不选择 MCU 或主板型号。')
    output('1. 后台 + Fluidd   2. 仅后台   3. 仅 Fluidd 显示')
    components = choose_value('安装组件 [1]：', {'1': 'all', '2': 'backend', '3': 'fluidd'},
                              '1', input_fn, output)
    args = argparse.Namespace(command='install', components=components, drivers='lyx',
                              with_lyx=False, klipper=None, fluidd=None, hostname=None,
                              origin=None, api_url=None, backup_dir=str(ROOT / '.install-backups'))
    if components != 'fluidd':
        output('1. LYX9231（可共用 TMC；安装配套 LYX 主机模块）  2. 纯 TMC（不检查或修改 LYX 文件）')
        output('此选择只决定安装依赖；不会修改现有驱动 CFG 或限制后台发现的驱动。')
        args.drivers = choose_value('驱动依赖 [1]：', {'1': 'lyx', '2': 'tmc'},
                                   '1', input_fn, output)
        args.with_lyx = args.drivers == 'lyx'
        args.klipper = choose_path('klipper', discover_paths('klipper'), input_fn, output)
        if args.with_lyx:
            output('LYX 仍需要已有兼容 MCU 固件；此安装不会生成或刷写固件。')
    if components != 'backend':
        args.fluidd = choose_path('fluidd', discover_paths('fluidd'), input_fn, output)
        output('显示目前接入 Fluidd 同源根站点；其他网页可使用“仅后台”，不在这里选择其网页目录。')
        output('hostname 必须与 Moonraker /printer/info 返回值一致。本机系统名称仅供参考：' + socket.gethostname())
        args.hostname = input_fn('输入目标 /printer/info 的 hostname：').strip()
        args.origin = input_fn('Fluidd 网页来源（如 http://printer.local；多个用空格分隔）：').split()
        args.api_url = input_fn('Fluidd 保存的 API URL（多个用空格分隔；留空使用以上网页来源）：').split() or None
    backup = input_fn('备份目录 [%s]：' % args.backup_dir).strip()
    if backup:
        args.backup_dir = str(Path(backup).expanduser())
    return args


def run_setup(plan_only=False, input_fn=None, output=None):
    input_fn = input if input_fn is None else input_fn
    output = print if output is None else output
    args = collect_options(input_fn, output)
    plan = installer.build_plan(args)
    summary = {'status': 'plan', 'components': args.components, 'drivers': args.drivers,
               'paths': plan['paths'], 'target': plan['config'],
               'backup_dir': str(Path(args.backup_dir).expanduser().resolve()),
               'files': [{'path': str(item['path']),
                          'action': 'create' if item['before'] is None else 'replace'}
                         for item in plan['changes']]}
    output('\n安装计划（只包含以下文件）：')
    output(json.dumps(summary, ensure_ascii=False, indent=2))
    if plan_only:
        output('仅预览完成，没有创建备份或写入目标文件。')
        return summary
    if not plan['changes']:
        output('所选文件已经匹配，无需写入。')
        return dict(summary, status='unchanged')
    answer = input_fn('确认按此计划备份并安装？输入 yes 执行，其余输入取消：').strip()
    if answer != 'yes':
        output('已取消，未写入目标文件。')
        return dict(summary, status='cancelled')
    result = installer.apply_plan(plan, args.backup_dir)
    output(json.dumps(result, ensure_ascii=False, indent=2))
    output('文件安装完成；未修改 CFG、重启服务或刷写 MCU。')
    if args.components != 'fluidd':
        if args.drivers == 'lyx':
            output('LYX 后台首次使用：按 docs/INSTALLATION.md 添加一次 [driver_monitor]；已有配置不重复添加。')
        else:
            output('纯 TMC 卡片使用 Klipper 原有缓存，无需新增 [driver_monitor]；已有 LYX 配置保持原样。')
        output('主机代码需按本机服务管理方式重新加载；FLYOS 先检查是否绑定自动刷写动作。')
    else:
        output('刷新 Fluidd 页面查看；LYX 数据仍要求对应主机已经安装并加载后台。')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='Only collect options and preview; never write')
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.exit(2, '需要交互终端。批量安装请使用 bash install.sh plan/install 的显式参数；详见 docs/INSTALLATION.md。\n')
    try:
        run_setup(args.plan)
    except (EOFError, KeyboardInterrupt):
        parser.exit(1, '\n操作中断。如已开始写入，请检查输出的备份收据并按手册回退。\n')
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, 'Error: %s\n请按明确路径重试；脚本不会自动提权或重启服务。\n' % exc)


if __name__ == '__main__':
    main()
