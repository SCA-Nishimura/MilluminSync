import os
import socket
import unittest

from milluminsync.diag import PortUser, diagnose_target, parse_lsof, parse_netstat, port_users

LSOF_HEADER = "COMMAND         PID   USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n"


class ParseTest(unittest.TestCase):
    def test_lsof(self):
        out = LSOF_HEADER + (
            "Millumin5     4242 show   45u  IPv4 0x9a1b2c3d4e5f6a7b      0t0  UDP 127.0.0.1:5000\n"
            "ControlCenter  612 show   10u  IPv4 0x1111111111111111      0t0  TCP *:5000 (LISTEN)\n"
            "ControlCenter  612 show   11u  IPv6 0x2222222222222222      0t0  TCP *:5000 (LISTEN)\n"
            "Google\\x20Chrome 77 show 5u IPv4 0x3 0t0 UDP *:5000\n"
        )
        self.assertEqual(parse_lsof(out), [
            PortUser("TCP", "ControlCenter", 612),
            PortUser("UDP", "Google Chrome", 77),
            PortUser("UDP", "Millumin5", 4242),
        ])

    def test_lsof_empty(self):
        self.assertEqual(parse_lsof(""), [])

    def test_netstat(self):
        out = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:5000           0.0.0.0:0              LISTENING       900
  TCP    127.0.0.1:5000         127.0.0.1:61000        ESTABLISHED     901
  TCP    0.0.0.0:50001          0.0.0.0:0              LISTENING       902
  UDP    0.0.0.0:5000           *:*                                    4242
  UDP    [::]:5000              *:*                                    4242
  UDP    127.0.0.1:12000        *:*                                    77
"""
        self.assertEqual(parse_netstat(out, 5000), [("TCP", 900), ("UDP", 4242)])
        self.assertEqual(parse_netstat(out, 12000), [("UDP", 77)])

    def test_live_port_users_finds_this_process(self):
        # Runs the real lsof / netstat on the CI machine.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            users = port_users(port)
        if users is None:
            self.skipTest("lsof / netstat not available")
        self.assertIn(("UDP", os.getpid()), [(u.proto, u.pid) for u in users])


class DiagnoseTest(unittest.TestCase):
    def test_only_millumin_is_ok(self):
        d = diagnose_target(5000, [PortUser("UDP", "Millumin5", 1)])
        self.assertTrue(d.ok)

    def test_nobody_listening(self):
        d = diagnose_target(5000, [])
        self.assertFalse(d.ok)
        self.assertIn("受信しているソフトがありません", d.text)

    def test_airplay(self):
        d = diagnose_target(5000, [PortUser("UDP", "Millumin5", 1), PortUser("TCP", "ControlCenter", 2)])
        self.assertFalse(d.ok)
        self.assertIn("AirPlay", d.text)

    def test_other_app_shares_port(self):
        d = diagnose_target(5000, [PortUser("UDP", "Millumin5", 1), PortUser("UDP", "Chataigne", 2)])
        self.assertFalse(d.ok)
        self.assertIn("Chataigne", d.text)

    def test_other_app_only(self):
        d = diagnose_target(5000, [PortUser("UDP", "Chataigne", 2)])
        self.assertFalse(d.ok)
        self.assertIn("Millumin ではなく", d.text)

    def test_two_millumin_listeners(self):
        d = diagnose_target(5000, [PortUser("UDP", "Millumin5", 1), PortUser("UDP", "Millumin5", 3)])
        self.assertFalse(d.ok)

    def test_unknown(self):
        self.assertTrue(diagnose_target(5000, None).ok)


if __name__ == "__main__":
    unittest.main()
