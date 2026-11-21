import time

from src.char.BaseChar import BaseChar


class Sanhua(BaseChar):
    def do_perform(self):
        # 入场检测：如果协奏值已满，优先释放声骸技能并切换角色
        if self.is_con_full():
            self.click_echo()
            self.switch_next_char()
            return

        # 标记是否已释放共鸣解放（大招）
        liber_clicked = False
        # 标记是否已释放共鸣技能（小技能），用于避免重复补放
        skill_used = False
        # 默认重击蓄力时间
        sleep_time = 0.85
        # 短暂等待，确保状态稳定
        self.sleep(0.02)

        # 1. 优先释放共鸣技能（小技能）
        if self.resonance_available():
            self.click_resonance(send_click=False)
            skill_used = True
            # 等待共鸣技能动画
            self.sleep(0.1, False)
            # 增加额外延时，确保共鸣技能完全释放
            self.sleep(0.2, False)
            # 检测协奏值是否已满
            if self.is_con_full():
                self.click_echo()
                self.switch_next_char()
                return

        # 2. 释放共鸣解放（大招）
        if self.click_liberation(send_click=False):
            liber_clicked = True
            # 大招有动画，需要等待播放
            self.sleep(0.15, False)
            # 检测协奏值是否已满
            if self.is_con_full():
                self.click_echo()
                self.switch_next_char()
                return
            # 重置计时器，用于后续重击持续时间计算
            start = time.time()
        else:
            # 如果没有释放大招，也重置计时，以计算重击时间
            start = time.time()

        # 3. 长按鼠标进行重击（蓄力时间已缩短，不再额外增加）
        self.task.mouse_down()
        # 扣除因冻结/动画等消耗的时间，调整实际的按住鼠标持续时间
        sleep_time -= self.time_elapsed_accounting_for_freeze(start)
        self.logger.info('Sanhua to_sleep {}'.format(sleep_time))
        if sleep_time > 0:
            self.sleep(sleep_time, False)

        # 松开鼠标（结束重击）
        self.task.mouse_up()
        # 短暂停顿，确保动作衔接
        self.sleep(0.8)
        # 重击后检测协奏值是否已满
        if self.is_con_full():
            self.click_echo()
            self.switch_next_char()
            return

        # 如果释放了大招且之前没有释放小技能，则补放小技能（保证两个技能都释放）
        if liber_clicked and not skill_used:
            self.click_resonance(send_click=False)
            self.sleep(0.3)
            # 补放小技能后检测协奏值
            if self.is_con_full():
                self.click_echo()
                self.switch_next_char()
                return

        # 最终检测：如果协奏值已满，释放声骸技能
        if self.is_con_full():
            self.click_echo()

        # 切换至下一个角色
        self.switch_next_char()