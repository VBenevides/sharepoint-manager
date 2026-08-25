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
        'pull_request:\n    branches: ["main"]',
        'push:\n    branches: ["**"]',
        'cron: "0 0 * * 1"',
        "security-events: write",
        "queries: security-extended",
        "github/codeql-action/init@",
        "github/codeql-action/analyze@",
    ):
        assert required in codeql, required

    security = (workflow_dir / "security.yml").read_text()
    for required in (
        'push:\n    branches: ["**"]',
        'pull_request:\n    branches: ["main"]',
        "pip-audit",
        "pip-licenses",
        "cyclonedx-py environment",
        'bomFormat == "CycloneDX"',
        'components | type == "array"',
        "gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e",
        "pull-requests: write",
        "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
    ):
        assert required in security, required


if __name__ == "__main__":
    main()
