import os

from er.core import text_hook
from er.core import config
from er.core.config import FEATURES
from er.core.gal_json import GalJson
from er.core.pipeline import packer, scrpiler, textract
from er.processor.mapping import ReplacementPoolBuilder
from er.utils import fs
from er.utils.console import console


def extract() -> None:
    """提取(extract)相关逻辑"""
    console.print("执行提取...", style="info")

    packer.unpack("workspace/Rio.arc", "workspace/script")
    packer.decode_patch_files("workspace/patch", "workspace/script")

    scrpiler.decompile("workspace/script", "workspace/raw")

    gal_json = GalJson()
    textract.extract("workspace/raw", gal_json)

    (
        gal_json.apply_remove_fullwidth_spaces()
        .apply_transform(lambda s: s.replace("\\n", ""))
        .apply_current_to_raw_fields()
        .apply_add_tags()
        .save_to_path("workspace/raw.json")
    )

    console.print("提取完成", style="info")


def replace(check: bool = True) -> None:
    """替换(replace)相关逻辑"""
    console.print("执行替换...", style="info")

    gal_json = GalJson.load_from_path("workspace/translated.json")
    gal_json.apply_remove_tags()

    if check:
        (
            gal_json.check_korean_characters()
            .check_japanese_characters()
            .check_duplicate_quotes()
            .check_length_discrepancy()
            .check_quote_consistency()
            .check_invisible_characters()
            .check_forbidden_words()
            .check_unpaired_quotes()
            .check_max_text_len(28 * 4)
            .ok_or_print_error_and_exit()
        )

    (
        gal_json.apply_restore_whitespace()
        .apply_replace_rare_characters()
        .apply_replace_nested_brackets()
        .apply_replace_quotation_marks()
        .apply_map_gbk_unsupported_chars()
    )

    pool = ReplacementPoolBuilder().exclude_from_gal_text(gal_json).build()
    gal_json.apply_mapping(pool)
    pool.save_mapping_to_path("workspace/generated/mapping.json")

    textract.apply("workspace/raw", gal_json, "workspace/generated/translated")

    scrpiler.compile("workspace/generated/translated", "workspace/generated/script")

    packer.pack("workspace/generated/script", "workspace/generated/dist/Split_chs.pak")

    fs.merge_dir("assets/dist_extra", "workspace/generated/dist")
    config.generate_config_files()

    text_hook.TextHookBuilder(os.environ["TEXT_HOOK_PROJECT_PATH"]).build(
        FEATURES, panic="immediate-abort"
    )

    console.print("替换完成", style="info")
