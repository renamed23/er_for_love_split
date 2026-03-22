from er.utils import misc


CONFIG = {
    "FONT_FACE": "SimHei",  # (ＭＳ ゴシック, SimHei, SimSun)
    "CHAR_SET": 134,  # CP932=128, GBK=134
    "FONT_FILTER": ["Microsoft YaHei", "Microsoft YaHei UI"],
    "WINDOW_TITLE": "Love Split ——第五个季节——",
    "REDIRECTION_SRC_PATH": "Rio.arc",
    "REDIRECTION_TARGET_PATH": "Split_chs.pak",
}

HOOK_LISTS = {
    "enable": [],
    "disable": [],
}


FEATURES = [
    "default_impl",
    "bind_text_mapping",
    "bind_font_manager",
    "enable_iat_hook",
    "bind_path_redirector",
    "bind_window_title_overrider",
    "enable_window_title_override",
]


def generate_config_files() -> None:
    """生成配置文件"""
    misc.write_json("workspace/generated/config.json", CONFIG)
    misc.write_json("workspace/generated/hook_lists.json", HOOK_LISTS)
