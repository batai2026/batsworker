"""The packaging scripts must fail when the frozen sidecar CLI lags the source.

A stale bundle starts fine and only fails at launch, which is how a missing
`--frontend-dist` reached a running desktop app during verification.
"""

from scripts.check_sidecar_cli import missing_options, options_from_help

SOURCE_HELP = """\
usage: openworker-server [-h] [--cwd CWD] [--model MODEL] [--mode MODE]
                         [--host HOST] [--port PORT] [--frontend-dist FRONTEND_DIST]

options:
  -h, --help            show this help message and exit
  --cwd CWD             optional seed/default workspace
  --frontend-dist FRONTEND_DIST
                          Directory containing the prebuilt frontend (dist/)
"""

STALE_HELP = """\
usage: openworker-server [-h] [--cwd CWD] [--model MODEL] [--mode MODE]
                         [--host HOST] [--port PORT]

options:
  -h, --help            show this help message and exit
  --cwd CWD             optional seed/default workspace
"""


def test_detects_the_option_the_shell_passes() -> None:
    assert missing_options(SOURCE_HELP, STALE_HELP) == ["--frontend-dist"]


def test_matching_clis_report_no_drift() -> None:
    assert missing_options(SOURCE_HELP, SOURCE_HELP) == []


def test_a_bundle_with_extra_options_is_not_drift() -> None:
    assert missing_options(STALE_HELP, SOURCE_HELP) == []


def test_options_are_deduplicated() -> None:
    assert options_from_help("--port PORT\n--port PORT") == {"--port"}


def test_prose_dashes_are_not_options() -> None:
    # Single dashes and bare words must not enter the set.
    assert options_from_help("-h  help\n see docs ") == set()
