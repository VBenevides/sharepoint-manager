from pathlib import Path
import runpy


def main() -> None:
    checks = sorted(Path(__file__).parent.glob("check_*.py"))
    for check in checks:
        runpy.run_path(str(check), run_name="__main__")
    print(f"{len(checks)} focused checks passed")


if __name__ == "__main__":
    main()
