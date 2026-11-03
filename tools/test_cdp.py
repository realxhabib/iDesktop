"""Check DevTools target selection without an open browser."""
import unittest

from cdp import find_viewer


class ViewerTargetTests(unittest.TestCase):
    def test_loopback_hosts_and_alternate_ports(self):
        for url in ('http://localhost:8080/', 'http://127.0.0.1:8080/', 'http://localhost:8087/'):
            with self.subTest(url=url):
                page = {'type': 'page', 'title': 'iDesktop', 'url': url}
                self.assertIs(find_viewer([page]), page)

    def test_other_tabs_and_workers_are_ignored(self):
        page = {'type': 'page', 'title': 'iDesktop', 'url': 'http://localhost:8081/'}
        others = [dict(page, title='Other app'), dict(page, type='service_worker'),
                  dict(page, url='http://localhost.example.com:8081/'),
                  dict(page, url='http://localhost:8081/other')]
        self.assertIs(find_viewer(others + [page]), page)

    def test_missing_viewer_has_an_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError, 'IDESKTOP_DEVTOOLS'):
            find_viewer([])


if __name__ == '__main__':
    unittest.main()
