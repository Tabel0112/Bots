"""Offline public-navigation preflight; transport enforcement is separate."""

import unittest
from unittest.mock import patch

from argus.registry import DOMAIN_POLICY, domain_allowed


class PublicDomainPolicyTests(unittest.TestCase):
    def assert_domains(self, domains, expected):
        for domain in domains:
            with self.subTest(domain=domain):
                allowed, reason = domain_allowed(domain)
                self.assertEqual(allowed, expected, reason)
                self.assertTrue(reason)

    def test_public_provider_pages_and_docs_are_eligible(self):
        self.assert_domains((
            "docs.stripe.com", "stripe.com", "developer.paypal.com",
            "paypal.com", "www.paypal.com", "auth0.com",
            "example.com", "jobs.example.com", "8.8.8.8",
            "2606:4700:4700::1111",
        ), True)

    def test_dedicated_login_and_checkout_hosts_are_blocked(self):
        self.assert_domains((
            "accounts.google.com", "LOGIN.MICROSOFTONLINE.COM.",
            "checkout.stripe.com", "nested.checkout.stripe.com",
        ), False)

    def test_policy_matches_whole_labels(self):
        self.assert_domains((
            "notaccounts.google.com", "checkout.stripe.com.example.com",
            "public-local.example.com",
        ), True)

    def test_normalises_idna_case_and_dns_trailing_dot(self):
        allowed, reason = domain_allowed(" BÜCHER.DE. ")
        self.assertTrue(allowed, reason)
        self.assertIn("xn--bcher-kva.de", reason)
        self.assertFalse(domain_allowed("accounts\u3002google.com")[0])

    def test_rejects_local_and_reserved_names(self):
        self.assert_domains((
            "localhost", "app.localhost", "LOCALHOST.", "printer",
            "host.local", "host.internal", "host.lan", "host.home",
            "router.home.arpa", "host.corp", "host.intranet",
            "host.test", "host.invalid", "host.example", "host.onion",
        ), False)

    def test_rejects_non_public_ipv4_and_ipv6_literals(self):
        self.assert_domains((
            "0.0.0.0", "10.2.3.4", "127.0.0.1", "172.16.1.2",
            "192.168.1.2", "169.254.169.254", "100.64.0.1",
            "192.0.2.1", "198.51.100.1", "203.0.113.1",
            "224.0.0.1", "240.0.0.1", "255.255.255.255",
            "::", "::1", "fc00::1", "fe80::1", "ff02::1",
            "2001:db8::1", "::ffff:127.0.0.1",
        ), False)

    def test_rejects_browser_numeric_address_aliases(self):
        self.assert_domains((
            "127.1", "127.0.1", "2130706433", "0x7f000001",
            "0177.0.0.1", "0x7f.0.0.1", "017700000001",
            "127.000.000.001", "0xffffffff", "example.123",
            "example.0x7f000001", "999.1.1.1",
        ), False)

    def test_rejects_urls_ports_and_malformed_inputs(self):
        self.assert_domains((
            None, 123, "", " ", "https://example.com", "//example.com",
            "example.com/path", "example.com:443", "user@example.com",
            "example.com?x=y", "example.com#fragment", "example..com",
            "example.com..", "-example.com", "example-.com",
            "exa_mple.com", "bad domain.com", "[::1]", "fe80::1%en0",
            "[2606:4700:4700::1111]", "\ud800.com", "a" * 64 + ".com",
            "xn--a.com",
        ), False)

    def test_offline_helper_never_resolves_dns(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("network forbidden")):
            self.assertTrue(domain_allowed("jobs.example.com")[0])

    def test_closed_policy_also_rejects_public_ip_literals(self):
        with patch.dict(DOMAIN_POLICY, {"allow_any_other": False}):
            self.assert_domains(("example.com", "8.8.8.8", "2606:4700:4700::1111"), False)


if __name__ == "__main__":
    unittest.main()
