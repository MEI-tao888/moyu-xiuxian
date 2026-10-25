# -*- coding: utf-8 -*-
"""真实日志结构的回归样例，不包含用户消息内容。"""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import detector


class DetectLogs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = time.time()

    def write(self, path, *records, append=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a' if append else 'w', encoding='utf-8') as f:
            for rec in records:
                f.write(json.dumps(rec) + '\n')

    def event(self, kind, turn='a'):
        return {'type': 'event_msg', 'payload': {'type': kind, 'turn_id': turn}}

    def test_codex_explicit_boundaries_and_partial_line(self):
        p = os.path.join(self.tmp.name, '2026', '10', '24', 'rollout-a.jsonl')
        self.write(p, self.event('task_started'))
        self.assertTrue(detector.codex_busy(base=self.tmp.name, now=self.now))
        self.write(p, {'type': 'response_item', 'payload': {'type': 'message',
                   'role': 'assistant', 'phase': 'commentary'}}, append=True)
        self.assertTrue(detector.codex_busy(base=self.tmp.name, now=self.now))
        with open(p, 'a') as f:
            f.write('{"type":"event_msg","payload":\n')
        self.assertTrue(detector.codex_busy(base=self.tmp.name, now=self.now))
        with open(p, 'w', encoding='utf-8') as f:
            f.write(json.dumps(self.event('task_started')) + '\n')
            f.write(json.dumps(self.event('task_complete')) + '\n')
        self.assertFalse(detector.codex_busy(base=self.tmp.name, now=self.now))

    def test_codex_interleaved_turns_abort_and_truncate(self):
        p = os.path.join(self.tmp.name, '2025', '01', '02', 'rollout-a.jsonl')
        self.write(p, self.event('task_started'), self.event('task_started', 'b'),
                   self.event('task_complete'))
        self.assertTrue(detector.codex_busy(base=self.tmp.name, now=self.now))
        self.write(p, self.event('turn_aborted', 'b'), append=True)
        self.assertFalse(detector.codex_busy(base=self.tmp.name, now=self.now))
        self.write(p, self.event('task_started', 'c'))
        self.assertTrue(detector.codex_busy(base=self.tmp.name, now=self.now))
        old = self.now - detector.CODEX_STALE - 1
        os.utime(p, (old, old))
        self.assertFalse(detector.codex_busy(base=self.tmp.name, now=self.now))

    def test_wb_long_tool_and_non_turn_records(self):
        p = os.path.join(self.tmp.name, 'project', 's.jsonl')
        self.write(p, {'type': 'message', 'role': 'user'},
                   {'type': 'function_call', 'call_id': 'x'})
        old = self.now - 90
        os.utime(p, (old, old))
        # 未在本进程新鲜窗口内观察到的旧记录，遵循 8 秒规则视为空闲。
        self.assertFalse(detector.wb_busy(base=self.tmp.name, now=self.now))
        self.write(p, {'type': 'function_call_result', 'call_id': 'x'},
                   {'type': 'message', 'role': 'assistant'},
                   {'type': 'credit-usage'}, {'type': 'ai-title'}, append=True)
        self.assertFalse(detector.wb_busy(base=self.tmp.name, now=self.now))

    def test_wb_observed_turn_grace_and_assistant_clear(self):
        p = os.path.join(self.tmp.name, 'project', 'grace.jsonl')
        self.write(p, {'type': 'message', 'role': 'user'},
                   {'type': 'function_call', 'call_id': 'long'})
        self.assertTrue(detector.wb_busy(base=self.tmp.name, now=self.now))
        old = self.now - detector.WB_FRESH - 1
        os.utime(p, (old, old))
        self.assertTrue(detector.wb_busy(base=self.tmp.name, now=self.now + 120))
        self.write(p, {'type': 'message', 'role': 'assistant'}, append=True)
        self.assertFalse(detector.wb_busy(base=self.tmp.name, now=self.now + 121))

    def test_wb_old_unobserved_file_uses_short_window(self):
        p = os.path.join(self.tmp.name, 'project', 'old.jsonl')
        self.write(p, {'type': 'function_call', 'call_id': 'old'})
        old = self.now - detector.WB_GRACE - 1
        os.utime(p, (old, old))
        self.assertFalse(detector.wb_busy(base=self.tmp.name, now=self.now))
        old = self.now - detector.WB_GRACE - 1
        self.write(p, {'type': 'function_call', 'call_id': 'x'})
        os.utime(p, (old, old))
        self.assertFalse(detector.wb_busy(base=self.tmp.name, now=self.now))

    def test_enabled_tools(self):
        with patch.dict(detector.ADAPTERS, {'A': lambda: True, 'B': lambda: True}, clear=True):
            self.assertEqual(detector.busy_sources({'B'}), ['B'])
            self.assertEqual(detector.busy_sources(set()), [])

    def test_dsh_long_turn_keeps_busy_after_file_stales(self):
        """同一 open 回合长时间不写盘时，不应被拆成两次思考。"""
        p = os.path.join(self.tmp.name, 'session-long.json')
        d = {'record': {'rows': {'turnBoundary': {'val': {'openTurnStartSeq': 7}}}}}
        self.write(p, d)
        self.assertTrue(detector.dsh_busy(base=self.tmp.name, now=self.now))
        old = self.now - detector.DSH_FRESH - 1
        os.utime(p, (old, old))
        self.assertTrue(detector.dsh_busy(base=self.tmp.name, now=self.now + 120))
        d['record']['rows']['turnBoundary']['val']['openTurnStartSeq'] = None
        self.write(p, d)
        self.assertFalse(detector.dsh_busy(base=self.tmp.name, now=self.now + 121))

    def test_monitor_aggregates_overlapping_tools(self):
        """DSH 与 WorkBuddy 交错进行时只产生一组开始/结束事件。"""
        state = {'DSH': False, 'WorkBuddy': False}

        def probe(enabled):
            return [name for name, busy in state.items() if busy]

        mon = detector.Monitor(probe=probe, start_after=3, end_after=4)
        state['DSH'] = True
        self.assertIsNone(mon.poll(now=0))
        self.assertEqual(mon.poll(now=3.1), 'start')

        # 第二个工具随后开始，首个工具结束；整体仍处于忙碌状态。
        state['WorkBuddy'] = True
        state['DSH'] = False
        self.assertIsNone(mon.poll(now=5))
        self.assertIsNone(mon.poll(now=30))

        # 两个工具都结束后，只有持续空闲满 end_after 才结束。
        state['WorkBuddy'] = False
        self.assertIsNone(mon.poll(now=31))
        self.assertEqual(mon.poll(now=35.1), 'end')
        self.assertFalse(mon.active)

    def test_monitor_long_thought_does_not_repeat_start(self):
        """单次很长的思考期间持续轮询，不应重复触发 start。"""
        busy = {'value': True}
        mon = detector.Monitor(
            probe=lambda enabled: ['DSH'] if busy['value'] else [],
            start_after=3,
            end_after=4,
        )
        self.assertIsNone(mon.poll(now=0))
        self.assertEqual(mon.poll(now=3), 'start')
        for t in (10, 60, 300, 900):
            self.assertIsNone(mon.poll(now=t))
        busy['value'] = False
        self.assertIsNone(mon.poll(now=901))
        self.assertEqual(mon.poll(now=905), 'end')


if __name__ == '__main__':
    unittest.main()
