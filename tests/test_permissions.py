import pytest

from minicodeagent.permissions import (
    PermissionConfig,
    PermissionController,
    PermissionDenied,
)


def test_blocks_non_allowlisted_tool():
    permissions = PermissionController(PermissionConfig(allowed_tools={"read_file"}))

    with pytest.raises(PermissionDenied):
        permissions.check_tool("write_file")


def test_write_requires_allow_write_or_dry_run():
    permissions = PermissionController(PermissionConfig(allowed_tools={"write_file"}))

    with pytest.raises(PermissionDenied):
        permissions.check_tool("write_file", mutates_files=True)


def test_blocks_unsafe_python():
    permissions = PermissionController(PermissionConfig(allowed_tools={"run_python"}))

    with pytest.raises(PermissionDenied):
        permissions.check_python("import os\nprint(os.getcwd())")
