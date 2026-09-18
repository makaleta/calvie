"""Keep tests independent of the developer's calendar configuration."""
import configparser
from unittest.mock import patch

import pytest

# main reads configuration during import; never read the user's config in tests.
with patch.object(configparser.ConfigParser, "read"):
    import main


@pytest.fixture(autouse=True)
def isolated_config():
    config = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
    config.read_dict({"DEFAULT": dict(main.CONFIG.defaults())})
    # Existing tests import CONFIG directly, so preserve its identity.
    original = {name: dict(main.CONFIG[name]) for name in main.CONFIG.sections()}
    defaults = dict(main.CONFIG.defaults())
    main.CONFIG.clear()
    main.CONFIG["DEFAULT"] = dict(config.defaults())
    yield main.CONFIG
    main.CONFIG.clear()
    main.CONFIG["DEFAULT"] = defaults
    main.CONFIG.read_dict(original)
