from pathlib import Path
from packer import _decode_scr
from utils_tools.libs import translate_lib


files = translate_lib.collect_files("patch")

for file in files:
    file_path = Path(file)
    decoded = _decode_scr(file_path.read_bytes())
    Path(f"asmed/{file_path.name}").write_bytes(decoded)