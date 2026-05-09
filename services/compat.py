import importlib.metadata
import sys


def _version_tuple(package_name):
    try:
        version = importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return ()

    parts = []
    for raw_part in version.split("."):
        numeric = ""
        for char in raw_part:
            if not char.isdigit():
                break
            numeric += char
        if numeric:
            parts.append(int(numeric))
        else:
            break
    return tuple(parts)


def disable_incompatible_pandas_accelerators():
    numpy_version = _version_tuple("numpy")
    if not numpy_version or numpy_version[0] < 2:
        return

    incompatible_accelerators = {
        "numexpr": (2, 10),
        "bottleneck": (1, 4),
    }

    for package_name, minimum_version in incompatible_accelerators.items():
        package_version = _version_tuple(package_name)
        if package_version and package_version < minimum_version:
            sys.modules[package_name] = None
