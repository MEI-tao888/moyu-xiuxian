# -*- coding: utf-8 -*-
"""境界筛选、连续历练和旧存档兼容。"""
import json
import os
import random
import tempfile
import unittest

import events as ev
from game import Game


class Story(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "save.json")

    def test_realm_pool_and_unique_events(self):
        self.assertGreaterEqual(len(ev.EVENTS), 200)
        self.assertEqual(len({e['id'] for e in ev.EVENTS}), len(ev.EVENTS))
        g = Game(self.path, random.Random(4))
        g.load()
        g.save = lambda: None
        lookup = {e['id']: e for e in ev.EVENTS}
        for realm in range(9):
            g.s.update(realm=realm, stage=0, exp=0, journey=None)
            for _ in range(30):
                g.s.update(stage=0, exp=0)
                g.choose(0)
                if g.s['scene']['kind'] == 'event':
                    e = lookup[g.s['scene']['id']]
                    self.assertLessEqual(e['min_realm'], realm)
                    self.assertGreaterEqual(e['max_realm'], realm)

    def test_short_story_survives_reload_and_ends(self):
        g = Game(self.path, random.Random(7))
        g.load()
        self.assertIsNotNone(g.s.get('journey'))
        journey = dict(g.s['journey'])
        g.choose(0)
        g = Game(self.path, random.Random(100))
        g.load()
        self.assertEqual(g.s['journey']['id'], journey['id'])
        self.assertEqual(g.s['journey']['bind'], journey['bind'])
        self.assertIn('result', g.s['last_turn'])
        for _ in range(2):
            g.choose(0)
        self.assertTrue(g.s['last_turn']['finished'])
        self.assertIn('choice', g.s['last_turn'])

    def test_old_save_keeps_pending_scene_and_progress(self):
        g = Game(self.path, random.Random(1))
        g.load()
        for k in ('journey', 'last_turn'):
            g.s.pop(k, None)
        original = json.loads(json.dumps(g.s))
        g.save()
        h = Game(self.path)
        self.assertTrue(h.load())
        self.assertEqual(h.s['scene'], original['scene'])
        self.assertEqual(h.s['exp'], original['exp'])
        h.choose(0)
        self.assertEqual(h.s['turn'], 1)


if __name__ == '__main__':
    unittest.main()
