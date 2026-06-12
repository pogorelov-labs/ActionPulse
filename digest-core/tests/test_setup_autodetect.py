"""Tests for setup_autodetect — local environment discovery for the wizard."""

from collections import Counter

from digest_core import setup_autodetect as sa


class TestExtractEmails:
    def test_finds_emails_in_binary_noise(self):
        data = b"\x00\x01junk Ruslan.POGORELOV@megacorp.ru\x00more ruapgr2@owa.megacorp.ru\xff"
        found = sa._extract_emails(data)
        assert "Ruslan.POGORELOV@megacorp.ru" in found
        assert "ruapgr2@owa.megacorp.ru" in found

    def test_rejects_malformed(self):
        data = b"a..b@x.ru dot.@x.ru u@.bad.ru ok@good.ru"
        found = sa._extract_emails(data)
        assert found == ["ok@good.ru"]


class TestScanKeychain:
    def test_counts_across_files(self, tmp_path):
        kc = tmp_path / "Library" / "Keychains"
        kc.mkdir(parents=True)
        (kc / "login.keychain-db").write_bytes(b"\x00a@corp.ru\x00b@corp.ru\x00a@corp.ru\x00")
        (kc / "extra.keychain-db").write_bytes(b"\x00a@corp.ru\x00")
        counts = sa._scan_keychain_emails(kc)
        assert counts["a@corp.ru"] == 3
        assert counts["b@corp.ru"] == 1

    def test_email_split_across_chunks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sa, "_SCAN_CHUNK", 16)
        kc = tmp_path / "kc"
        kc.mkdir()
        (kc / "x.keychain-db").write_bytes(b"\x00" * 12 + b"user@megacorp.ru" + b"\x00" * 12)
        counts = sa._scan_keychain_emails(kc)
        assert counts["user@megacorp.ru"] >= 1

    def test_missing_dir(self, tmp_path):
        assert sa._scan_keychain_emails(tmp_path / "nope") == Counter()


class TestParseRealName:
    def test_caps_surname_first(self):
        assert sa._parse_real_name("POGORELOV Ruslan") == ("Ruslan", "POGORELOV")

    def test_caps_surname_last(self):
        assert sa._parse_real_name("Ruslan POGORELOV") == ("Ruslan", "POGORELOV")

    def test_plain_order(self):
        assert sa._parse_real_name("Ruslan Pogorelov") == ("Ruslan", "Pogorelov")

    def test_single_token(self):
        assert sa._parse_real_name("admin") == ("admin", None)

    def test_empty(self):
        assert sa._parse_real_name("  ") == (None, None)


class TestRankEmails:
    def _counts(self):
        return Counter(
            {
                "Ruslan.POGORELOV@megacorp.ru": 5,
                "ruapgr2@owa.megacorp.ru": 3,
                "personal@gmail.com": 8,
                "noreply@apple.com": 20,
            }
        )

    def test_name_match_beats_frequency(self):
        ranked = sa._rank_emails(
            self._counts(),
            login="ruapgr2",
            name_tokens=["Ruslan", "POGORELOV"],
            domain_hints=["megacorp.ru"],
        )
        assert ranked[0].address == "Ruslan.POGORELOV@megacorp.ru"
        assert "first+last name in the address" in ranked[0].reasons
        assert "domain matches the network" in ranked[0].reasons

    def test_login_artifact_is_demoted_for_upn(self):
        ranked = sa._rank_emails(
            self._counts(), login="ruapgr2", name_tokens=[], domain_hints=["megacorp.ru"]
        )
        owa = next(c for c in ranked if c.address == "ruapgr2@owa.megacorp.ru")
        assert "service address (login@host)" in owa.reasons

    def test_public_providers_sink(self):
        ranked = sa._rank_emails(
            self._counts(),
            login="ruapgr2",
            name_tokens=["Ruslan", "POGORELOV"],
            domain_hints=["megacorp.ru"],
        )
        addresses = [c.address for c in ranked]
        assert addresses.index("Ruslan.POGORELOV@megacorp.ru") < addresses.index(
            "personal@gmail.com"
        )
        assert addresses.index("Ruslan.POGORELOV@megacorp.ru") < addresses.index(
            "noreply@apple.com"
        )

    def test_casing_representative_is_most_frequent(self):
        counts = Counter({"User@Corp.ru": 3, "user@corp.ru": 1})
        ranked = sa._rank_emails(counts, login=None, name_tokens=[], domain_hints=["corp.ru"])
        assert ranked[0].address == "User@Corp.ru"
        assert ranked[0].count == 4


class TestExtractEwsHost:
    def _ranked(self, counts):
        return sa._rank_emails(counts, login="ruapgr2", name_tokens=[], domain_hints=[])

    def test_finds_owa_host_from_login_artifact(self):
        ranked = self._ranked(Counter({"ruapgr2@owa.megacorp.ru": 2}))
        host = sa._extract_ews_host(
            ranked, login="ruapgr2", upn="Ruslan.POGORELOV@megacorp.ru", domain_hints=[]
        )
        assert host == "owa.megacorp.ru"

    def test_bare_domain_artifact_is_not_a_host(self):
        ranked = self._ranked(Counter({"ruapgr2@megacorp.ru": 2}))
        host = sa._extract_ews_host(
            ranked, login="ruapgr2", upn="Ruslan.POGORELOV@megacorp.ru", domain_hints=[]
        )
        assert host is None

    def test_foreign_domain_rejected(self):
        ranked = self._ranked(Counter({"ruapgr2@owa.othercorp.com": 5}))
        host = sa._extract_ews_host(
            ranked, login="ruapgr2", upn="Ruslan.POGORELOV@megacorp.ru", domain_hints=[]
        )
        assert host is None

    def test_owa_prefix_preferred(self):
        ranked = self._ranked(Counter({"ruapgr2@vpn.megacorp.ru": 9, "ruapgr2@owa.megacorp.ru": 1}))
        host = sa._extract_ews_host(ranked, login="ruapgr2", upn="x.y@megacorp.ru", domain_hints=[])
        assert host == "owa.megacorp.ru"


