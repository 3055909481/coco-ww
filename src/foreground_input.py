"""前台输入: 和上游 ok-ww v3.8.3 一样走 pynput (SendInput), 设置里「交互方式」选 Pynput 就用这个。

和默认的后台 PostMessage 比:
- 键鼠走系统真实输入流, 游戏看到的和物理键盘一样 (按键状态、Raw Input 都有), 不用再发 WM_ACTIVATE
- 只能在游戏窗口在前台时用: 切到别的窗口就暂停截图和操作; 会占用真实的键盘和鼠标
- 要管理员权限 (游戏本身是管理员运行的, 启动 coco-ww.bat 已经会提权)

类名就叫 Pynput: 设置界面直接显示这个名字, 存进配置的也是 'Pynput', 和上游一致。
coco-ww 多做的一件事: 每次自己按 F 都先告诉「按 F 开始战斗」的读键线程, 免得把拾取、处决按的 F 当成玩家按的。
"""
from ok.device.interaction_methods.pynput import PynputInteraction
from src.combat.FKeyStart import f_key_watcher


class Pynput(PynputInteraction):

    def send_key(self, key, down_time=0.01):
        f_key_watcher.note_self_key(key, down_time)
        super().send_key(key, down_time)

    def send_key_down(self, key, activate=True):
        # activate 只有 PostMessage 用得上; 收下这个参数, 写了 activate=False 的队伍代码换到前台也能跑
        f_key_watcher.note_self_key(key)
        super().send_key_down(key)

    def send_key_up(self, key):
        try:
            super().send_key_up(key)
        finally:
            f_key_watcher.note_self_key_up(key)
