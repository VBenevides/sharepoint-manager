from pathlib import Path


def main() -> None:
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/staging.yml").read_text()
    smoke = (root / "verification/staging_smoke.py").read_text()
    for required in (
        'cron: "17 2 * * 1-5"',
        "workflow_dispatch:",
        "environment: staging",
        "concurrency:",
        "SP_STAGING_DENIED_SITE_URL",
        "SP_STAGING_ALLOW_DESTRUCTIVE",
        "verification/staging_smoke.py",
    ):
        assert required in workflow, required
    for required in (
        "list_files",
        "list_folders",
        "get_folder_delta",
        'conflict_behavior="fail"',
        "delete_file",
        "SPAuthorizationError",
    ):
        assert required in smoke, required


if __name__ == "__main__":
    main()
