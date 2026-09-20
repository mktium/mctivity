#!/usr/bin/env python3
import unittest

from anti_sway import (
    AntiSwayConfigError,
    build_zvd_impulses,
    plan_zvd_move,
    shaped_target_counts,
)


class AntiSwayTests(unittest.TestCase):
    def test_zvd_impulses_are_normalized_and_time_ordered(self):
        impulses = build_zvd_impulses(1150, 0.05)
        self.assertEqual([item[0] for item in impulses], [0, 575, 1150])
        self.assertAlmostEqual(sum(item[1] for item in impulses), 1.0, places=12)
        self.assertGreater(impulses[0][1], 0.0)
        self.assertGreater(impulses[1][1], impulses[0][1])
        self.assertGreater(impulses[2][1], impulses[0][1])

    def test_planned_move_preserves_signed_integer_distance(self):
        positive = plan_zvd_move(10000, 1150)
        negative = plan_zvd_move(-10000, 1150)
        self.assertEqual(sum(item.delta_counts for item in positive), 10000)
        self.assertEqual(sum(item.delta_counts for item in negative), -10000)
        self.assertEqual(shaped_target_counts(123, positive)[-1][1], 10123)
        self.assertEqual(shaped_target_counts(123, negative)[-1][1], -9877)

    def test_invalid_period_and_damping_fail_closed(self):
        with self.assertRaises(AntiSwayConfigError):
            build_zvd_impulses(9)
        with self.assertRaises(AntiSwayConfigError):
            build_zvd_impulses(1150, 1.0)
        with self.assertRaises(AntiSwayConfigError):
            plan_zvd_move(10, 1150, -0.1)


if __name__ == "__main__":
    unittest.main()
