import re
import ntpath


def camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower().replace("date_time", "datetime")


def get_filename(target_path: str) -> str:
    """
    Returns the name of a file from the given path.

    This function accepts paths terminating in '/' and works in any OS.

    Parameters
    ----------
    target_path : str
        Path to reach a file.

    Returns
    -------
    str
        Name of the file.
    """
    head, tail = ntpath.split(target_path)
    return tail or ntpath.basename(head)


def get_names_to_folder(target_path: str) -> list[str]:
    """
    Returns a list of names (str) of all folders from root to the target folder.

    Parameters
    ----------
    target_path : str
        Path to reach the target folder.

    Returns
    -------
    list of str
        Names of folders to reach the target folder (in order), including the name of the target folder.
    """

    if len(target_path) == 0:
        return []
    target_path = target_path[:-1] if (target_path[-1] in ["/", "\\"]) else target_path
    return target_path.replace("\\", "/").split("/")
