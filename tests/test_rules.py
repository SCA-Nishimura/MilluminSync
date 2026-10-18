import unittest

from milluminsync.osc import OscMessage
from milluminsync.rules import Rule, default_rules


def fire(rules, msg):
    return [o for r in rules if (o := r.apply(msg, now=0.0)) is not None]


class RuleTest(unittest.TestCase):
    def test_default_column_launch(self):
        out = fire(default_rules(), OscMessage("/millumin/board/launchedColumn", [4, "Scene 4"]))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].address, "/millumin/action/launchColumn")
        self.assertEqual(out[0].args, [4])
        self.assertEqual(out[0].type_tags(), "i")

    def test_float_index_becomes_int(self):
        out = fire(default_rules(), OscMessage("/millumin/board/launchedColumn", [4.0, "x"]))
        self.assertEqual(out[0].type_tags(), "i")
        self.assertEqual(out[0].args, [4])

    def test_launch_by_name(self):
        rules = default_rules()
        for r in rules:
            r.enabled = r.name == "Column launch (by name)"
        out = fire(rules, OscMessage("/millumin/board/launchedColumn", [4, "Scene 4"]))
        self.assertEqual(out[0].args, ["Scene 4"])
        self.assertEqual(out[0].type_tags(), "s")

    def test_missing_arg_does_not_fire(self):
        out = fire(default_rules(), OscMessage("/millumin/board/launchedColumn", []))
        self.assertEqual(out, [])

    def test_action_pass_through(self):
        msg = OscMessage("/millumin/action/goToNextColumn", [])
        out = fire(default_rules(), msg)
        self.assertEqual([(o.address, o.args) for o in out], [("/millumin/action/goToNextColumn", [])])
        msg = OscMessage("/millumin/action/launchColumn", ["Intro"])
        self.assertEqual(fire(default_rules(), msg)[0].args, ["Intro"])

    def test_unrelated_address_dropped(self):
        self.assertEqual(fire(default_rules(), OscMessage("/millumin/layer:A/media/time", [1.0])), [])

    def test_wildcard_capture(self):
        r = Rule(match="/millumin/layer:*/media/time", send="/millumin/layer:{1}/media/time", args=["$0:f"])
        out = r.apply(OscMessage("/millumin/layer:Video 1/media/time", [12]), now=0.0)
        self.assertEqual(out.address, "/millumin/layer:Video 1/media/time")
        self.assertEqual(out.type_tags(), "f")
        self.assertIsNone(r.apply(OscMessage("/millumin/layer:a/b/media/time", [1]), now=0.0))

    def test_literals_and_all(self):
        r = Rule(match="/in", send="/out", args=[7, 0.5, "hi", "$$literal", "$*"])
        out = r.apply(OscMessage("/in", [1, "a"]), now=0.0)
        self.assertEqual(out.args, [7, 0.5, "hi", "$literal", 1, "a"])
        self.assertEqual(out.type_tags(), "ifssis")

    def test_throttle(self):
        r = Rule(match="/t", throttle_ms=100)
        self.assertIsNotNone(r.apply(OscMessage("/t", [1]), now=10.0))
        self.assertIsNone(r.apply(OscMessage("/t", [2]), now=10.05))
        self.assertIsNotNone(r.apply(OscMessage("/t", [3]), now=10.2))

    def test_disabled(self):
        r = Rule(match="/t", enabled=False)
        self.assertIsNone(r.apply(OscMessage("/t"), now=0.0))

    def test_dict_roundtrip(self):
        for r in default_rules():
            self.assertEqual(Rule.from_dict(r.to_dict()), r)


if __name__ == "__main__":
    unittest.main()
