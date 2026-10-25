# -*- coding: utf-8 -*-
import json
import os
import random
import tempfile
import time
import unittest

import detector
import events as ev
import game as gm


def mk(seed=1):
    d = tempfile.mkdtemp()
    g = gm.Game(os.path.join(d, "save.json"), rng=random.Random(seed))
    g.load()
    return g, d


class EventData(unittest.TestCase):
    def test_all_templates_format(self):
        g, _ = mk()
        for seed in range(30):
            g.rng = random.Random(seed)
            for realm in range(9):
                g.s["realm"] = realm
                b = g._bind()
                b.update(target="X", cost=1)
                for e in ev.EVENTS:
                    e["text"].format(**b)
                    for k, lab, ok, ko in e["opts"]:
                        lab.format(**b), ok.format(**b)
                        if ko:
                            ko.format(**b)
                    self.assertEqual(len(e["opts"]), 3, e["id"])


class Flow(unittest.TestCase):
    def test_play_many_rounds_reaches_ascend(self):
        g, _ = mk(7)
        g.save = lambda: None  # 跳过磁盘写入，加快测试
        for _ in range(3000):
            g.choose(g.rng.randrange(3))
            self.assertLessEqual(g.s["exp"], max(g.need, 1) * 3)
            self.assertGreaterEqual(g.s["exp"], 0)
            self.assertGreaterEqual(g.s["realm"], 0)
        self.assertGreaterEqual(g.s["rebirth"], 1)
        self.assertLessEqual(len(g.s["log"]), gm.LOG_KEEP)

    def test_pace(self):
        """爽感节奏：平均每个小阶段需要的回合数应该在合理范围。"""
        g, _ = mk(3)
        rounds = 0
        while g.s["realm"] == 0 and g.s["stage"] < 3 and rounds < 500:
            g.choose(1)
            rounds += 1
        self.assertLess(rounds, 80)

    def test_failure_never_lowers_realm(self):
        g, _ = mk(5)
        g.save = lambda: None
        for _ in range(500):
            before = (g.s["realm"] + 9 * g.s["rebirth"], g.s["stage"])
            g.choose(2)
            after = (g.s["realm"] + 9 * g.s["rebirth"], g.s["stage"])
            self.assertGreaterEqual(after, before)

    def test_pity(self):
        g, _ = mk(2)
        g.s["bad"] = 2
        for _ in range(50):
            g.s["bad"] = 2
            ok, pity = g._roll("gamble", 0.38)
            self.assertTrue(ok and pity)

    def test_breakthrough_scene(self):
        g, _ = mk(4)
        g.s["stage"] = 3
        g.s["exp"] = g.need
        g._next_scene()
        self.assertEqual(g.s["scene"]["kind"], "break")
        g.s["stones"] = 9999
        g.choose(0)
        self.assertEqual(g.s["realm"], 1)


class Save(unittest.TestCase):
    def test_roundtrip_and_backup(self):
        g, d = mk()
        g.choose(0)
        g.choose(0)
        snap = json.loads(json.dumps(g.s))
        g2 = gm.Game(g.path)
        self.assertTrue(g2.load())
        self.assertEqual(g2.s["turn"], snap["turn"])
        self.assertEqual(g2.s["scene"], snap["scene"])
        self.assertTrue(os.path.exists(g.bak))

    def test_corrupt_main_falls_back(self):
        g, d = mk()
        g.choose(0)
        g.choose(0)
        with open(g.path, "w") as f:
            f.write("{broken")
        g2 = gm.Game(g.path)
        self.assertTrue(g2.load())
        self.assertIn("备份", g2.load_note)

    def test_both_corrupt_new_game(self):
        g, d = mk()
        for p in (g.path, g.bak):
            with open(p, "w") as f:
                f.write("x")
        g2 = gm.Game(g.path)
        self.assertFalse(g2.load())
        self.assertEqual(g2.s["turn"], 0)

    def test_no_tmp_left(self):
        g, d = mk()
        g.choose(0)
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])


class Detect(unittest.TestCase):
    def test_dsh(self):
        d = tempfile.mkdtemp()

        def w(name, seq):
            with open(os.path.join(d, name), "w") as f:
                json.dump({"record": {"rows": {"turnBoundary": {"val": {"openTurnStartSeq": seq}}}}}, f)
        w("session-a.json", None)
        self.assertFalse(detector.dsh_busy(base=d))
        w("session-b.json", 12)
        self.assertTrue(detector.dsh_busy(base=d))
        old = time.time() - detector.DSH_FRESH - 1
        os.utime(os.path.join(d, "session-b.json"), (old, old))
        # 已观察到的同一 open 回合允许长思考期间停写，不被拆成多回合。
        self.assertTrue(detector.dsh_busy(base=d))
        with open(os.path.join(d, "session-b.json"), "w") as f:
            json.dump({"record": {"rows": {"turnBoundary": {"val": {"openTurnStartSeq": None}}}}}, f)
        self.assertFalse(detector.dsh_busy(base=d))
        with open(os.path.join(d, "session-c.json"), "w") as f:
            f.write("{half")
        self.assertFalse(detector.dsh_busy(base=d))

    def test_wb(self):
        d = tempfile.mkdtemp()
        pd = os.path.join(d, "proj")
        os.makedirs(pd)
        p = os.path.join(pd, "s.jsonl")

        def w(*recs):
            with open(p, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        user = {"type": "message", "role": "user"}
        w(user, {"type": "function_call"}, {"type": "file-history-snapshot"})
        self.assertTrue(detector.wb_busy(base=d))
        w(user, {"type": "message", "role": "assistant"}, {"type": "session-meta"})
        self.assertFalse(detector.wb_busy(base=d))
        w(user)
        old = time.time() - 300
        os.utime(p, (old, old))
        self.assertFalse(detector.wb_busy(base=d))

    def test_monitor_debounce(self):
        state = {"b": False}
        m = detector.Monitor(probe=lambda e: ["X"] if state["b"] else [], start_after=3, end_after=4)
        state["b"] = True
        self.assertIsNone(m.poll(now=0))
        self.assertIsNone(m.poll(now=2))
        self.assertEqual(m.poll(now=3.1), "start")
        state["b"] = False
        self.assertIsNone(m.poll(now=5))
        state["b"] = True          # 工具调用间隙，短暂空闲
        self.assertIsNone(m.poll(now=6))
        state["b"] = False
        self.assertIsNone(m.poll(now=7))
        self.assertEqual(m.poll(now=11.5), "end")
        # 短回复不触发
        state["b"] = True
        m.poll(now=20)
        state["b"] = False
        self.assertIsNone(m.poll(now=21))
        self.assertFalse(m.active)


if __name__ == "__main__":
    unittest.main()

