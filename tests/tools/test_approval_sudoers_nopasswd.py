"""A NOPASSWD sudoers grant of ALL or a file-writing/shell-capable binary is passwordless root (#15028).

The approval prompt is where the user and the agent learn what a command does, so writing such a
rule must say it is root-equivalent instead of the generic "overwrite system file via tee".
"""
import pytest

from tools.approval_detection import detect_dangerous_command

ROOT_EQUIVALENT = (
    "sudoers NOPASSWD rule for ALL or a file-writing/shell-capable command (e.g. curl, tee, cp) "
    "is passwordless root; use a root-owned wrapper script with fixed arguments instead"
)


@pytest.mark.parametrize("command", [
    'echo "me ALL=(ALL) NOPASSWD: /usr/bin/curl" | sudo tee /etc/sudoers.d/mihomo',
    "echo 'me ALL=(ALL) NOPASSWD: ALL' | sudo tee -a /etc/sudoers",
    "echo 'me ALL=(root) NOPASSWD: /usr/bin/systemctl restart mihomo, /bin/cp' > /etc/sudoers.d/x",
    "echo 'me ALL=(root) NOPASSWD: /usr/bin/curl *' > /etc/sudoers.d/x",
    "sudo bash -c \"echo 'me ALL=(ALL) NOPASSWD: /usr/bin/wget' > /etc/sudoers.d/w\"",
    "sudo tee /etc/sudoers.d/c <<'EOF'\nme ALL=(ALL) NOPASSWD: /usr/bin/curl\nEOF",
    "sudo tee /private/etc/sudoers.d/c <<'EOF'\nme ALL=(ALL) NOPASSWD:SETENV: /bin/sh\nEOF",
])
def test_unrestricted_root_equivalent_grant_names_the_risk(command):
    assert detect_dangerous_command(command) == (True, ROOT_EQUIVALENT, ROOT_EQUIVALENT)


@pytest.mark.parametrize("command", [
    # The recommended shape: a wrapper script with its arguments fixed inside it.
    "echo 'me ALL=(root) NOPASSWD: /usr/local/bin/update-mihomo.sh' | sudo tee /etc/sudoers.d/mihomo",
    # Arguments pinned in the rule itself.
    "echo 'me ALL=(root) NOPASSWD: /usr/bin/systemctl restart mihomo' | sudo tee /etc/sudoers.d/mihomo",
])
def test_restricted_grant_keeps_the_generic_system_write_reason(command):
    is_dangerous, _, description = detect_dangerous_command(command)
    assert is_dangerous and description != ROOT_EQUIVALENT


@pytest.mark.parametrize("command", [
    "sudo grep -r NOPASSWD /etc/sudoers.d",
    "echo 'NOPASSWD: /usr/bin/curl' >> notes.txt",
])
def test_reading_or_mentioning_nopasswd_is_not_flagged(command):
    assert detect_dangerous_command(command) == (False, None, None)
