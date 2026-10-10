#!/usr/bin/env python3
"""Download a stable public release, verify its checksum, then run its manager."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request

REPOSITORY = 'zhangmumu903-web/klipper-driver-monitor'
ARCHIVE = 'klipper-driver-monitor.tar.gz'
MAX_ARCHIVE = 16 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024


def download(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'klipper-driver-monitor-installer'})
    with urllib.request.urlopen(request, timeout=40) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('下载文件超过大小限制')
    return data


def select_release_url(url):
    prefix = 'https://github.com/%s/releases/tag/' % REPOSITORY
    if not url.startswith(prefix):
        raise ValueError('稳定版跳转目标不是本仓库的发行标签')
    tag = url[len(prefix):]
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('没有符合要求的正式稳定版本')
    base = 'https://github.com/%s/releases/download/%s/' % (REPOSITORY, tag)
    return tag, base + ARCHIVE, base + 'SHA256SUMS'


def resolve_release():
    # GitHub's official latest redirect excludes draft/prerelease releases and
    # does not consume the anonymous REST API quota. Pin all assets to its tag.
    request = urllib.request.Request(
        'https://github.com/%s/releases/latest' % REPOSITORY,
        headers={'User-Agent': 'klipper-driver-monitor-installer'})
    with urllib.request.urlopen(request, timeout=40) as response:
        return select_release_url(response.geturl())


def verify_archive(data, checksum):
    matches = []
    for line in checksum.decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?' + re.escape(ARCHIVE), line)
        if match:
            matches.append(match.group(1).lower())
    digest = hashlib.sha256(data).hexdigest()
    if matches != [digest]:
        raise ValueError('安装包 SHA256 校验失败，未执行安装')
    return digest


def extract_package(data, destination):
    """Extract regular files only, checking the complete archive before writing."""
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        entries, names, total = [], set(), 0
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if (name.is_absolute() or '..' in name.parts or '\\' in member.name
                    or not name.parts or name.parts[0] != 'klipper-driver-monitor'
                    or member.name in names or not (member.isfile() or member.isdir())):
                raise ValueError('安装包含不允许的路径或文件类型: ' + member.name)
            names.add(member.name)
            total += member.size
            if total > MAX_EXPANDED or len(names) > 2000:
                raise ValueError('安装包展开内容超过限制')
            entries.append(member)
        for member in entries:
            path = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, path.open('xb') as target:
                    shutil.copyfileobj(source, target)
                path.chmod(0o755 if member.mode & 0o111 else 0o644)
    root = destination / 'klipper-driver-monitor'
    if not (root / 'scripts/manage.py').is_file() or not (root / 'VERSION').is_file():
        raise ValueError('安装包缺少管理器或版本信息')
    return root


def fetch_release(cache=None):
    tag, archive_url, sums_url = resolve_release()
    data = download(archive_url, MAX_ARCHIVE)
    digest = verify_archive(data, download(sums_url, 16384))
    cache = Path(cache or Path.home() / '.local/share/klipper-driver-monitor/packages').expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    if cache.is_symlink():
        raise ValueError('安装包缓存目录不能是符号链接')
    # Unique directories avoid overwriting an edited or concurrently running copy.
    destination = Path(tempfile.mkdtemp(prefix=tag + '-', dir=str(cache)))
    try:
        root = extract_package(data, destination)
        if (root / 'VERSION').read_text().strip() != tag[1:]:
            raise ValueError('安装包版本与发布标签不一致')
        (destination / 'download.json').write_text(json.dumps(
            {'tag': tag, 'sha256': digest, 'source': archive_url}, indent=2))
        return root, tag
    except Exception:
        shutil.rmtree(destination)
        raise


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv.pop(0) if argv and argv[0] in ('install', 'update') else 'install'
    try:
        root, tag = fetch_release()
    except Exception as error:
        print('无法取得稳定安装包：%s' % error, file=sys.stderr)
        print('现有安装未改变；可使用已下载版本的 install.sh 离线安装。', file=sys.stderr)
        return 2
    print('已校验 %s；安装工具保存在 %s' % (tag, root), flush=True)
    os.execv(sys.executable, [sys.executable, str(root / 'scripts/manage.py'), command] + argv)


if __name__ == '__main__':
    sys.exit(main())
