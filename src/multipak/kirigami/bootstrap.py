"""Load MultiPak alongside the Qt bindings provided by its execution environment."""

import importlib.util
import sys
from pathlib import Path

package = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "multipak", package / "__init__.py", submodule_search_locations=[str(package)]
)
module = importlib.util.module_from_spec(spec)
sys.modules["multipak"] = module
spec.loader.exec_module(module)

from multipak.localization import tr  # noqa: E402

try:
    if "--manager" in sys.argv[1:]:
        sys.argv.remove("--manager")
        from multipak.kirigami.manager import main
    else:
        from multipak.kirigami.main import main
except ImportError as error:
    raise SystemExit(tr("MultiPak could not load its interface: {0}").format(error)) from error

raise SystemExit(main())
