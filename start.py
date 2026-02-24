#!/usr/bin/env python3

import os
from pathlib import Path

from utils_tools.libs import translate_lib

config = {
    "FONT_FACE": "SimHei",  # (ＭＳ ゴシック, SimHei, SimSun)
    "CHAR_SET": 134,  # CP932=128, GBK=134
    "FONT_FILTER": ["Microsoft YaHei", "Microsoft YaHei UI"],
    "WINDOW_TITLE": "Love Split ——第五个季节——",
    "REDIRECTION_SRC_PATH": "Rio.arc",
    "REDIRECTION_TARGET_PATH": "Split_chs.pak",
}

hook_lists = {
    "enable": [],
    "disable": [],
}


features = [
    "default_impl",
    "text_hook",
    "iat_hook",
    "create_file_redirect",
    "override_window_title"
]

PACKER = "python packer.py"
ASMER = "python ops.py"

ER = [
    (
        "python er.py extract --path raw --output raw.json",
        "python er.py replace --path raw --text generated/translated.json",
    )
]


def extract():
    print("执行提取...")
    translate_lib.system(
        f"{PACKER} unpack -i Rio.arc -o asmed")
    translate_lib.system("python decode_patch_files.py")
    translate_lib.system(
        f"{ASMER} disasm asmed raw")
    translate_lib.extract_and_concat(ER)
    translate_lib.json_process("e", "raw.json")


def replace():
    print("执行替换...")
    Path("generated/dist").mkdir(parents=True, exist_ok=True)

    # 你的 replace 逻辑
    translate_lib.generate_json(config, "config.json")
    translate_lib.generate_json(hook_lists, "hook_lists.json")
    translate_lib.copy_path(
        "translated.json", "generated/translated.json", overwrite=True
    )
    translate_lib.copy_path("raw.json", "generated/raw.json", overwrite=True)
    translate_lib.json_check()
    translate_lib.json_process("r", "generated/translated.json")
    # translate_lib.ascii_to_fullwidth()
    translate_lib.replace("cp932", filter_rare=False)  # cp932,shift_jis,gbk

    translate_lib.split_and_replace(ER)

    translate_lib.copy_path(
        "translated", "generated/translated", overwrite=True)

    translate_lib.system(f"{ASMER} asm generated/translated generated/asmed")

    translate_lib.system(
        f"{PACKER} pack -i generated/asmed -o generated/dist/Split_chs.pak"
    )

    translate_lib.merge_directories(
        "assets/dist_pass", "generated/dist", overwrite=True
    )

    translate_lib.TextHookBuilder(os.environ["TEXT_HOOK_PROJECT_PATH"]).build(
        features, panic="immediate-abort"
    )


def main():
    translate_lib.create_cli(extract, replace)()


if __name__ == "__main__":
    main()