class TestDetectEnvironment:
    def _fake_home(self, tmp_path, blobs):
        kc = tmp_path / "Library" / "Keychains"
        kc.mkdir(parents=True)
        (kc / "login.keychain-db").write_bytes(b"\x00".join(blobs))
        return tmp_path

    def test_full_corp_scenario(self, tmp_path, monkeypatch):
        """The reference machine: caps surname, non-name login, owa artifact."""
        home = self._fake_home(
            tmp_path,
            [b"Ruslan.POGORELOV@megacorp.ru"] * 4 + [b"ruapgr2@owa.megacorp.ru"] * 2,
        )
        monkeypatch.setattr(sa, "_machine_login", lambda: "ruapgr2")
        monkeypatch.setattr(sa, "_real_name_raw", lambda login, runner: "POGORELOV Ruslan")
        monkeypatch.setattr(sa, "_ad_domain", lambda runner: None)
        monkeypatch.setattr(sa, "_dns_search_domains", lambda runner: [])

        det = sa.detect_environment(home=home, dns_check=False)

        assert det.login == "ruapgr2"
        assert (det.first_name, det.last_name) == ("Ruslan", "POGORELOV")
        assert det.best_upn == "Ruslan.POGORELOV@megacorp.ru"
        # No network hints, but the login@owa.<upn-domain> artifact corroborates.
        assert det.upn_confidence == sa.CONF_HIGH
        assert det.ews_host == "owa.megacorp.ru"
        assert det.has_findings()
        assert any("Email (Keychain)" in n for n in det.notes)

    def test_domain_hint_raises_confidence_without_artifact(self, tmp_path, monkeypatch):
        home = self._fake_home(tmp_path, [b"Ruslan.POGORELOV@megacorp.ru"] * 2)
        monkeypatch.setattr(sa, "_machine_login", lambda: "ruapgr2")
        monkeypatch.setattr(sa, "_real_name_raw", lambda login, runner: "POGORELOV Ruslan")
        monkeypatch.setattr(sa, "_ad_domain", lambda runner: "megacorp.ru")
        monkeypatch.setattr(sa, "_dns_search_domains", lambda runner: [])

        det = sa.detect_environment(home=home, dns_check=False)
        assert det.best_upn == "Ruslan.POGORELOV@megacorp.ru"
        assert det.upn_confidence == sa.CONF_HIGH
        assert det.ews_host is None

    def test_no_name_match_is_medium_at_best(self, tmp_path, monkeypatch):
        """Cyrillic RealName cannot match a Latin local part — never auto-filled."""
        home = self._fake_home(tmp_path, [b"r.pog@megacorp.ru"] * 5)
        monkeypatch.setattr(sa, "_machine_login", lambda: "ruapgr2")
        monkeypatch.setattr(sa, "_real_name_raw", lambda login, runner: "ПОГОРЕЛОВ Руслан")
        monkeypatch.setattr(sa, "_ad_domain", lambda runner: "megacorp.ru")
        monkeypatch.setattr(sa, "_dns_search_domains", lambda runner: [])

        det = sa.detect_environment(home=home, dns_check=False)
        assert det.best_upn == "r.pog@megacorp.ru"
        assert det.upn_confidence == sa.CONF_MEDIUM

    def test_empty_machine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sa, "_machine_login", lambda: None)
        monkeypatch.setattr(sa, "_real_name_raw", lambda login, runner: None)
        monkeypatch.setattr(sa, "_ad_domain", lambda runner: None)
        monkeypatch.setattr(sa, "_dns_search_domains", lambda runner: [])

        det = sa.detect_environment(home=tmp_path, dns_check=False)
        assert det.best_upn is None
        assert det.upn_confidence == sa.CONF_NONE
        assert not det.has_findings()


class TestProbeParsers:
    def test_ad_domain_parse(self, monkeypatch):
        monkeypatch.setattr(sa.sys, "platform", "darwin")
        out = "You are bound...\nActive Directory Domain = megacorp.ru\nOther = x\n"
        assert sa._ad_domain(lambda cmd: out) == "megacorp.ru"

    def test_dns_search_domains_parse(self, monkeypatch):
        monkeypatch.setattr(sa.sys, "platform", "darwin")
        out = (
            "resolver #1\n  search domain[0] : megacorp.ru\n"
            "  search domain[1] : corp.local\n  search domain[2] : ad.megacorp.ru\n"
        )
        assert sa._dns_search_domains(lambda cmd: out) == ["megacorp.ru", "ad.megacorp.ru"]

    def test_real_name_via_dscl(self, monkeypatch):
        monkeypatch.setattr(sa.sys, "platform", "darwin")

        def runner(cmd):
            assert cmd[:3] == ["dscl", ".", "-read"]
            return "RealName:\n POGORELOV Ruslan\n"

        assert sa._real_name_raw("nonexistent-login-xyz", runner) == "POGORELOV Ruslan"
