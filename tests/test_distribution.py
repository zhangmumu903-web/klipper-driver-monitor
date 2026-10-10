import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('distribution', ROOT / 'scripts/distribution.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as tar:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            if kind == 'link':
                info.type = tarfile.SYMTYPE
                info.linkname = '/tmp/outside'
            tar.addfile(info, io.BytesIO(data))
    return stream.getvalue()


class DistributionTest(unittest.TestCase):
    def metadata(self):
        base = 'https://github.com/' + d.REPOSITORY + '/releases/download/v1.0.0/'
        return {'tag_name': 'v1.0.0', 'draft': False, 'prerelease': False,
                'assets': [{'name': name, 'browser_download_url': base + name}
                           for name in [d.ARCHIVE, 'SHA256SUMS']]}

    def test_only_stable_complete_expected_origin_release_is_selected(self):
        self.assertEqual('v1.0.0', d.select_release(self.metadata())[0])
        for key in ('draft', 'prerelease'):
            item = self.metadata(); item[key] = True
            with self.assertRaises(ValueError):
                d.select_release(item)
        item = self.metadata(); item['assets'][0]['browser_download_url'] = 'https://example.org/code'
        with self.assertRaises(ValueError):
            d.select_release(item)
        item = self.metadata(); item['assets'].pop()
        with self.assertRaises(ValueError):
            d.select_release(item)

    def test_checksum_requires_exactly_one_matching_archive(self):
        data = b'package'
        line = hashlib.sha256(data).hexdigest() + '  ' + d.ARCHIVE + '\n'
        self.assertEqual(hashlib.sha256(data).hexdigest(), d.verify_archive(data, line.encode()))
        for sums in [line + line, line.replace(d.ARCHIVE, 'wrong.tar.gz'), '0' * 64 + '  ' + d.ARCHIVE]:
            with self.assertRaises(ValueError):
                d.verify_archive(data, sums.encode())

    def test_valid_package_extracts_without_trusting_modes_or_ownership(self):
        data = archive([('klipper-driver-monitor/VERSION', b'1.0.0\n', ''),
                        ('klipper-driver-monitor/scripts/manage.py', b'pass\n', '')])
        with tempfile.TemporaryDirectory() as directory:
            root = d.extract_package(data, Path(directory))
            self.assertEqual('1.0.0', (root / 'VERSION').read_text().strip())

    def test_traversal_links_duplicate_and_wrong_root_rejected_before_writes(self):
        for bad, kind in [('klipper-driver-monitor/../../escape', ''), ('/absolute', ''),
                          ('another-root/file', ''), ('klipper-driver-monitor/link', 'link')]:
            data = archive([('klipper-driver-monitor/VERSION', b'1.0.0', ''), (bad, b'', kind)])
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                with self.assertRaises(ValueError):
                    d.extract_package(data, target)
                self.assertEqual([], list(target.iterdir()))
        data = archive([('klipper-driver-monitor/VERSION', b'x', '')] * 2)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            d.extract_package(data, Path(directory))

    def test_failed_download_never_executes_manager(self):
        with patch.object(d, 'fetch_release', side_effect=OSError('offline')), \
                patch.object(d.os, 'execv') as execute:
            self.assertEqual(2, d.main(['update', '--yes']))
            execute.assert_not_called()

    def test_downloaded_bootstrap_matches_reviewed_source(self):
        source = (ROOT / 'install.sh').read_text()
        embedded = source.split("KDM_BOOTSTRAP_PY'\n", 1)[1].split('\nKDM_BOOTSTRAP_PY\n', 1)[0]
        self.assertEqual((ROOT / 'scripts/distribution.py').read_text(), embedded)


if __name__ == '__main__':
    unittest.main()
