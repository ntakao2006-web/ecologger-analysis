import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
import check_licenses  # noqa: E402


class LicenseRuleTest(unittest.TestCase):
    def test_installed_dependencies_follow_the_rule(self):
        rows, bad = check_licenses.check()
        self.assertTrue(rows)
        self.assertFalse(bad, '\n'.join(' '.join(r) for r in rows if r[3] != 'OK'))

    def test_gpl_family_is_rejected(self):
        for text in ('GPL-3.0-or-later', 'GNU General Public License v3 (GPLv3)', 'LGPL-2.1', 'AGPL-3.0'):
            self.assertTrue(check_licenses.NG.search(text), text)
        for text in ('MIT', 'BSD-3-Clause', 'Apache-2.0', 'PSF-2.0'):
            self.assertFalse(check_licenses.NG.search(text), text)
            self.assertTrue(check_licenses.OK.search(text), text)


if __name__ == '__main__':
    unittest.main()
