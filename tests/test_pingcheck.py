"""Reachability probe: SSRF guards and check-host response parsing."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "console" / "api"))

from lunel_console.services.pingcheck import PingCheckError, _summarise, validate_public_host


class HostGuardTests(unittest.TestCase):
    """The probed host is operator-controlled, so internal targets must never pass."""

    REJECTED = [
        "localhost", "127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
        "169.254.169.254", "0.0.0.0", "::1", "[::1]", "[fd00::1]",
        "http://example.com", "https://example.com", "example.com/path",
        "user@example.com", "example.com:8080", "", "   ", "foo.local",
        "x.internal", "0177.0.0.1", "2130706433", "0x7f.0.0.1", "8.8.8.8",
    ]

    def test_internal_and_ambiguous_targets_are_refused(self):
        for host in self.REJECTED:
            with self.subTest(host=host):
                with self.assertRaises(PingCheckError):
                    validate_public_host(host)

    def test_public_domain_is_normalised(self):
        self.assertEqual(validate_public_host("Example.COM."), "example.com")


class ResultParsingTests(unittest.TestCase):
    """check-host answers progressively; verdicts must not overstate certainty."""

    NODES = {
        "us1.node.check-host.net": ["us", "USA", "Los Angeles", "5.253.30.82", "AS18978"],
        "ir1.node.check-host.net": ["ir", "Iran", "Tehran", "1.2.3.4", "AS9000"],
    }

    def test_iran_reachable_when_a_probe_succeeds(self):
        results = {
            "us1.node.check-host.net": [[["OK", 0.044, "9.9.9.9"], ["OK", 0.0433]]],
            "ir1.node.check-host.net": [[["OK", 0.12, "9.9.9.9"], ["TIMEOUT", 3.0]]],
        }
        iran_ok, verdicts, pending = _summarise(self.NODES, results)
        self.assertTrue(iran_ok)
        self.assertFalse(pending)
        ir = next(v for v in verdicts if v["country"] == "ir")
        self.assertTrue(ir["reachable"])
        self.assertEqual(ir["ok"], 1)
        self.assertEqual(ir["total"], 2)
        self.assertAlmostEqual(ir["best_ms"], 120.0, delta=0.5)

    def test_not_reachable_when_all_iran_probes_fail(self):
        results = {
            "us1.node.check-host.net": [[["OK", 0.05, "9.9.9.9"]]],
            "ir1.node.check-host.net": [[["TIMEOUT", 3.0], ["MALFORMED", 0.04]]],
        }
        iran_ok, _, pending = _summarise(self.NODES, results)
        self.assertFalse(iran_ok)
        self.assertFalse(pending)

    def test_pending_when_results_have_not_arrived(self):
        _, _, pending = _summarise(self.NODES, {"ir1.node.check-host.net": None})
        self.assertTrue(pending)

    def test_dns_failure_node_is_not_treated_as_reachable(self):
        results = {"ir1.node.check-host.net": [[None]]}
        iran_ok, verdicts, _ = _summarise(self.NODES, results)
        self.assertFalse(iran_ok)
        self.assertFalse(verdicts[0]["reachable"])


if __name__ == "__main__":
    unittest.main()