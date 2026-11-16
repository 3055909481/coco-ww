import time

from src.char.BaseChar import BaseChar, SwitchPriority

HIYUKI_NAME = 'char_hiyuki'
RETURN_TIMEOUT = 2.0
RETURN_KEY_INTERVAL = 0.12


def return_to_hiyuki(char):
    """凑数队友被游戏换上场(一般是绯雪倒了)时, 按数字键切回绯雪。

    不走 switch_next_char(): 那里会补普攻、按处决。切过去之后补上框架那套交接记账,
    否则没人的 is_current_char 是 True。Returns: 切回去没有。
    """
    task = char.task
    hiyuki = next((c for c in task.chars if c is not None and c is not char and c.char_name == HIYUKI_NAME), None)
    if hiyuki is None:
        char.logger.warning(f'{char} on field but hiyuki not in team: '
                            f'{[None if c is None else c.char_name for c in task.chars]}')
        return False
    start = time.time()
    while time.time() - start < RETURN_TIMEOUT:
        in_team, current_index, _ = task.in_team()
        if in_team and current_index == hiyuki.index:
            char.switch_out(con_full=False)
            hiyuki.is_current_char = True
            hiyuki.has_intro = False
            hiyuki.last_switch_in_time = time.time()
            char.logger.info(f'{char} back to hiyuki in {time.time() - start:.2f}s')
            return True
        task.send_key(hiyuki.index + 1)
        char.sleep(RETURN_KEY_INTERVAL)
    char.logger.warning(f'{char} could not switch back to hiyuki (down?)')
    return False


class Chixia(BaseChar):
    """绯雪单人轴里的凑数队友: 从不主动上场。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bullets = 0

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        return SwitchPriority.NO

    def do_perform(self):
        if not return_to_hiyuki(self):
            self.continues_normal_attack(0.5)
