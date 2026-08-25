import ast
import asyncio
import doctest
import py_compile
import runpy
import sys
import types
from pathlib import Path

_PUBLIC_CLASSES = {
    "AsyncSharepointManager": "sharepoint_manager/async_core.py",
    "SharepointManager": "sharepoint_manager/core.py",
    "TokenProvider": "sharepoint_manager/dataclasses.py",
    "OperationPolicy": "sharepoint_manager/dataclasses.py",
    "ClientCredential": "sharepoint_manager/dataclasses.py",
    "UserDelegatedCredential": "sharepoint_manager/dataclasses.py",
    "SPObject": "sharepoint_manager/dataclasses.py",
    "SPFolder": "sharepoint_manager/dataclasses.py",
    "SPFile": "sharepoint_manager/dataclasses.py",
    "SPDeletedItem": "sharepoint_manager/dataclasses.py",
    "SPCollectionPage": "sharepoint_manager/dataclasses.py",
    "SPDeltaPage": "sharepoint_manager/dataclasses.py",
    "QuickXorHash": "sharepoint_manager/utils.py",
}
_EXAMPLES = (
    "client_credential.py",
    "user_delegated_credential.py",
    "workflow_sync_fs.py",
    "workflow_sync_url.py",
    "workflow_async_fs.py",
    "workflow_async_url.py",
)


def _parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    return {arg.arg for arg in args if arg.arg not in {"self", "cls"}}


def _documented_parameters(docstring: str) -> set[str]:
    lines = docstring.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "Parameters")
    except StopIteration:
        return set()
    names: set[str] = set()
    for line in lines[start + 2 :]:
        if (
            line
            and not line[0].isspace()
            and line.strip()
            in {
                "Returns",
                "Yields",
                "Raises",
                "Warnings",
                "Examples",
                "Notes",
            }
        ):
            break
        if " : " in line:
            parameter_list = line.strip().split(" : ", 1)[0]
            names.update(part.strip() for part in parameter_list.split(","))
    return names


def _check_docstring(name: str, node: ast.AST) -> None:
    docstring = ast.get_docstring(node)
    assert docstring, f"missing public docstring: {name}"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        parameters = {
            name for name in _parameter_names(node) if not name.startswith("_")
        }
        if parameters:
            documented = _documented_parameters(docstring)
            assert "Parameters" in docstring, f"missing Parameters section: {name}"
            assert parameters <= documented, (
                f"undocumented parameters for {name}: {sorted(parameters - documented)}"
            )
        assert not any(
            parameter in _documented_parameters(docstring)
            for parameter in _parameter_names(node)
            if parameter.startswith("_")
        ), f"private recursion state documented as public API: {name}"
    for example in doctest.DocTestParser().get_examples(docstring):
        compile(example.source, f"<doctest {name}>", "exec")


def _check_public_docstrings(root: Path) -> None:
    for public_name, relative_path in _PUBLIC_CLASSES.items():
        tree = ast.parse((root / relative_path).read_text(encoding="utf-8"))
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == public_name
        )
        _check_docstring(public_name, class_node)
        for node in class_node.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                not node.name.startswith("_") or node.name == "__init__"
            ):
                _check_docstring(f"{public_name}.{node.name}", node)


def _check_examples(root: Path) -> None:
    examples = root / "examples"
    assert tuple(path.name for path in sorted(examples.glob("*.py"))) == tuple(
        sorted(_EXAMPLES)
    ), "examples must contain exactly the six documented modules"
    for filename in _EXAMPLES:
        path = examples / filename
        py_compile.compile(str(path), doraise=True)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.Attribute) and node.attr.startswith("_")
            for node in ast.walk(tree)
        ), f"private API call in {filename}"

    msal = types.ModuleType("msal")
    msal.ConfidentialClientApplication = type("Confidential", (), {})
    msal.PublicClientApplication = type("Public", (), {})
    sys.modules.setdefault("msal", msal)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(examples))
    original_to_thread = asyncio.to_thread

    async def direct_to_thread(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    asyncio.to_thread = direct_to_thread
    try:
        for filename in _EXAMPLES:
            runpy.run_path(str(examples / filename), run_name="__main__")
    finally:
        asyncio.to_thread = original_to_thread


def _check_public_contract(root: Path) -> None:
    _check_public_docstrings(root)
    _check_examples(root)


def main() -> None:
    root = Path(__file__).parents[1]
    readme = (root / "README.md").read_text()
    contributing = (root / "CONTRIBUTING.md").read_text()
    security = (root / "SECURITY.md").read_text()
    migration = (root / "docs/migration-0.1.md").read_text()
    for required in (
        "Sites.Selected",
        "TokenProvider",
        "OperationPolicy",
        "iter_collection()",
        "iter_folder_delta()",
        "SPAmbiguousWriteError",
        "QuickXorHash",
        "CONTRIBUTING.md",
        "SECURITY.md",
    ):
        assert required in readme, required
    assert "python -m unittest discover -s tests -v" in contributing
    assert "least-privilege test site" in security
    assert "iter_folder_delta()" in migration
    assert "UserDelegatedCredential" in migration
    _check_public_contract(root)


if __name__ == "__main__":
    main()
