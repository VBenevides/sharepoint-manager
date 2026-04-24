import re
import ntpath
import base64


class QuickXorHash:
    """Hash algorithm used by Microsoft Graph for hashing file contents for SharePoint and OneDrive.

    It can be used to check if a local version of a file is the same as the version in SharePoint without downloading the file, by comparing the hash of the local file with the hash provided by Microsoft Graph in the file metadata.
    """

    _MASK_160 = (1 << 160) - 1

    def __init__(self):
        self._state = 0
        self._lengthSoFar = 0
        self._shiftSoFar = 0

    def update(self, array: bytes):
        cbSize = len(array)
        if cbSize == 0:
            return

        limit = (cbSize // 160) * 160

        if limit > 0:
            accum = 0
            mv = memoryview(array)
            from_bytes = int.from_bytes

            for i in range(0, limit, 160):
                accum ^= from_bytes(mv[i : i + 160], "little")

            collapsed = accum.to_bytes(160, "little")
            self._apply_block(collapsed)

        # 2. Process any remaining bytes at the end of the chunk
        if cbSize > limit:
            self._apply_block(array[limit:])

        self._lengthSoFar += cbSize

    def _apply_block(self, block: bytes):
        state = self._state
        shift = self._shiftSoFar
        mask = self._MASK_160

        for b in block:
            val = b << shift
            state ^= (val & mask) ^ (val >> 160)
            shift += 11
            if shift >= 160:
                shift -= 160

        self._state = state
        self._shiftSoFar = shift

    def digest(self):
        final_state = self._state ^ (self._lengthSoFar << 96)
        return final_state.to_bytes(20, "little")

    def b64digest(self):
        return base64.b64encode(self.digest()).decode("utf-8")

    def hexdigest(self):
        return self.digest().hex()


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
