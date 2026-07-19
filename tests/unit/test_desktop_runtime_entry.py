from __future__ import annotations

import pytest

from desktop.runtime_entry import build_parser


def test_desktop_runtime_entry_accepts_only_local_gateway_arguments():
    parser = build_parser()

    args = parser.parse_args(["server", "--host", "localhost", "--port", "8123"])

    assert args.command == "server"
    assert args.host == "localhost"
    assert args.port == 8123


@pytest.mark.parametrize(
    "argv",
    [
        ["server", "--host", "0.0.0.0"],
        ["server", "--port", "80"],
        ["server", "--port", "65536"],
        ["agent"],
    ],
)
def test_desktop_runtime_entry_rejects_remote_privileged_and_non_gateway_commands(argv):
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)
