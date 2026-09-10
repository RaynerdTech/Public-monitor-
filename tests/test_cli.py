from typer.main import get_command

from app.main import cli


def test_cli_builds_without_invalid_option_declarations():
    command = get_command(cli)
    assert command is not None


def test_cli_contains_watch_all_command():
    command = get_command(cli)
    assert "watch-all" in command.commands
