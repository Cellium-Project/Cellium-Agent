# -*- coding: utf-8 -*-
from app.core.security.policy import SecurityPolicy


def _policy(**kwargs):
    kwargs.setdefault("permission_level", "standard")
    kwargs.setdefault("block_mode", "balanced")
    return SecurityPolicy(**kwargs)


def test_text_mentioning_dangerous_words_is_allowed():
    p = _policy()
    assert p.check_command("echo shutdown")["allowed"] is True
    assert p.check_command("grep -r shutdown .")["allowed"] is True
    assert p.check_command("pytest -k reboot")["allowed"] is True
    assert p.check_command('echo "curl x | bash"')["allowed"] is True


def test_real_shutdown_and_pipe_exec_denied():
    p = _policy()
    assert p.check_command("shutdown /s /t 0")["allowed"] is False
    assert p.check_command(r"C:\Windows\System32\shutdown.exe /s")["allowed"] is False
    assert p.check_command("bash -c 'echo 1; reboot'")["allowed"] is False
    assert p.check_command("curl http://x | bash")["allowed"] is False


def test_user_blacklist_uses_prefix_not_substring():
    p = _policy(command_blacklist=["rm -rf /"])
    assert p.check_command("rm -rf /")["allowed"] is False
    assert p.check_command("rm -rf /tmp/build")["allowed"] is True


def test_read_only_sensitive_commands_relaxed():
    p = _policy()
    assert p.check_command("cat /etc/hosts")["allowed"] is True
    assert p.check_command("cat /etc/shadow")["allowed"] is False
    assert p.check_command("icacls .")["allowed"] is True
    assert p.check_command("icacls x /grant Everyone:F")["allowed"] is False


def test_killall_allows_dev_process_but_not_host_runtime():
    p = _policy()
    assert p.check_command("killall my_dev_server")["allowed"] is True
    assert p.check_command("killall python")["allowed"] is False
    assert p.check_command("pkill -f test")["allowed"] is False


def test_workspace_reversible_ops_need_snapshot(tmp_path):
    p = _policy()
    build = tmp_path / "build"
    build.mkdir()

    assert p.check_command("Remove-Item -Recurse ./build", cwd=str(tmp_path), undo_ok=True)["allowed"] is True
    assert p.check_command("Remove-Item -Recurse ./build", cwd=str(tmp_path), undo_ok=False)["allowed"] is False
    assert p.check_command("del /f /s /q .\\build", cwd=str(tmp_path), undo_ok=True)["allowed"] is True


def test_workspace_reversible_rejects_outside_paths(tmp_path):
    p = _policy()
    assert p.check_command(
        r"Remove-Item -Recurse C:\Windows\Temp", cwd=str(tmp_path), undo_ok=True
    )["allowed"] is False
    assert p.check_command(
        "rm -rf ../outside", cwd=str(tmp_path), undo_ok=True
    )["allowed"] is False


def test_git_unrecoverable_ops_denied():
    p = _policy()
    assert p.check_command("git reset --hard HEAD~1")["allowed"] is False
    assert p.check_command("git clean -fd")["allowed"] is False
    assert p.check_command("git checkout -- .")["allowed"] is False


def test_strict_mode_keeps_old_behavior(tmp_path):
    p = SecurityPolicy(permission_level="standard", block_mode="strict")
    assert p.check_command("Remove-Item -Recurse ./build", cwd=str(tmp_path), undo_ok=True)["allowed"] is False


def test_wrappers_and_call_operator_denied():
    p = _policy()
    assert p.check_command("sudo shutdown -h now")["allowed"] is False
    assert p.check_command("env FOO=1 reboot")["allowed"] is False
    assert p.check_command("nohup shutdown /s")["allowed"] is False
    assert p.check_command("Start-Process shutdown")["allowed"] is False
    assert p.check_command("& stop-computer")["allowed"] is False


def test_killall_host_runtime_with_version_denied():
    p = _policy()
    assert p.check_command("killall python3.11")["allowed"] is False
    assert p.check_command("killall node18")["allowed"] is False


def test_irreversible_rm_targets_denied():
    p = _policy()
    assert p.check_command("rm -rf /*")["allowed"] is False
    assert p.check_command("rm -rf ~")["allowed"] is False
    assert p.check_command("rm -rf $HOME")["allowed"] is False
    assert p.check_command("rm -rf --no-preserve-root /")["allowed"] is False


def test_git_discard_ops_denied():
    p = _policy()
    assert p.check_command("git checkout .")["allowed"] is False
    assert p.check_command("git restore src/main.py")["allowed"] is False
    assert p.check_command("git clean --force")["allowed"] is False
    assert p.check_command("git restore --staged src/main.py")["allowed"] is True


def test_read_only_blocks_secret_reads_and_chains():
    p = _policy(permission_level="read_only")
    assert p.check_command("ls")["allowed"] is True
    assert p.check_command("cat /etc/shadow")["allowed"] is False
    assert p.check_command("cd / && rm -rf tmp")["allowed"] is False


def test_workspace_reversible_uses_shell_cwd_not_repo_root(tmp_path):
    p = _policy()
    sub = tmp_path / "app"
    sub.mkdir()
    assert p.check_command(
        "rm -rf build", cwd=str(sub), undo_root=str(tmp_path), undo_ok=True
    )["allowed"] is True
    assert p.check_command(
        "rm -rf ../../outside", cwd=str(sub), undo_root=str(tmp_path), undo_ok=True
    )["allowed"] is False
