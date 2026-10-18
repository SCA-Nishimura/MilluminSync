"""End-to-end over real UDP sockets: fake master Millumin -> relay -> fake slave Millumin."""

import socket
import tempfile
import time
import unittest
from pathlib import Path

from milluminsync.config import Config
from milluminsync.osc import OscMessage, decode_packet, encode_message
from milluminsync.relay import Relay, Target


class FakeMillumin:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(2.0)
        self.port = self.sock.getsockname()[1]

    def recv(self) -> OscMessage:
        data, _ = self.sock.recvfrom(65535)
        return list(decode_packet(data))[0]

    def close(self):
        self.sock.close()


def send(port: int, msg: OscMessage) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(encode_message(msg), ("127.0.0.1", port))


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.slaves = [FakeMillumin(), FakeMillumin()]
        self.events = []
        cfg = Config()
        self.relay = Relay(
            0,
            [Target("127.0.0.1", s.port) for s in self.slaves],
            cfg.rules,
            listen_host="127.0.0.1",
            on_event=self.events.append,
        )
        self.relay.start()

    def tearDown(self):
        self.relay.stop()
        for s in self.slaves:
            s.close()

    def test_column_launch_reaches_all_slaves(self):
        send(self.relay.listen_port, OscMessage("/millumin/board/launchedColumn", [2, "Scene B"]))
        for slave in self.slaves:
            got = slave.recv()
            self.assertEqual(got.address, "/millumin/action/launchColumn")
            self.assertEqual(got.args, [2])
            self.assertEqual(got.type_tags(), "i")
        self.assertEqual(self.relay.received, 1)
        self.assertEqual(self.relay.forwarded, 2)

    def test_sequence_order_is_kept(self):
        for i in range(1, 21):
            send(self.relay.listen_port, OscMessage("/millumin/board/launchedColumn", [i, f"c{i}"]))
        got = [self.slaves[0].recv().args[0] for _ in range(20)]
        self.assertEqual(got, list(range(1, 21)))

    def test_bundle_input(self):
        import struct
        m = encode_message(OscMessage("/millumin/board/launchedColumn", [5, "x"]))
        bundle = b"#bundle\x00" + b"\x00" * 8 + struct.pack(">i", len(m)) + m
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(bundle, ("127.0.0.1", self.relay.listen_port))
        self.assertEqual(self.slaves[0].recv().args, [5])

    def test_garbage_does_not_kill_relay(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"not osc", ("127.0.0.1", self.relay.listen_port))
        send(self.relay.listen_port, OscMessage("/millumin/action/stopColumn"))
        self.assertEqual(self.slaves[0].recv().address, "/millumin/action/stopColumn")
        self.assertTrue(any(e.kind == "error" for e in self.events))

    def test_rule_update_while_running(self):
        from milluminsync.rules import Rule
        self.relay.update(rules=[Rule(match="/x", send="/millumin/action/launchColumn", args=[9])])
        send(self.relay.listen_port, OscMessage("/x"))
        self.assertEqual(self.slaves[0].recv().args, [9])


class PortConflictTest(unittest.TestCase):
    def test_port_in_use_raises(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("0.0.0.0", 0))
        try:
            relay = Relay(blocker.getsockname()[1], [], [])
            with self.assertRaises(OSError):
                relay.start()
        finally:
            blocker.close()


class ConfigTest(unittest.TestCase):
    def test_save_load(self):
        path = Path(tempfile.mkdtemp()) / "c.json"
        cfg = Config(listen_port=9000, targets=["192.168.0.200:5000", "192.168.0.201:5000"])
        cfg.rules[0].enabled = False
        cfg.save(path)
        loaded = Config.load(path)
        self.assertEqual(loaded.listen_port, 9000)
        self.assertEqual(loaded.targets, cfg.targets)
        self.assertEqual(loaded.rules, cfg.rules)

    def test_target_parse(self):
        self.assertEqual(Target.parse("192.168.0.200:5000"), Target("192.168.0.200", 5000))
        self.assertEqual(Target.parse(" 10.0.0.1 "), Target("10.0.0.1", 5000))


if __name__ == "__main__":
    unittest.main()
