import os, pytest
def pytest_collection_modifyitems(config, items):
    if os.path.isdir(os.path.join(os.getcwd(), "apps", "modal", "workers")):
        return
    skip = pytest.mark.skip(reason="monorepo shim test — runs only inside trunk")
    for item in items:
        if "shim" in item.name:
            item.add_marker(skip)
