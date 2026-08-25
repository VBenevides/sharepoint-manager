import runpy
import unittest
from pathlib import Path


CHECKS = sorted(Path(__file__).parents[1].joinpath("verification").glob("check_*.py"))


class FocusedChecks(unittest.TestCase):
    """Run the deterministic contract checks as independently reported tests."""


def _make_test(check: Path):
    def test(self: FocusedChecks) -> None:
        runpy.run_path(str(check), run_name="__main__")

    test.__name__ = f"test_{check.stem.removeprefix('check_')}"
    test.__qualname__ = f"FocusedChecks.{test.__name__}"
    return test


for check_path in CHECKS:
    setattr(
        FocusedChecks,
        f"test_{check_path.stem.removeprefix('check_')}",
        _make_test(check_path),
    )


if __name__ == "__main__":
    unittest.main()
