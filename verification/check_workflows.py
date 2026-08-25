import re
from pathlib import Path

_PINNED_ACTION = re.compile(r"^\s*(?:-\s+)?uses: [^\s]+@([0-9a-f]{40})(?:\s+#.*)?$")


def main() -> None:
    workflow_dir = Path(__file__).parents[1] / ".github/workflows"
    workflows = list(workflow_dir.glob("*.yml"))
    assert workflows
    for path in workflows:
        for line in path.read_text().splitlines():
            if "uses:" in line:
                assert _PINNED_ACTION.match(line), f"un-pinned action in {path}: {line}"

    codeql = (workflow_dir / "codeql.yml").read_text()
    for required in (
        "  pull_request:",
        "  push:",
        'cron: "43 3 * * 1"',
        "security-events: write",
        "queries: security-extended",
        "github/codeql-action/init@",
        "github/codeql-action/analyze@",
        "pip-audit",
        "cyclonedx-py environment",
    ):
        assert required in codeql, required


if __name__ == "__main__":
    main()
