import os
from pathlib import Path
import sys
import traceback

try:
    from aemeath_pet.app import main
    result = main()
except Exception:
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AemeathPet"
    if "--smoke-test" in sys.argv:
        folder = Path(sys.argv[sys.argv.index("--smoke-test")+1])
    folder.mkdir(parents=True, exist_ok=True)
    (folder/"error.log").write_text(traceback.format_exc(), encoding="utf-8")
    result = 1
raise SystemExit(result)
