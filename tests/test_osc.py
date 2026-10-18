import struct
import unittest

from milluminsync.osc import OscError, OscMessage, decode_message, decode_packet, encode_message


class OscCodecTest(unittest.TestCase):
    def roundtrip(self, msg: OscMessage) -> OscMessage:
        data = encode_message(msg)
        self.assertEqual(len(data) % 4, 0)
        return decode_message(data)

    def test_known_bytes(self):
        # Reference encoding from the OSC 1.0 spec examples.
        data = encode_message(OscMessage("/oscillator/4/frequency", [440.0]))
        self.assertEqual(
            data,
            b"/oscillator/4/frequency\x00,f\x00\x00" + struct.pack(">f", 440.0),
        )

    def test_roundtrip_types(self):
        msg = OscMessage("/millumin/board/launchedColumn", [3, "Intro", 1.5, True, False, None, b"\x01\x02", 2**40])
        out = self.roundtrip(msg)
        self.assertEqual(out.address, msg.address)
        self.assertEqual(out.type_tags(), "isfTFNbh")
        self.assertEqual(out.args[:2], [3, "Intro"])
        self.assertAlmostEqual(out.args[2], 1.5)
        self.assertEqual(out.args[3:], [True, False, None, b"\x01\x02", 2**40])

    def test_explicit_types(self):
        out = self.roundtrip(OscMessage("/x", [1, 2.0], "fd"))
        self.assertEqual(out.type_tags(), "fd")
        self.assertEqual(out.args, [1.0, 2.0])

    def test_unicode_string(self):
        out = self.roundtrip(OscMessage("/millumin/action/launchColumn", ["オープニング"]))
        self.assertEqual(out.args, ["オープニング"])

    def test_no_args(self):
        out = self.roundtrip(OscMessage("/millumin/action/stopColumn"))
        self.assertEqual(out.args, [])

    def test_missing_typetag(self):
        self.assertEqual(decode_message(b"/ping\x00\x00\x00").address, "/ping")

    def test_bundle(self):
        m1 = encode_message(OscMessage("/a", [1]))
        m2 = encode_message(OscMessage("/b", ["x"]))
        inner = b"#bundle\x00" + b"\x00" * 8 + struct.pack(">i", len(m2)) + m2
        bundle = b"#bundle\x00" + b"\x00" * 8 + struct.pack(">i", len(m1)) + m1 + struct.pack(">i", len(inner)) + inner
        msgs = list(decode_packet(bundle))
        self.assertEqual([m.address for m in msgs], ["/a", "/b"])

    def test_garbage(self):
        with self.assertRaises(OscError):
            decode_message(b"hello")
        with self.assertRaises(OscError):
            decode_message(b"/a\x00\x00,i\x00\x00\x00")


if __name__ == "__main__":
    unittest.main()
