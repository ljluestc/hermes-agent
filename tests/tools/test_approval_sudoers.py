"""Approval-gate wording for NOPASSWD sudoers writes. Regression for #15028."""

import pytest

from tools.approval import detect_dangerous_command


class TestNopasswdSudoersWrite:
    """A NOPASSWD rule like `NOPASSWD: /usr/bin/curl` is passwordless root,
    so the approval prompt must name that risk instead of a generic system-file write."""

    @pytest.mark.parametrize("command", [
        'echo "u ALL=(root) NOPASSWD: /usr/bin/curl" | sudo tee /etc/sudoers.d/mihomo',
        "sudo tee -a /etc/sudoers.d/x <<'EOF'\nu ALL=(ALL) NOPASSWD: ALL\nEOF",
        'echo "u ALL=NOPASSWD: /usr/bin/curl" >> /etc/sudoers',
        "sudo cp /tmp/rule /etc/sudoers.d/rule  # u ALL=NOPASSWD: /usr/bin/wget",
        'sudo sed -i "$ a u ALL=NOPASSWD: /usr/bin/tee" /private/etc/sudoers',
    ])
    def test_every_write_spelling_gets_the_privilege_escalation_prompt(self, command):
        _, generic_key, _ = detect_dangerous_command('echo "Defaults env_reset" | sudo tee /etc/sudoers.d/x')
        is_dangerous, key, desc = detect_dangerous_command(command)
        assert is_dangerous is True
        assert key != generic_key
        assert "nopasswd" in desc.lower() and "root" in desc.lower()

    def test_reading_sudoers_is_not_flagged(self):
        assert detect_dangerous_command("grep NOPASSWD /etc/sudoers /etc/sudoers.d/*")[0] is False
