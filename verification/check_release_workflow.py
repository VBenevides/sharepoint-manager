from pathlib import Path


def main() -> None:
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/release.yml").read_text()
    changelog = (root / "CHANGELOG.md").read_text()
    for required in (
        'tags: ["v*.*.*"]',
        "environment: release",
        "id-token: write",
        "requirements-build.txt",
        'test "$version" = "$(tr -d',
        "twine check --strict",
        "cyclonedx-py",
        "actions/attest-build-provenance",
        "pypa/gh-action-pypi-publish",
        "Smoke-install",
    ):
        assert required in workflow, required
    assert "## [0.1.0]" in changelog


if __name__ == "__main__":
    main()
