# -*- coding: utf-8 -*-
"""界面自动弹出抑制规则的纯逻辑测试。"""
import unittest

from xiuxian import auto_popup_allowed


class PopupPolicy(unittest.TestCase):
    def test_cooldown_blocks_only_before_expiry(self):
        self.assertFalse(auto_popup_allowed(100, now=99.9))
        self.assertTrue(auto_popup_allowed(100, now=100))
        self.assertTrue(auto_popup_allowed(0, now=99.9))

    def test_invalid_saved_value_does_not_block_popup(self):
        self.assertTrue(auto_popup_allowed("坏存档", now=100))


if __name__ == "__main__":
    unittest.main()
