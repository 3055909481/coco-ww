"""coco-ww 不走 PyAppify 启动器, 也不跟上游自动更新。

ok 框架在几处直接 `import pyappify`, 这里只留它要读的那几个名字。框架对其余函数
(get_version_list / create_shortcut / update_to_version ...) 都是先 getattr 再用,
没有就自动关掉对应功能: 关于页不出「应用更新」卡片, 也不建快捷方式。
"""

app_version = None
app_starting_version = None
app_profile = None
app_ref = None
update_note = None
pyappify_version = None
pyappify_upgradeable = False
pyappify_executable = None
logger = None


def hide_pyappify():
    return None


def kill_pyappify():
    return None
