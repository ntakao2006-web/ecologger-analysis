import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecoanalysis import session  # noqa: E402
from ecoanalysis.sample import write_sample  # noqa: E402


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = write_sample(Path(self.tmp.name) / 'sample.ecorun')

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, change):
        p = self.dir / 'session.json'
        meta = json.loads(p.read_text(encoding='utf-8'))
        change(meta)
        p.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')

    def problems(self):
        with self.assertRaises(session.SessionError) as cm:
            session.load(self.dir)
        return '\n'.join(cm.exception.problems)

    def test_sample_loads(self):
        s = session.load(self.dir)
        self.assertEqual(s.title, '作りものの走行')
        self.assertEqual(list(s.channels), ['Speed', 'RPM', 'Throttle', 'LongAccel', 'LatAccel', 'WaterTemp', 'ClutchDisengaged'])
        self.assertEqual(len(s.t), len(s.channels['RPM']))
        self.assertAlmostEqual(s.t[-1], s.meta['duration_s'], places=3)
        self.assertEqual([x['lap'] for x in s.laps], [1, 2, 3])
        self.assertEqual(sorted(s.gnss), sorted(session.GNSS_COLUMNS))
        self.assertTrue(all(abs(v - 36.0) < 0.01 for v in s.gnss['lat']))

    def test_empty_cells_are_nan(self):
        import gzip
        p = self.dir / 'channels.csv.gz'
        lines = gzip.decompress(p.read_bytes()).decode().splitlines()
        cells = lines[5].split(',')
        cells[2] = ''
        lines[5] = ','.join(cells)
        p.write_bytes(gzip.compress(('\n'.join(lines) + '\n').encode()))
        s = session.load(self.dir)
        self.assertTrue(math.isnan(s.channels['RPM'][4]))

    def test_unknown_version_is_refused(self):
        self.edit(lambda m: m.update(version=99))
        self.assertIn('知らない版', self.problems())

    def test_files_outside_the_folder_are_refused(self):
        for bad in ('../channels.csv.gz', '/etc/passwd', 'C:/x.csv', '..\\x.csv'):
            self.assertIsNone(session.safe_file(self.dir, bad), bad)
        self.edit(lambda m: m.update(channels_file='../channels.csv.gz'))
        self.assertIn('channels_file', self.problems())

    def test_bad_laps_and_channels_are_listed_together(self):
        def change(m):
            m['laps'][0]['end'] = m['laps'][0]['start'] - 1
            m['laps'][1]['end'] = m['duration_s'] + 50
            m['channels'][0]['kind'] = 'curve'
        self.edit(change)
        text = self.problems()
        self.assertIn('1周目：始まり・終わり・タイムがおかしい', text)
        self.assertIn('2周目：チャンネルの時間の外にある', text)
        self.assertIn('Speed：kind', text)

    def test_header_must_match_channels(self):
        self.edit(lambda m: m['channels'].reverse())
        self.assertIn('見出し', self.problems())

    def test_video_sync(self):
        (self.dir / 'video').mkdir()
        (self.dir / 'video' / '01.mp4').write_bytes(b'')
        sync = [[0.0, 10.0], [100.0, 110.05]]
        self.edit(lambda m: m.update(videos=[{'file': 'video/01.mp4', 'sync': sync}]))
        s = session.load(self.dir)
        self.assertAlmostEqual(session.video_seconds(s.videos[0]['sync'], 60.025), 50.0, places=6)
        self.assertIsNone(session.video_seconds(sync, 5.0))
        self.assertAlmostEqual(session.session_time(sync, 50.0), 60.025, places=6)
        self.edit(lambda m: m.update(videos=[{'file': 'video/01.mp4', 'sync': [[0, 10], [5, 9]]}]))
        self.assertIn('増える一方', self.problems())
        self.edit(lambda m: m.update(videos=[{'file': 'video/02.mp4', 'sync': sync}]))
        self.assertIn('動画のファイルが無い', self.problems())


if __name__ == '__main__':
    unittest.main()
