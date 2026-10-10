"""Loopback-only smoke tests for the offline demo and its module configuration."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import server


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.set_scenario({'scenario': 'multiple', 'auto_enabled': False})
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.origin = 'http://127.0.0.1:%s' % cls.httpd.server_port
        cls.worker = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.worker.join()

    def read(self, path):
        with urlopen(self.origin + path, timeout=5) as response:
            return response.read().decode(), response.headers['Content-Type']

    def test_preview_serves_the_empty_configuration_and_explicit_opt_in(self):
        html, _ = self.read('/')
        self.assertIn('data-driver-monitor-preview="true"', html)
        self.assertIn('driver-monitor-demo', html)
        config, content_type = self.read('/frontend/driver-monitor-config.mjs')
        self.assertIn('javascript', content_type)
        self.assertIn("hostname: ''", config)
        self.assertIn('pageOrigins: Object.freeze([])', config)
        self.assertIn('apiEndpoints: Object.freeze([])', config)
        for name in ('driver-monitor.mjs', 'driver-monitor-core.mjs'):
            module, content_type = self.read('/frontend/' + name)
            self.assertIn('javascript', content_type)
            self.assertIn("from './driver-monitor-config.mjs'", module)

    def test_multiple_driver_snapshot_is_local_and_passive(self):
        info = json.loads(self.read('/printer/info')[0])['result']
        self.assertEqual(info['hostname'], 'driver-monitor-demo')
        names = json.loads(self.read('/printer/objects/list')[0])['result']['objects']
        self.assertIn('driver_monitor', names)
        self.assertIn('tmc2209 extruder', names)
        self.assertIn('tmc5160 stepper_z1', names)
        query = json.loads(self.read('/printer/objects/query?webhooks&driver_monitor')[0])['result']
        monitor = query['status']['driver_monitor']
        self.assertEqual(set(query['status']), {'webhooks', 'driver_monitor'})
        self.assertEqual([item['stepper'] for item in monitor['drivers']], ['stepper_x', 'stepper_y'])
        self.assertEqual(monitor['readings']['stepper_y']['ALARM_CODE']['value'], 2)
        self.assertFalse(monitor['auto_enabled'])
        evidence = json.loads(self.read('/__test')[0])
        self.assertEqual(evidence['posts'], [])
        self.assertEqual(evidence['get_counts'], {
            '/printer/info': 1, '/printer/objects/list': 1, '/printer/objects/query': 1})

    def test_user_layout_and_styles_are_same_origin_and_survive_scenario_changes(self):
        layout, _ = self.read('/driver-monitor-user/layout.json')
        self.assertEqual(json.loads(layout)['mode'], 'cards')
        style, content_type = self.read('/driver-monitor-user/custom.css')
        self.assertIn('text/css', content_type)
        self.assertIn('ShadowRoot', style)
        try:
            server.state['layout_mode'] = 'z-overview'
            server.set_scenario({'scenario': 'four_z', 'auto_enabled': False})
            layout, _ = self.read('/driver-monitor-user/layout.json')
            self.assertEqual(json.loads(layout)['mode'], 'z-overview')
            monitor = server.status()['driver_monitor']
            self.assertEqual([entry['stepper'] for entry in monitor['drivers']],
                             ['stepper_z', 'stepper_z1', 'stepper_z2', 'stepper_z3'])
            self.assertEqual(len(monitor['protection']), 4)
            self.assertTrue(monitor['shutdown_on_alarm'])
            self.assertIn('tmc2209 extruder', server.status())
        finally:
            server.state['layout_mode'] = 'cards'
            server.set_scenario({'scenario': 'multiple', 'auto_enabled': False})

    def test_tmc_only_fixture_retains_tmc_objects_and_has_no_lyx_drivers(self):
        try:
            server.set_scenario({'scenario': 'tmc_only', 'auto_enabled': False})
            names = json.loads(self.read('/printer/objects/list')[0])['result']['objects']
            self.assertIn('tmc2209 extruder', names)
            self.assertIn('tmc5160 stepper_z1', names)
            self.assertFalse(any(name.startswith('lyx9231 ') for name in names))
            query = json.loads(self.read('/printer/objects/query?webhooks&driver_monitor')[0])['result']
            self.assertEqual(set(query['status']), {'webhooks', 'driver_monitor'})
            self.assertEqual(query['status']['driver_monitor']['drivers'], [])
            self.assertEqual(query['status']['driver_monitor']['readings'], {})
        finally:
            server.set_scenario({'scenario': 'multiple', 'auto_enabled': False})


if __name__ == '__main__':
    unittest.main()
