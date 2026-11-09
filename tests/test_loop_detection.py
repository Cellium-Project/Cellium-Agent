# -*- coding: utf-8 -*-
"""模型输出循环检测：单次回复内刷屏重复"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.loop.loop_controller import LoopController, detect_stream_loop

SENTENCE = "正在检查配置文件中的模型参数是否正确并记录日志到控制台"


class TestStreamLoopDetection(unittest.TestCase):

    def test_normal_output_not_flagged(self):
        text = "\n".join(f"第 {i} 步：读取文件并检查配置项 {i}" for i in range(80))
        self.assertIsNone(detect_stream_loop(text))

    def test_repeated_sentence_flagged(self):
        self.assertIsNotNone(detect_stream_loop(SENTENCE * 40))

    def test_repeated_long_lines_flagged(self):
        text = "\n".join([SENTENCE] * 80)
        self.assertIsNotNone(detect_stream_loop(text))

    def test_shared_ending_sentences_not_flagged(self):
        # 每条检查项结尾是同一句话，但中间有不同内容（非连续重复）
        text = "\n".join(
            f"检查项 {i}：请检查网络连接并重新执行上述命令后再验证结果。" for i in range(30)
        )
        self.assertIsNone(detect_stream_loop(text))

    def test_data_table_not_flagged(self):
        rows = "\n".join(f"| 参数{i} | string | 默认值{i} | 说明文字{i} | 必填 |" for i in range(40))
        self.assertIsNone(detect_stream_loop(rows))

    def test_short_repeat_not_flagged(self):
        self.assertIsNone(detect_stream_loop(SENTENCE * 5))

    def test_separator_lines_not_flagged(self):
        text = "\n".join(["------------------------------"] * 40)
        self.assertIsNone(detect_stream_loop(text))


class TestOutputLoopAcrossIterations(unittest.TestCase):

    def test_same_final_output_stops(self):
        ctrl = LoopController(loop_detection_threshold=3)
        self.assertFalse(ctrl.check_output_loop("重复的最终回答内容")[0])
        self.assertFalse(ctrl.check_output_loop("重复的最终回答内容")[0])
        self.assertTrue(ctrl.check_output_loop("重复的最终回答内容")[0])


if __name__ == "__main__":
    unittest.main()
