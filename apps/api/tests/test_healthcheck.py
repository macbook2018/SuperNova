from app.healthcheck import worker_process_running


def test_worker_process_running_finds_module_command(tmp_path):
    process_dir = tmp_path / "123"
    process_dir.mkdir()
    (process_dir / "cmdline").write_bytes(
        b"/usr/local/bin/python\0-m\0app.knowledge.worker\0"
    )

    assert worker_process_running(
        "app.knowledge.worker",
        proc_root=tmp_path,
        current_pid=999,
    )


def test_worker_process_running_ignores_current_process(tmp_path):
    process_dir = tmp_path / "123"
    process_dir.mkdir()
    (process_dir / "cmdline").write_bytes(
        b"/usr/local/bin/python\0-m\0app.knowledge.worker\0"
    )

    assert not worker_process_running(
        "app.knowledge.worker",
        proc_root=tmp_path,
        current_pid=123,
    )


def test_worker_process_running_rejects_different_module(tmp_path):
    process_dir = tmp_path / "123"
    process_dir.mkdir()
    (process_dir / "cmdline").write_bytes(
        b"/usr/local/bin/python\0-m\0app.chat_import.worker\0"
    )

    assert not worker_process_running(
        "app.knowledge.worker",
        proc_root=tmp_path,
        current_pid=999,
    )
