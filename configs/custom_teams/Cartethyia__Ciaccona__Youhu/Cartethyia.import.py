# -*- coding: utf-8 -*-
"""
卡提希娅 + 夏空 5R10下7EE v1.9.1 等待补刀优化版
小卡段：R切小卡 -> 缓冲 -> A4（段内回声键）-> 缓冲 -> 完整重击 -> 跳 -> A 0.50s -> R切回。
v1.9.1：R1 等待普攻补能，E 转好后优先 E 接下落；R2 等待加入短普攻填充，触发后立即停手。
本版基于用户原始 ZIP，修复首轮及循环轮共用的小卡段；不插入闪避。
针对用户录像与日志问题全面修复：
1. 夏空 R 释放：移除过早 available 误判门禁，进场直接 fire_liberation 并等待演出，彻底根治“冷却状态下无法切换角色”与夏空漏大问题。
2. 演出恢复 wait_hud_back：min_gone 改为 0.5s，连续2帧恢复即放行，杜绝变身卡死 3.5~4.6 秒。
3. 大卡 R2 终结技：移除死板模板等待，a5/双E 后直接送 R 键并确认，杜绝 3.2s 平 A 补能发愣。
4. 次轮与循环轮节奏：严格对齐视频
   - 启动轮：小卡 e -> R1 -> EE -> 夏r切大卡 -> a5 -> ra4z闪跳a三剑 -> ra5R2 -> e延夏 -> 夏空充能 -> a4闪跳a三剑 -> a4ezR1
   - 次轮：大卡 EE -> 夏r切大卡 -> a5 -> r跳a三剑 -> a4ze打三剑 -> ra5R2 -> a4延夏 -> 夏空充能 -> 空中e打三剑 -> R1
   - 循环轮：大卡 EE -> 夏r切大卡 -> ra4ze打三剑 -> ra5(EE)R2 -> a4延夏 -> 夏空充能 -> 空中e打三剑 -> R1
5. 第5轮收尾：打满 5 轮后转入终结清场模式（E技能 + 重击 + 普攻连续收割），绝不重复调用半截第5轮。
"""
import ctypes
import os
import threading
import time
import cv2
import numpy as np

from src.char.BaseChar import SwitchPriority
from src.char.Cartethyia import Cartethyia as NativeCartethyia


# ==================== 物理 F 键监听 ====================
F_VK = 0x46
F_POLL_INTERVAL = 0.05
F_LOG_DEBOUNCE = 1.0
F_TASK_NAME = 'FStartCombatTask'
F_NEW_SESSION_GAP = 50.0


def f_settings(task):
    executor = getattr(task, 'executor', None)
    for group in ('trigger_tasks', 'onetime_tasks'):
        for other in getattr(executor, group, None) or ():
            if type(other).__name__ == F_TASK_NAME:
                try:
                    return bool(other.enabled), float(getattr(other, 'f_start_delay', 0) or 0)
                except Exception:
                    return True, 0.0
    return True, 0.0


def start_f_watch(task, logger=None):
    if getattr(task, '_f_watch_started', False):
        return
    task._f_watch_started = True
    if not hasattr(task, '_f_last_press'):
        task._f_last_press = -1
    if not hasattr(task, '_opener_last_time'):
        task._opener_last_time = -1

    def watch():
        try:
            user32 = ctypes.windll.user32
            user32.GetAsyncKeyState.restype = ctypes.c_short
        except Exception as e:
            task._f_watch_started = False
            if logger:
                logger.error(f'f watch: GetAsyncKeyState unavailable ({e})')
            return
        last_log = 0
        was_down = False
        while True:
            try:
                down = bool(user32.GetAsyncKeyState(F_VK) & 0x8000)
                if down and not was_down:
                    now = time.time()
                    last_opener = getattr(task, '_opener_last_time', -1)
                    is_new_session = (last_opener < 0 or now - last_opener > F_NEW_SESSION_GAP)
                    if is_new_session:
                        task._f_last_press = now
                        if logger and now - last_log >= F_LOG_DEBOUNCE:
                            last_log = now
                            logger.info('[F键监控] 检测到新战斗物理 F 键按下，启动轴已重新武装！')
                    else:
                        if logger and now - last_log >= F_LOG_DEBOUNCE:
                            last_log = now
                            logger.info('[F键监控] 战斗中物理 F 键按下，记录重开时间戳')
                was_down = down
                time.sleep(F_POLL_INTERVAL)
            except Exception:
                return

    threading.Thread(target=watch, name='ok_ww_kax_f_watch', daemon=True).start()


# ==================== 任务开关监听 ====================
ENABLE_POLL_INTERVAL = 0.2


def start_enable_watch(task, logger=None):
    if getattr(task, '_ww_enable_watch_started', False):
        return
    task._ww_enable_watch_started = True
    if not hasattr(task, '_ww_task_restarted_at'):
        task._ww_task_restarted_at = -1

    def watch():
        last_state = getattr(task, 'enabled', True)
        while True:
            try:
                now_state = getattr(task, 'enabled', True)
                if (not last_state) and now_state:
                    task._ww_task_restarted_at = time.time()
                    if logger:
                        logger.info('[重启监控] 检测到自动战斗重新开启(False->True) -> 启动轴已重新武装并重置为第1轮！')
                last_state = now_state
                time.sleep(ENABLE_POLL_INTERVAL)
            except Exception:
                return

    threading.Thread(target=watch, name='ok_ww_kax_enable_watch', daemon=True).start()


class CycleAbort(Exception):
    pass


class Cartethyia(NativeCartethyia):

    # 时间轴核心参数
    OPENER_MIN_GAP = 12.0
    OPENER_START_DELAY = 0.25       # 开局进战缓冲延迟
    A_INTERVAL = 0.12
    MIN_KEY_HOLD = 0.04

    # 用户手打首段左键长按约 1.013s。剑标记采用原生角色类相同的模板。
    # 手打反馈：R 切小卡后约 0.67s 按 Q，小卡普攻输入持续约 2.54s，
    # 再长按重击约 1.01s。这里保留少量输入余量，但不再让剑图标识别
    # 决定是否执行后续跳跃；该图标在特效遮挡下会出现稳定漏检。
    SMALL_A4_DURATION = 2.55
    SMALL_A4_ECHO_AT = 0.67
    SMALL_SWITCH_TO_A4_GAP = 0.18
    SMALL_A4_ECHO_RECOVERY_GAP = 0.16
    SMALL_A4_TO_HEAVY_GAP = 0.20
    SMALL_HEAVY_HOLD = 1.02
    SMALL_HEAVY_TO_JUMP_GAP = 0.05
    SMALL_PLUNGE_LAND_SETTLE = 0.30

    # 夏空协奏切回小卡后的变奏 A4。反馈局中切人松键后仅 0.024s 即开始 A，
    # 且 2.50s 时就起跳，第四段普攻尚未完整结束。
    CART_INTRO_SETTLE = 0.20
    CART_INTRO_A4_DURATION = 2.80
    CART_INTRO_A4_TO_JUMP_GAP = 0.18
    CART_INTRO_E_RELEASE_MAX = 0.80
    CART_INTRO_E_CAST_SETTLE = 0.60

    # 宇哥哥“卡夏千”轴的夏空回路输入结构。
    CIAC_INTRO_SETTLE = 0.90
    CIAC_PRE_L1_HOLD = 0.05
    CIAC_PRE_L1_GAP = 0.45
    CIAC_PRE_L2_HOLD = 0.05
    CIAC_PRE_L2_GAP = 0.15
    CIAC_L1_HOLD = 0.06
    CIAC_L1_GAP = 0.10
    CIAC_L2_HOLD = 0.06
    CIAC_FALL_TO_LAND = 0.55
    CIAC_LAND_A_HOLD = 0.078
    CIAC_LAND_A_GAP = 0.05
    CIAC_RB_HOLD = 0.05
    CIAC_HEAVY_HOLD = 1.10
    CIAC_CONCERTO_COMP_CAP = 3.50
    CIAC_LAND_SETTLE = 0.20
    CIAC_A4_DURATION = 1.45

    # 卡夏千轴的变身补偿上限与 A→R 重试间隔。
    TRANSFORM_COMP_CAP = 8.0
    TRANSFORM_A_GAP = 0.15
    TRANSFORM_R_WATCH = 0.35
    R2_FILL_ATTACK_DELAY = 0.22
    R2_FILL_ATTACK_INTERVAL = 0.25
    TRANSFORM_POST_HUD_EE_WINDOW = 0.78
    TRANSFORM_POST_HUD_EE = TRANSFORM_POST_HUD_EE_WINDOW
    TRANSFORM_LAND_SETTLE = 0.20

    # 技能动画时间常数
    CART_TRANSFORM_ANIM = 3.5      # R1 变身镜头
    CIAC_R_ANIM = 3.6              # 夏空 R 停时大招镜头
    FLEUR_LIB_ANIM = 2.0           # 大卡 R2 万阙垂天动画
    ANIM_GONE_GRACE = 1.0          # 若大招未触发镜头，宽限放行时间

    ENABLE_7EE = True              # 7EE 模式开关

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opener_armed = True
        self.last_opener = -1
        self.macro_t0 = 0.0
        self.macro_marks = []
        self.cur = None
        self.held = set()
        self.current_round = 1
        self._ignored_combat_lost = 0
        self._comp_count = 0

    def reset_state(self):
        super().reset_state()
        self.opener_armed = True
        self.last_opener = -1
        self.current_round = 1
        self.cur = None
        self.held.clear()
        self._ignored_combat_lost = 0
        self._comp_count = 0
        start_f_watch(self.task, self.logger)
        start_enable_watch(self.task, self.logger)

    def on_combat_end(self, chars=None):
        super().on_combat_end(chars) if chars else super().on_combat_end()
        self.opener_armed = True
        self.current_round = 1
        self.last_opener = -1
        self.release_all()

    # ==================== 队伍与角色识别 ====================
    @property
    def is_in_kxq_cycle(self) -> bool:
        if hasattr(self.task, 'chars') and self.task.chars:
            for char in self.task.chars:
                if char is not None and getattr(char, 'name', '') == 'Ciaccona':
                    return True
        return True

    def teammate(self, name):
        for char in self.task.chars:
            if char is not None and char.name == name:
                return char
        return None

    def char_by_token(self):
        ciac = None
        other = None
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if getattr(char, 'name', '') == 'Ciaccona':
                ciac = char
            else:
                other = char
        return {
            '卡': self, '夏': ciac, '千': other, '釉': other,
            'Cartethyia': self, 'Ciaccona': ciac,
            'Chisa': other, 'Youhu': other
        }

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if self.is_in_kxq_cycle:
            return SwitchPriority.MUST if self.opener_pending() else SwitchPriority.HIGH
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    # ==================== 重启与启动轴判定 ====================
    def opener_pending(self):
        return self.opener_blocked_by() is None

    def opener_blocked_by(self):
        if not self.is_in_kxq_cycle:
            return '队伍不包含夏空(Ciaccona)'

        restarted = getattr(self.task, '_ww_task_restarted_at', -1)
        if restarted > self.last_opener:
            if not self.opener_armed:
                self.logger.info('[重启检测] 自动战斗开关重启 -> 启动轴重新武装，轮次重置为第1轮！')
            self.opener_armed = True
            self.current_round = 1
            return None

        f_press = getattr(self.task, '_f_last_press', -1)
        if f_press > self.last_opener:
            self.opener_armed = True
            self.current_round = 1
            return None

        if not self.opener_armed:
            return f'已在循环战斗中 (当前轮次: 第 {self.current_round} 轮)'
        if self.last_opener >= 0 and time.time() - self.last_opener < self.OPENER_MIN_GAP:
            return f'距离上次启动轴执行不足 {self.OPENER_MIN_GAP:.0f}s'
        if not getattr(self.task, 'use_liberation', True):
            return '任务共鸣解放(大招)已被手动关闭'
        return None

    # ==================== 矩阵脱战与安全操作屏蔽层 ====================
    @staticmethod
    def _is_not_in_combat(exc):
        msg = str(exc)
        return (
            'not in combat' in msg
            or 'Combat lost target' in msg
            or 'target lost' in msg
            or 'not in_team' in msg
        )

    def safe_next_frame(self):
        try:
            return self.task.next_frame()
        except Exception as e:
            if self._is_not_in_combat(e):
                self._ignored_combat_lost += 1
                return getattr(self.task, 'frame', None)
            raise

    def safe_click(self):
        try:
            self.task.click()
        except Exception as e:
            if self._is_not_in_combat(e):
                self._ignored_combat_lost += 1
                return
            raise

    def safe_in_team(self):
        try:
            res = self.task.in_team()
            return res[0] if isinstance(res, tuple) else bool(res)
        except Exception as e:
            if self._is_not_in_combat(e):
                self._ignored_combat_lost += 1
                return True
            raise

    def check_enabled(self):
        try:
            if hasattr(self.task, 'executor') and hasattr(self.task.executor, 'check_enabled'):
                self.task.executor.check_enabled(check_pause=False)
        except Exception as e:
            if self._is_not_in_combat(e):
                self._ignored_combat_lost += 1
                return
            raise

    # ==================== HUD 演出感知原语 ====================
    def wait_hud_back(self, tag='演出', min_gone=0.35, time_out=4.5, initial_gone_at=None):
        """
        动态感知 HUD 消失与恢复：
        等待特写/大招演出 HUD 消失后恢复（连续 2 帧恢复放行），
        避免大招特写镜头演出期间提前切人失败或卡死！
        """
        start = time.time()
        gone_at = initial_gone_at
        back_hits = 0
        back = False
        while time.time() - start < time_out:
            self.safe_next_frame()
            in_team = self.safe_in_team()
            if not in_team:
                if gone_at is None:
                    gone_at = time.time()
                back_hits = 0
            elif gone_at is not None:
                # 只有当 HUD 消失时间已经 >= min_gone 时才开始确认恢复
                if time.time() - gone_at >= min_gone:
                    back_hits += 1
                    if back_hits >= 2:
                        back = True
                        break
            elif time.time() - start >= 0.5:
                # 若 0.5s 内未消失说明无特写或极快，直接放行
                back = True
                break
            time.sleep(0.015)
            self.check_enabled()
        duration = time.time() - start
        self.macro_mark(f'{tag}({"已恢复" if back else "超时"},{duration:.2f}s)')
        return back, duration

    def wait_anim(self, tag, time_out=2.5, gone_grace=0.4):
        return self.wait_hud_back(tag=tag, min_gone=gone_grace, time_out=time_out)

    # ==================== 目标锁定 Helper ====================
    def safe_relock_target(self, tag='索敌锁定', settle=0.04):
        """
        形态切换与变身后的索敌目标重锁定保护。
        复用 OK-WW 已有 self.task.middle_click 接口，配合异常防护与短稳定等待。
        """
        self.macro_mark(f'{tag}_开始')
        try:
            if hasattr(self.task, 'middle_click'):
                self.task.middle_click()
            elif hasattr(self, 'middle_click'):
                self.middle_click()
            self.macro_mark(f'{tag}_成功')
        except Exception as e:
            self.macro_mark(f'{tag}_异常({e})')
            if hasattr(self, 'logger') and self.logger:
                self.logger.warning(f'[{tag}] 目标锁定执行异常: {e}')
        if settle > 0:
            self.macro_wait(settle)

    # ==================== 定时与安全打断 ====================
    def sleep_until(self, target):
        while True:
            left = target - time.time()
            if left <= 0:
                return

            self.check_enabled()

            restarted = getattr(self.task, '_ww_task_restarted_at', -1)
            if restarted > self.macro_t0 + 0.1:
                raise CycleAbort('打断: 自动战斗在宏执行期间被重新开启')

            f_press = getattr(self.task, '_f_last_press', -1)
            if f_press > self.macro_t0 + 0.5:
                raise CycleAbort('物理 F 打断: 检测到新战斗的实体 F 重开')

            if left > 0.05:
                time.sleep(min(0.04, left - 0.01))
            else:
                time.sleep(left)

    def macro_wait(self, sec):
        self.sleep_until(time.time() + max(0.0, sec))

    def key_of(self, token):
        if token == 'E':
            return self.get_resonance_key()
        if token == 'Q':
            return self.get_echo_key()
        if token == 'R':
            return self.get_liberation_key()
        if token == 'SP':
            return self.task.key_config.get('Jump Key', 'space')
        return None

    def press_down(self, token, char_by_token):
        if token == 'L':
            self.task.mouse_down()
        elif token == 'RB':
            self.task.mouse_down(key='right')
        elif token in char_by_token:
            target = char_by_token.get(token)
            if target is not None:
                self.task.send_key_down(str(target.index + 1))
                if target is not self.cur:
                    self.handoff(self.cur if self.cur is not None else self, target)
        else:
            k = self.key_of(token)
            if k:
                self.task.send_key_down(k)
        self.held.add(token)

    def press_up(self, token, char_by_token):
        if token == 'L':
            self.task.mouse_up()
        elif token == 'RB':
            self.task.mouse_up(key='right')
        elif token in char_by_token:
            target = char_by_token.get(token)
            if target is not None:
                self.task.send_key_up(str(target.index + 1))
        else:
            k = self.key_of(token)
            if k:
                self.task.send_key_up(k)
        self.held.discard(token)

    def release_all(self):
        chars = self.char_by_token()
        for token in list(self.held):
            try:
                self.press_up(token, chars)
            except Exception:
                pass
        self.held.clear()

    def handoff(self, from_char, to_char):
        if from_char is None:
            from_char = self
        self.task.in_liberation = False
        try:
            from_char.switch_out(con_full=False)
        except Exception:
            pass
        to_char.is_current_char = True
        to_char.has_intro = False
        to_char.has_sub_dps_intro = False
        to_char.last_switch_in_time = time.time()
        self.cur = to_char

    def hand_to_cart(self, sender):
        self.cur = self
        self.is_current_char = True
        if sender is not None:
            sender.is_current_char = False
        key = str(self.index + 1)
        for _ in range(3):
            self.task.send_key_down(key)
            self.macro_wait(0.04)
            self.task.send_key_up(key)
            self.macro_wait(0.05)
        return True

    # ==================== 角色切换与重试机制 ====================
    def switch_to_char(self, target_or_token, tag='切人', retries=2):
        """
        统一稳健切人方法：
        支持传入 BaseChar 对象或标识符 ('卡', '夏', '千', '釉', 'Cartethyia', 'Ciaccona', 'Youhu', 'Chisa')
        """
        target = None
        chars = self.char_by_token()
        if isinstance(target_or_token, str):
            target = chars.get(target_or_token)
            if target is None:
                for c in getattr(self.task, 'chars', []) or []:
                    if c is not None and (getattr(c, 'name', '') == target_or_token or getattr(c, 'token', '') == target_or_token):
                        target = c
                        break
        else:
            target = target_or_token

        if target is None:
            self.logger.warning(f'[{tag}] 未找到目标角色: {target_or_token}')
            return False

        if target is self.cur and getattr(target, 'is_current_char', False):
            return True

        key = str(target.index + 1)
        self.macro_mark(f'{tag}_{target.name}_按{key}')

        for attempt in range(max(1, retries)):
            self.task.send_key_down(key)
            self.macro_wait(0.04)
            self.task.send_key_up(key)
            self.handoff(self.cur if self.cur is not None else self, target)
            self.macro_wait(0.08)

        return True

    def switch_retry(self, target_or_token, tag='切人', retries=2):
        """兼容别名，统一调用 switch_to_char"""
        return self.switch_to_char(target_or_token, tag=tag, retries=retries)

    # ==================== 时间轴标记 ====================
    def macro_start(self):
        self.macro_t0 = time.time()
        self.macro_marks = []

    def macro_mark(self, name):
        self.macro_marks.append(f'{name}@{time.time() - self.macro_t0:.2f}')

    def macro_timeline(self):
        return ' '.join(self.macro_marks)


    # ==================== 官方变身时长与 E 直达 CD 机制 ====================
    CART_TRANSFORM_ANIM = 3.40  # 官方小卡变大卡标准动画时长 (3.40s ~ 3.55s)
    O_R1_TO_SWORD = 3.55        # 官方变身到出招判定时长 (3.55s)

    def is_resonance_on_cd(self):
        """统一检测当前角色 E 技能是否已进入 CD 状态"""
        try:
            if hasattr(self, 'has_cd') and self.has_cd('resonance'):
                return True
            if hasattr(self.task, 'has_cd') and self.task.has_cd('resonance'):
                return True
            if hasattr(self.task, 'get_cd') and self.task.get_cd('resonance') > 0.2:
                return True
            if hasattr(self, 'resonance_available') and not self.resonance_available():
                return True
            if hasattr(self.task, 'available') and not self.task.available('resonance', check_color=False):
                return True
        except Exception:
            pass
        return False

    def is_small_cart_r_ready(self):
        """
        精准检测小卡 R 技能是否已经充能完毕且可用：
        1. 原生 liberation_available 判定 (检测 box_liberation 高亮金色/白色能量圈且无 CD)
        2. 原生 task.available('liberation')
        3. YOLO / 特征检测 (如 lib_cartethyia_big 亮起)
        """
        try:
            if hasattr(self, 'liberation_available') and self.liberation_available():
                return True
        except Exception:
            pass

        try:
            if hasattr(self.task, 'available') and self.task.available('liberation'):
                return True
        except Exception:
            pass

        try:
            if hasattr(self, 'is_lib_big_available') and self.is_lib_big_available():
                return True
        except Exception:
            pass

        return False

    def ensure_resonance_released(self, tag='点按E', max_wait=1.2):
        """
        尝试点击释放 E，直到 E 成功释放（进入 CD 状态）：
        以 0.10s 间隔连续点按 E，并在每次点击后读屏检测 E 是否已进入 CD；
        一旦检测到 E 进入 CD，立即判定释放成功并退出，绝不再单按被吞！
        """
        self.macro_mark(f'{tag}_开始')
        e_key = self.key_of('E') or 'e'

        start = time.time()
        e_released = False

        while time.time() - start < max_wait:
            self.task.send_key_down(e_key)
            self.macro_wait(0.05)
            self.task.send_key_up(e_key)

            self.safe_next_frame()
            if self.is_resonance_on_cd():
                e_released = True
                self.macro_mark(f'{tag}_检测到E成功释放(耗时{time.time() - start:.2f}s)')
                break

            self.macro_wait(0.08)

        if not e_released:
            self.macro_mark(f'{tag}_超时放行')
        return e_released

    def ensure_small_cart_r_ready_with_attacks(self, tag='小卡R充能保底', max_wait=3.5):
        """
        小卡 ER 连招充能保底机制（用户实操反馈专项加固）：
        1. 优先检测 E 是否真正进入 CD，若未进 CD 说明上一步 E 没放出来，优先尝试点击补放 E，绝不盲目浪费时间普攻！
        2. 确认 E 成功释放后，若 R 充能仍差一丝，只持续普攻补能；
           本方法不再提前按 R，避免变身已触发后又被下一阶段重复按键。
        """
        self.safe_next_frame()

        # 0. 用户核心痛点解决：若检测到 E 根本没进 CD（说明前序单点被重击后摇吞了），绝不盲目普攻，优先尝试点击释放 E！
        if not self.is_resonance_on_cd():
            self.logger.info(f'[{tag}] 检测到小卡 E 尚未进入 CD，优先尝试连点释放 E！')
            self.ensure_resonance_released(tag=f'{tag}_补按E')
            self.macro_wait(0.30)
            self.safe_next_frame()

        if self.is_small_cart_r_ready():
            return True

        self.logger.info(f'[{tag}] 小卡 R 充能尚未打满，启动普攻连打补能保底！')
        self.macro_mark(f'{tag}_开始补能')

        start_fill = time.time()

        while time.time() - start_fill < max_wait:
            self.safe_next_frame()

            # 1. 优先检测 R 技能是否已经充满高亮
            if self.is_small_cart_r_ready():
                self.macro_mark(f'{tag}_R充能已满(耗时{time.time() - start_fill:.2f}s)')
                return True

            # 只用普攻补能，不在“检查是否就绪”阶段偷按 R；R 的释放与
            # HUD 消失确认统一交给 cast_r_and_press_e_until_cd。
            self.small_cart_tap('L', hold=0.035, gap=0.110)

            self.check_enabled()

        self.macro_mark(f'{tag}_补能结束')
        self.safe_next_frame()
        return self.is_small_cart_r_ready()

    def cast_r_and_press_e_until_cd(self, tag='R变身EE_直达CD', max_wait=6.5):
        """
        R1 变大卡并完整打完双 E（手打录制 2.894s 结构对齐）：
        1. 按下 R 变身大卡 (结合 HUD 消失检测)；
        2. 变身期间持续以 0.08s 间隔连点 E (手打录制连点 13~16 次)；
        3. HUD 恢复后继续连点 E 1.00s，确保第二段 E 完整触发；
        4. 等待角色落地并稳定 0.20s，才允许进入后续切人流程。
        """
        self.macro_mark(f'{tag}_开始')

        # 1. 持续尝试 R，必须确认 HUD 消失后才算真正触发变身。
        # R 未触发时先看 E：E 可用就优先补放并利用腾空窗口接下落；
        # E 已进入 CD 才用普攻补能。每轮都会重查 CD，因此普攻期间 E
        # 一旦转好，下一轮就会立即切回 E + 下落攻击。
        start_r = time.time()
        transformed = False
        r_attempts = 0
        e_fill_attempts = 0
        attack_fills = 0
        while time.time() - start_r < self.TRANSFORM_COMP_CAP:
            r_attempts += 1
            self.small_cart_tap('R', hold=0.04)

            watch_end = time.time() + self.TRANSFORM_R_WATCH
            while time.time() < watch_end:
                self.safe_next_frame()
                if not self.safe_in_team():
                    transformed = True
                    break
                self.macro_wait(0.02)
            if transformed:
                self.macro_mark(
                    f'{tag}_R变身成功触发(HUD消失,R尝试x{r_attempts},耗时{time.time() - start_r:.2f}s)'
                )
                break

            if not self.is_resonance_on_cd():
                e_fill_attempts += 1
                self.macro_mark(f'{tag}_R1前优先E下落_x{e_fill_attempts}')
                e_released = self.ensure_resonance_released(
                    tag=f'{tag}_R1前补E{e_fill_attempts}',
                    max_wait=0.45,
                )
                if e_released:
                    # E 自带腾空，不额外按跳跃；短暂等待起身后连点左键引导
                    # 下落，并确认落地再回到 R 检查循环。
                    self.macro_wait(0.12)
                    self.rapid_attack_clicks(
                        duration=0.50,
                        tag=f'{tag}_R1前E下落{e_fill_attempts}',
                        clicks_per_sec=7.0,
                    )
                    self.wait_down(click=False)
                    self.macro_wait(self.SMALL_PLUNGE_LAND_SETTLE)
                    self.macro_mark(f'{tag}_R1前E下落{e_fill_attempts}_落地确认')
            else:
                attack_fills += 1
                self.safe_click()
                self.macro_mark(f'{tag}_E冷却普攻补刀x{attack_fills}')
            self.macro_wait(self.TRANSFORM_A_GAP)

        if not transformed:
            self.macro_mark(
                f'{tag}_R变身补偿超时({self.TRANSFORM_COMP_CAP:.1f}s,'
                f'R尝试x{r_attempts},补Ex{e_fill_attempts},普攻x{attack_fills})'
            )
            raise CycleAbort(f'[{tag}] R变身补偿超时，未确认HUD消失')

        start_time = time.time()
        last_e = 0.0
        n_e = 0
        hud_back_hits = 0
        hud_back_at = None

        # 2. 变身期间持续连点 E。HUD 刚恢复时角色仍可能在空中，且第二段 E
        # 尚未完整触发，所以恢复后仍保留固定 1.00s 的输入窗口。
        while time.time() - start_time < max_wait:
            now = time.time()
            if now - last_e >= 0.08:
                self.small_cart_tap('E', hold=0.04)
                last_e = now
                n_e += 1

            self.safe_next_frame()
            now = time.time()
            if now - start_time >= self.CART_TRANSFORM_ANIM and self.safe_in_team():
                hud_back_hits += 1
                if hud_back_hits >= 2 and hud_back_at is None:
                    hud_back_at = now
                    self.macro_mark(
                        f'{tag}_变身演出恢复(耗时{now - start_time:.2f}s,E连点x{n_e})'
                    )
                    self.safe_relock_target(tag=f'{tag}_R1恢复锁定')
            elif hud_back_at is None:
                hud_back_hits = 0

            if (hud_back_at is not None
                    and now - hud_back_at >= self.TRANSFORM_POST_HUD_EE_WINDOW):
                self.macro_mark(
                    f'{tag}_完整EE确认(HUD恢复后{now - hud_back_at:.2f}s,E连点x{n_e})'
                )
                break

            time.sleep(0.015)
            self.check_enabled()
        else:
            if hud_back_at is None:
                raise CycleAbort(f'[{tag}] 变身演出后HUD未恢复，禁止提前切人')
            raise CycleAbort(f'[{tag}] HUD已恢复但完整EE输入窗口未完成')

        # 3. v1.9.0 提前交接夏空：双E已可靠输入且HUD恢复，移除大卡 wait_down 与 0.20s 落地等待，直接交接切夏空
        self.macro_mark(f'{tag}_提前交接夏空')
        self.macro_mark(f'{tag}_成功')
        return True

    # ==================== 1. 夏空脱手 R 流程 (手打录制 1:1 还原) ====================
    def execute_ciac_r_and_switch_back(self, tag='切夏r切回'):
        """
        手打录制 1:1 还原（下落攻击完整落地后释放大招）：
        1. 秒切夏空入场 (Key 2)；
        2. 空中触发夏空下落攻击，并等待下落攻击完整砸地扎稳落地；
        3. 完整落地后才开启夏空脱手大招 R 直至成功释放；
        4. 演出期间动态等待 HUD 恢复 (~3.3s)；
        5. 连点 1 号键秒切回卡提大卡！
        """
        chars = self.char_by_token()
        ciac = chars.get('夏')
        cart = self
        if ciac is None:
            return

        self.macro_mark(f'{tag}_开始')

        # 1. 秒切夏空 (Key 2，单次精准按键)
        self.switch_to_char(ciac, tag=f'{tag}_切夏', retries=1)
        self.macro_wait(0.06)

        # 2. 空中触发夏空下落攻击，并等待下落攻击完整砸地落地扎稳
        self.macro_mark(f'{tag}_空中下落攻击')
        # 连点左键触发下落攻击并引导下坠砸向地面 (手打录制约 0.80s)
        self.rapid_attack_clicks(duration=0.80, tag=f'{tag}_下落砸地', clicks_per_sec=6.5)
        # 确保下落攻击完整着地稳固，收招落地硬直完全解除 (0.25s)
        self.macro_wait(0.25)
        self.macro_mark(f'{tag}_下落攻击已完整落地')

        # 3. 完整落地后才开启夏空的大招 R (连点直至大招成功触发)
        r_key = self.key_of('R') or 'r'
        start_r = time.time()
        r_fired = False
        while time.time() - start_r < 2.2:
            self.task.send_key_down(r_key)
            self.macro_wait(0.04)
            self.task.send_key_up(r_key)
            self.safe_next_frame()

            is_hud_gone = not self.safe_in_team()
            is_cd = False
            try:
                if hasattr(self.task, 'has_cd') and self.task.has_cd('liberation'):
                    is_cd = True
                elif hasattr(ciac, 'has_cd') and ciac.has_cd('liberation'):
                    is_cd = True
                elif hasattr(self.task, 'get_cd') and self.task.get_cd('liberation') > 0.2:
                    is_cd = True
            except Exception:
                pass

            if is_hud_gone or is_cd:
                r_fired = True
                self.macro_mark(f'{tag}_夏空R成功释放(HUD消失={is_hud_gone},CD={is_cd})')
                break
            time.sleep(0.02)
            self.check_enabled()

        # 4. 等待大招黑边演出恢复 (允许切人窗口开启)
        if r_fired:
            self.wait_hud_back(tag=f'{tag}_夏空R演出恢复', min_gone=0.35, time_out=3.5)
        else:
            self.macro_wait(0.20)

        # 5. 连点 1 切回卡提大卡
        cart_key = str(cart.index + 1)
        start_sw = time.time()
        while time.time() - start_sw < 2.0:
            self.task.send_key_down(cart_key)
            self.macro_wait(0.04)
            self.task.send_key_up(cart_key)
            self.safe_next_frame()
            try:
                in_team, cur_idx, _ = self.task.in_team()
                if in_team and cur_idx == cart.index:
                    break
            except Exception:
                pass
            self.macro_wait(0.05)
            self.check_enabled()

        self.handoff(ciac, cart)
        self.macro_mark(f'{tag}_完成')


    # ==================== 2. 大卡 R2 万阙终结技 (手打录制 1:1 还原) ====================
    def ensure_r2(self, tag='R2万阙', max_wait_cd=3.5, lib_anim_timeout=6.5):
        """
        大卡 R2 万阙终结大招释放（手打录制 1:1 还原）：
        1. 连点 R 触发万阙大招 (HUD 消失进入特写演出)；
        2. 等待演出结束 HUD 恢复 (4.60s 演出)；
        3. 落地连点 E (0.60s) 补能，随后立即秒切夏空！
        """
        self.macro_mark(f'{tag}_开始')
        r_key = self.key_of('R') or 'r'

        start_tap = time.time()
        triggered = False
        last_r = 0.0
        n_r = 0
        last_fill_attack = start_tap
        n_fill_attacks = 0
        gone_at = None

        while time.time() - start_tap < max_wait_cd:
            now = time.time()
            if now - last_r >= 0.08:
                self.task.send_key_down(r_key)
                self.macro_wait(0.04)
                self.task.send_key_up(r_key)
                last_r = now
                n_r += 1

            self.safe_next_frame()
            if not self.safe_in_team():
                triggered = True
                gone_at = time.time()
                self.macro_mark(
                    f'{tag}_大招已触发(等待{time.time() - start_tap:.2f}s,'
                    f'R连点x{n_r},补普攻x{n_fill_attacks})'
                )
                break

            # R2 仍未触发且 HUD 正常时，用短普攻填补冷却等待。先检查 HUD
            # 再补刀，保证触发 R 的这一帧绝不会多按一次攻击。
            fill_now = time.time()
            if (fill_now - start_tap >= self.R2_FILL_ATTACK_DELAY
                    and fill_now - last_fill_attack >= self.R2_FILL_ATTACK_INTERVAL):
                self.safe_click()
                last_fill_attack = fill_now
                n_fill_attacks += 1

            time.sleep(0.015)
            self.check_enabled()

        if not triggered:
            self.macro_mark(
                f'{tag}_等待超时放行(R连点x{n_r},补普攻x{n_fill_attacks})'
            )

        if triggered:
            back, anim_dur = self.wait_hud_back(
                tag=f'{tag}_演出等待',
                min_gone=0.45,
                time_out=lib_anim_timeout,
                initial_gone_at=gone_at,
            )
            self.macro_wait(0.15)
            self.macro_mark(f'{tag}_结束(成功={back},演出耗时{anim_dur:.2f}s)')
            if back:
                self.safe_relock_target(tag=f'{tag}_HUD恢复后锁定')
        else:
            self.macro_mark(f'{tag}_超时放行')

        # 落地连点 E 补能 (手打录制：连点 E 6次，约 0.60s)
        e_key = self.key_of('E') or 'e'
        end_e = time.time() + 0.60
        while time.time() < end_e:
            self.task.send_key_down(e_key)
            self.macro_wait(0.04)
            self.task.send_key_up(e_key)
            self.macro_wait(0.08)

        return triggered

    def play_macro(self, macro, tag, start=None):
        if not macro:
            return
        t0 = time.time() if start is None else start
        chars = self.char_by_token()
        for t, act, token in macro:
            target = t0 + t
            left = target - time.time()
            if left > 0.002:
                self.sleep_until(target)
            if act == 'd':
                self.press_down(token, chars)
            elif act == 'u':
                self.press_up(token, chars)
        self.release_all()

    def click_until(self, target, interval=None):
        iv = interval or self.DEFAULT_CLICK_INTERVAL
        while time.time() < target:
            self.safe_click()
            self.macro_wait(iv)

    def rapid_attack_clicks(self, duration, tag='高频攻击连打', clicks_per_sec=6.8):
        """
        以 6~7 次/秒高频快速点击鼠标左键（连点释放频率）：
        按下 0.035s，抬起 0.110s，单次循环 0.145s (约 6.9 次/秒)，
        在预输入缓冲下以极致攻速连贯打出全套动作，绝不卡顿、绝不漏刀！
        """
        self.macro_mark(f'{tag}_开始({duration:.2f}s)')
        end_time = time.time() + duration
        interval = 1.0 / clicks_per_sec if clicks_per_sec > 0 else 0.145
        hold = 0.035
        gap = max(0.01, interval - hold)
        clicks = 0
        while time.time() < end_time:
            self.task.mouse_down()
            self.macro_wait(hold)
            self.task.mouse_up()
            self.macro_wait(gap)
            clicks += 1
            self.check_enabled()
        self.macro_mark(f'{tag}_完成(共{clicks}次)')
        return clicks

    def is_concerto_ready(self):
        try:
            if hasattr(self.task, 'is_con_full') and self.task.is_con_full():
                return True
            if hasattr(self.task, 'get_current_con'):
                val = self.task.get_current_con()
                if val >= 0.98:
                    return True
        except Exception:
            pass
        return False

    def is_ciaccona_forte_3_bars(self):
        try:
            chars = self.char_by_token()
            ciac = chars.get('夏')
            if ciac is not None:
                if hasattr(ciac, 'is_mouse_forte_full') and ciac.is_mouse_forte_full():
                    return True
                if hasattr(ciac, 'judge_forte') and ciac.judge_forte() >= 3:
                    return True
        except Exception:
            pass
        return False

    # ==================== 3. 夏空协奏充能回路 (手打录制 1:1 还原) ====================
    # ==================== 3. 夏空变奏入场与协奏循环 ====================
    def run_post_r2_ciaccona_cycle(self, tag='夏空协奏充能'):
        """
        夏空变奏入场与协奏回路爆发：
        1. 秒切夏空入场；
        2. 入场预输入 A1 触发下落攻击，等待并确认落地扎稳；
        3. 落地后打完完整 A4（A1~A4），攒满第 3 格共鸣回路；
        4. 按 E 技能打断 A5 后摇；
        5. E 期间预输入 Q + 重击长按；
        6. 重击消耗 3 格回路，确认满协奏；
        7. 触发延奏，切回卡提变奏入场！
        """
        self.macro_mark(f'{tag}_开始')
        chars = self.char_by_token()
        ciac = chars.get('Ciaccona') or chars.get('夏')
        cart = chars.get('Cartethyia') or self
        if ciac is None:
            return

        # 1. 秒切夏空入场
        self.macro_mark(f'{tag}_秒切夏空')
        self.switch_to_char(ciac, tag=f'{tag}_切夏', retries=1)
        self.macro_wait(0.05)

        # 2. 入场预输入 A1 触发下落攻击并等待落地
        self.macro_mark(f'{tag}_预输入A1下落')
        self.small_cart_tap('L', hold=0.05, gap=0.04)
        self.macro_mark(f'{tag}_等待下落落地')
        self.wait_down(click=False)
        self.macro_wait(self.CIAC_LAND_SETTLE)
        self.macro_mark(f'{tag}_下落落地确认')

        # 3. 落地后完整打完 A4 (A1~A4)
        self.macro_mark(f'{tag}_A4连打开始')
        self.rapid_attack_clicks(duration=self.CIAC_A4_DURATION, tag=f'{tag}_A4', clicks_per_sec=7.0)
        self.macro_mark(f'{tag}_A4完整结束')

        # 4. 按 E 技能打断 A5
        self.macro_mark(f'{tag}_E打断A5')
        self.small_cart_tap('E', hold=0.06, gap=0.05)

        # 5. E 期间预输入 Q + 重击长按
        self.macro_mark(f'{tag}_E期间预输入Q')
        self.small_cart_tap('Q', hold=0.06, gap=0.04)

        self.macro_mark(f'{tag}_重击长按开始')
        self.check_enabled()
        self.press_down('L', chars)
        try:
            self.macro_wait(self.CIAC_HEAVY_HOLD)
        finally:
            self.press_up('L', chars)
        self.macro_mark(f'{tag}_重击消耗回路完成')
        self.macro_wait(0.08)
        self.safe_next_frame()

        # 6. 协奏确认与兜底补能
        comp_start = time.time()
        heavy_retried = False
        while not self.is_concerto_ready() and time.time() - comp_start < self.CIAC_CONCERTO_COMP_CAP:
            self.safe_next_frame()
            if not heavy_retried and self.is_ciaccona_forte_3_bars():
                self.small_cart_tap('RB', hold=self.CIAC_RB_HOLD)
                self.press_down('L', chars)
                try:
                    self.macro_wait(self.CIAC_HEAVY_HOLD)
                finally:
                    self.press_up('L', chars)
                heavy_retried = True
                self.macro_mark(f'{tag}_协奏补偿重击')
            else:
                self.small_cart_tap('L', hold=0.05, gap=0.15)

        concerto_ready = self.is_concerto_ready()
        self.macro_mark(
            f'{tag}_协奏确认({"已满" if concerto_ready else "超时"},'
            f'{time.time() - comp_start:.2f}s)'
        )
        if not concerto_ready:
            self.logger.warning(f'[{tag}] 夏空协奏充能超时，仍按轴尝试切回卡提')

        # 7. 满协奏切回卡提
        self.macro_mark(f'{tag}_切回卡提')
        cart_key = str(cart.index + 1)
        start_sw = time.time()
        while time.time() - start_sw < 2.0:
            self.task.send_key_down(cart_key)
            self.macro_wait(0.04)
            self.task.send_key_up(cart_key)
            self.safe_next_frame()
            try:
                in_team, cur_idx, _ = self.task.in_team()
                if in_team and cur_idx == cart.index:
                    break
            except Exception:
                pass
            self.macro_wait(0.04)
        self.handoff(ciac, cart)
        self.macro_mark(f'{tag}_完成')

    def small_cart_sword_ready(self, marker):
        """检测原生剑标记；它只用于记录，不再作为连招硬门禁。"""
        try:
            return bool(self.task.find_one(marker, threshold=0.9))
        except Exception as exc:
            self.logger.warning(f'[小卡出剑检测] 无法检测 {marker}: {exc}')
            return False

    def small_cart_tap(self, token, hold=0.04, gap=0.0):
        """记录并配对按下/松开，停用或重启中断时也必须松键。"""
        chars = self.char_by_token()
        self.check_enabled()
        self.press_down(token, chars)
        try:
            self.macro_wait(hold)
        finally:
            self.press_up(token, chars)
        if gap > 0:
            self.macro_wait(gap)

    def execute_small_cart_a4(self, tag='小卡A4'):
        """按手打结构执行小卡 A4，并在段内补入 Q。

        普通攻击剑图标是否识别到，不能单独证明 A4 动作是否完成。
        原始反馈中 Q 位于 R 切小卡后约 0.75s、第一下普攻后约 0.67s，
        因此不能再提前到 R 之前。
        """
        start = time.perf_counter()
        deadline = start + self.SMALL_A4_DURATION
        clicks = 0
        echo_sent = False
        while time.perf_counter() < deadline:
            elapsed = time.perf_counter() - start
            if not echo_sent and elapsed + 1e-6 >= self.SMALL_A4_ECHO_AT:
                self.small_cart_tap('Q', hold=0.08)
                echo_sent = True
                # 反馈局中 Q 松开与下一次 A 仅隔约 0.0002s，续段容易被吞。
                # 延长总窗口同等时长，确保增加缓冲时不会减少 A4 的有效输入时间。
                deadline += self.SMALL_A4_ECHO_RECOVERY_GAP
                self.macro_wait(self.SMALL_A4_ECHO_RECOVERY_GAP)
                continue
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            if not echo_sent:
                until_echo = start + self.SMALL_A4_ECHO_AT - time.perf_counter()
                if 0 < until_echo <= 0.035:
                    self.macro_wait(until_echo)
                    continue
            self.small_cart_tap('L', hold=min(0.035, remaining))
            clicks += 1
            remaining = deadline - time.perf_counter()
            if remaining > 0:
                wait_for = min(0.11, remaining)
                if not echo_sent:
                    until_echo = start + self.SMALL_A4_ECHO_AT - time.perf_counter()
                    if until_echo > 0:
                        wait_for = min(wait_for, until_echo)
                    else:
                        wait_for = 0
                self.macro_wait(wait_for)
        if not echo_sent:
            self.small_cart_tap('Q', hold=0.08)
        self.macro_mark(
            f'{tag}_动作窗口完成({time.perf_counter() - start:.2f}s,输入{clicks}次,Q已释放)'
        )

    def execute_small_cart_full_heavy(self, tag='小卡完整重击'):
        """完整长按约 1.02s 后松键；剑标记仅记录，不再误截断连招。"""
        chars = self.char_by_token()
        marker = 'forte_cartethyia_sword1'
        self.safe_next_frame()
        existed_before = self.small_cart_sword_ready(marker)
        self.macro_mark(f'{tag}_开始(入段剑标记={existed_before})')
        self.check_enabled()
        self.press_down('L', chars)
        started = time.perf_counter()
        marker_seen = existed_before
        try:
            while time.perf_counter() - started < self.SMALL_HEAVY_HOLD:
                self.macro_wait(0.02)
                self.safe_next_frame()
                if self.small_cart_sword_ready(marker):
                    marker_seen = True
        finally:
            # 包括 F 重开、停用、读屏错误在内，任何路径都必须松开左键。
            held_duration = time.perf_counter() - started
            self.press_up('L', chars)

        self.macro_mark(
            f'{tag}_完整长按结束({held_duration:.2f}s,剑标记可见={marker_seen})'
        )
        if not marker_seen:
            self.logger.warning(
                f'[{tag}] 重击剑标记未识别；已完成固定长按，按手打轴继续跳跃'
            )
        self.macro_wait(self.SMALL_HEAVY_TO_JUMP_GAP)

    def execute_small_cart_plunge_inputs(self, tag='小卡下落攻击输入'):
        """跳跃后连续输入左键 0.50s，并确认完整落地后才允许切人。"""
        self.small_cart_tap('SP', hold=0.06, gap=0.12)
        deadline = time.perf_counter() + 0.50
        while time.perf_counter() < deadline:
            remaining = deadline - time.perf_counter()
            self.small_cart_tap('L', hold=min(0.035, remaining))
            remaining = deadline - time.perf_counter()
            if remaining > 0:
                self.macro_wait(min(0.11, remaining))
        self.macro_mark(f'{tag}_0.50s完成')
        # 反馈局中最后一次左键松开后仅约 0.026s 就按 R，伤害尚未砸地。
        # 优先使用运行时空中状态检测，固定缓冲则覆盖图标暂时漏检的情况。
        self.wait_down(click=False)
        self.macro_wait(self.SMALL_PLUNGE_LAND_SETTLE)
        self.macro_mark(f'{tag}_完整落地确认')

    def execute_small_cart_jump_plunge_switch_back(self, tag='小卡跳A切大卡'):
        """
        用户手打结构：R切小卡 -> A4（约0.67s时Q）-> 完整重击
        -> 跳 -> 连点左键0.50s -> R切回大卡。
        """
        self.macro_mark(f'{tag}_开始')
        self.macro_mark(f'{tag}_切小卡')
        self.small_cart_tap('R', hold=0.08)
        # 反馈局中 R 松开后约 0.0003s 就按下第一发 A；先让切人动作接管角色。
        self.macro_wait(self.SMALL_SWITCH_TO_A4_GAP)
        self.macro_mark(f'{tag}_切小卡后缓冲完成({self.SMALL_SWITCH_TO_A4_GAP:.2f}s)')
        self.safe_relock_target(tag=f'{tag}_切小卡后锁定')
        self.execute_small_cart_a4(tag=f'{tag}_A4')
        # 给 A4 收尾动画留出明确边界，再开始重击长按。
        self.macro_wait(self.SMALL_A4_TO_HEAVY_GAP)
        self.execute_small_cart_full_heavy(tag=f'{tag}_完整重击')
        self.macro_mark(f'{tag}_完整重击后起跳')
        self.execute_small_cart_plunge_inputs(tag=f'{tag}_下落攻击')
        self.macro_mark(f'{tag}_落地切大卡')
        self.small_cart_tap('R', hold=0.04, gap=0.15)
        self.safe_relock_target(tag=f'{tag}_切回大卡后锁定')
        self.macro_mark(f'{tag}_完成')

    def execute_small_cart_heavy_plunge_switch_back(self, tag='小卡重击下落切大卡'):
        """第二轮起的提速结构：R切小卡 -> 完整重击 -> 跳A落地 -> R切回。"""
        self.macro_mark(f'{tag}_开始')
        self.macro_mark(f'{tag}_切小卡')
        self.small_cart_tap('R', hold=0.08)
        self.macro_wait(self.SMALL_SWITCH_TO_A4_GAP)
        self.macro_mark(f'{tag}_切小卡后缓冲完成({self.SMALL_SWITCH_TO_A4_GAP:.2f}s)')
        self.safe_relock_target(tag=f'{tag}_切小卡后锁定')
        self.execute_small_cart_full_heavy(tag=f'{tag}_完整重击')
        self.macro_mark(f'{tag}_完整重击后起跳')
        self.execute_small_cart_plunge_inputs(tag=f'{tag}_下落攻击')
        self.macro_mark(f'{tag}_落地切大卡')
        self.small_cart_tap('R', hold=0.04, gap=0.15)
        self.safe_relock_target(tag=f'{tag}_切回大卡后锁定')
        self.macro_mark(f'{tag}_完成')

    # 保留历史兼容别名
    execute_small_cart_switch_combo = execute_small_cart_jump_plunge_switch_back
    execute_small_cart_plunge_switch_back = execute_small_cart_jump_plunge_switch_back
    execute_small_cart_a4z_tri_swords = execute_small_cart_jump_plunge_switch_back

    # ==================== 5. 大卡 A5 连打 ====================
    def execute_fleur_a5(self, tag='大卡a5', need_r_switch=False, duration=2.50):
        """
        大卡 A5 高频连打（手打录制 1:1 还原）：
        持续以 6.8 次/秒高频快速点击鼠标左键：
        - 阶段一 (切小卡前): duration=2.50s (打满 15 次连打)
        - 阶段二 (开大前): duration=3.30s (打满 20~22 次连打并衔接 R2)
        """
        self.macro_mark(f'{tag}_开始')
        if need_r_switch:
            r_key = self.key_of('R') or 'r'
            self.task.send_key(r_key, down_time=0.04)
            self.macro_wait(0.18)

        self.rapid_attack_clicks(duration=duration, tag=f'{tag}_连打')
        self.macro_wait(0.06)
        self.macro_mark(f'{tag}_完成')

    # ==================== 6. 卡提变奏入场至 R1 变身 (手打录制 1:1 还原) ====================
    def execute_cart_intro_and_r1_transform(self, tag='卡提变奏R1变身', next_round=2):
        """
        卡提变奏入场充能至 R1 变身（手打录制 1:1 还原）：
        1. 卡提变奏稳定后地面平 A 连打 (2.80s，打满一套地面普攻回路)；
        2. 空格起跳 (Space)；
        3. 空中下落攻击连打 (5次空斩下落砸地，约 0.60s)；
        4. 下落攻击完整落地扎稳 (0.18s)；
        5. 确认 E 进入冷却，并等待施法动作完整结束；
        6. 不再接垫刀 A，避免取消 E 的充能动作；
        7. 直接按 R1 变身大卡，变身期间狂点 E 直达 CD，出双 EE 开启第 {next_round} 轮！
        """
        self.macro_mark(f'{tag}_开始')
        sp_key = self.key_of('SP') or 'space'
        e_key = self.key_of('E') or 'e'

        # 1. 夏空切回后先稳定，再把变奏地面 A4 完整打满。
        self.macro_wait(self.CART_INTRO_SETTLE)
        self.macro_mark(f'{tag}_变奏入场稳定({self.CART_INTRO_SETTLE:.2f}s)')
        self.macro_mark(f'{tag}_变奏平A连打')
        self.rapid_attack_clicks(duration=self.CART_INTRO_A4_DURATION, tag=f'{tag}_变奏平A')
        self.macro_wait(self.CART_INTRO_A4_TO_JUMP_GAP)
        self.macro_mark(f'{tag}_变奏A4完整结束')

        # 2. 空格起跳 + 下落攻击 (充能核心动作：起跳后 5 次空斩下落砸地)
        self.macro_mark(f'{tag}_起跳下落攻击')
        self.task.send_key_down(sp_key)
        self.macro_wait(0.06)
        self.task.send_key_up(sp_key)
        self.macro_wait(0.12)  # 稳妥起跳至下落高度

        # 空中下落攻击连打 (5次点击，约 0.60s，引导下坠砸向地面)
        self.rapid_attack_clicks(duration=0.60, tag=f'{tag}_下落砸地', clicks_per_sec=7.0)
        # 确保下落攻击完整砸地落地扎稳。
        self.wait_down(click=False)
        self.macro_wait(self.SMALL_PLUNGE_LAND_SETTLE)
        self.macro_mark(f'{tag}_下落攻击已完整落地')

        # 3. 确认 E 真正进入 CD 后等待动作结束。112531 反馈中最后一次 E
        # 松开仅 0.06s 就接垫刀 A，E 动作被取消，随后补能与 R 重试共浪费约 7.5s。
        self.macro_mark(f'{tag}_E完整释放')
        e_released = self.ensure_resonance_released(
            tag=f'{tag}_E完整释放',
            max_wait=self.CART_INTRO_E_RELEASE_MAX,
        )
        self.macro_wait(self.CART_INTRO_E_CAST_SETTLE)
        self.macro_mark(
            f'{tag}_E施法保护完成(已确认CD={e_released},等待{self.CART_INTRO_E_CAST_SETTLE:.2f}s)'
        )

        # 4. 直接尝试 R1；删除垫刀 A 与常规 2s 普攻补能，避免再次打断 E。
        self.cast_r_and_press_e_until_cd(tag=f'{tag}_变身EE开{next_round}轮')
        self.macro_mark(f'{tag}_完成')


    # 兼容历史方法名
    def execute_cart_intro_a4_jump_heavy_e(self, tag='卡提变奏4A落地重击E'):
        return self.execute_cart_intro_and_r1_transform(tag=tag, next_round=self.current_round + 1)

    # ==================== 1. 首轮开局轴 (从 2.894s 严格对齐手打结构) ====================
    def run_opener_round(self):
        """
        第 1 轮（首轮开局轴，从 2.894s 严格按照手打结构还原）：
        1. [2.894s] 点按 E (0.10s)
        2. [3.089s] 间隔 0.195s 按 R 变身大卡
        3. 变身期间狂点 E (出双EE直达CD)
        4. 落地立即按 2 秒切夏空
        5. 夏空平A 1.10s (7次) -> 按 R 脱手大招
        6. 演出结束连点 1 秒切回大卡
        7. 大卡前段 A5 (2.50s，15次) -> 按 R 切小卡
        8. Q -> R切小卡 -> A4 -> 完整重击并确认剑标记 -> 跳 -> 左键0.50s -> R切回
        9. 大卡后段 A5 (3.30s，20次) -> 按 R 触发 R2 万阙
        10. 演出 4.60s 落地连点 E (0.60s) -> 按 2 秒切夏空
        11. 夏空按卡夏千回路结构完成落地A、右键、长按重击，确认协奏后切回卡提
        12. 卡提充能后以 A↔R 补偿确认变身，再在演出期间连点E开启下一轮
        """
        self.logger.info('[卡5R] 开始执行: 第 1 轮 (手打 2.894s 结构对齐开局)')
        self.macro_mark('首轮开局_2.894s起')

        # 1. [2.894s] 点按 E (0.10s)
        e_key = self.key_of('E') or 'e'
        self.task.send_key(e_key, down_time=0.08)

        # 2. [3.089s] 间隔 0.195s 后按 R
        self.macro_wait(0.12)

        # 3. R1 变身并在变身期间持续连点 E 直达 CD
        self.cast_r_and_press_e_until_cd(tag='O1小卡R变身EE')

        # 4. 落地立即按 2 秒切夏空脱手 R (平A 7次 + R) 并秒切回大卡
        self.execute_ciac_r_and_switch_back(tag='O2切夏r切回')

        # 5. 大卡前段 a5 砍满 (2.50s，15次)
        self.execute_fleur_a5('O3大卡a5', duration=2.50)

        # 6. 小卡合轴 (Q -> R -> A4 -> 完整重击/确认 -> 跳A -> R)
        self.execute_small_cart_jump_plunge_switch_back('O4小卡跳A切大卡')

        # 7. 大卡后段 a5 砍满 (3.30s，20次) -> 大卡 R2 万阙
        self.execute_fleur_a5('O5大卡ra5', duration=3.30)
        self.ensure_r2(tag='O5大卡R2万阙')

        # 8. 切夏空，按卡夏千结构打满回路与协奏后切回卡提
        self.run_post_r2_ciaccona_cycle('O6夏空协奏充能')

        # 9. 卡提变奏入场 (变奏平A 3.2s + 连点 E 补能 + R1变身EE开第2轮)
        self.execute_cart_intro_and_r1_transform('O7卡提变奏R1变身', next_round=2)

        self.last_opener = time.time()
        self.task._opener_last_time = time.time()
        self.opener_armed = False
        self.current_round = 2

    # ==================== 2. 次轮轴 (第 2 轮) ====================
    def run_second_round(self):
        """第 2 轮：统一由 run_loop_round(2) 执行"""
        self.run_loop_round(2)

    # ==================== 3. 循环轴 (第 2、3、4、5 轮，手打录制 1:1 还原) ====================
    def run_loop_round(self, round_num):
        """
        循环轴 (第 2、3、4、5 轮，手打录制 1:1 结构完全对齐)：
        1. 承接上一轮末尾的大卡 EE，落地秒切夏空脱手 R (平A 7次 + R) 并秒切回大卡
        2. 大卡前段 a5 (2.50s，15次)
        3. 第二轮起小卡提速段 (R -> 完整重击 -> 跳A落地 -> R)
        4. 大卡后段 a5 (3.30s，22次) -> 大卡 R2 万阙终结大招
        5. 若 round_num >= 5: 第 5 轮万阙压轴斩杀直接收官！
        6. 万阙落地后切夏空，按卡夏千结构打满回路与协奏，再切回卡提
        7. 卡提充能后以 A↔R 补偿确认 R1 变身，并在演出期间连点 E 开下一轮
        """
        self.logger.info(f'[卡5R] 开始执行: 第 {round_num} 轮循环')
        self.macro_mark(f'第{round_num}轮开始')

        # 1. 承接上一轮末尾的 R1 变身 EE，秒切夏空脱手 R (平A 7次 + R) 并秒切回大卡
        self.execute_ciac_r_and_switch_back(tag=f'L{round_num}切夏r切回')

        # 2. 大卡前段 a5 砍满 (2.50s)
        self.execute_fleur_a5(f'L{round_num}大卡a5', duration=2.50)

        # 3. 第二轮起不再重复 A4/Q，只保留完整重击与下落攻击。
        self.execute_small_cart_heavy_plunge_switch_back(f'L{round_num}小卡重击下落切大卡')

        # 4. 大卡后段 a5 砍满 (3.30s) -> 大卡 R2 万阙
        self.execute_fleur_a5(f'L{round_num}大卡ra5', duration=3.30)

        # 7EE 关键点: 第 4 轮 a5 结尾追加释放一次 E (若可用)
        if self.ENABLE_7EE and round_num in (3, 4):
            e_key = self.key_of('E') or 'e'
            self.task.send_key(e_key, down_time=0.04)
            self.macro_wait(0.10)

        # 5. 大卡 R2 万阙终结大招
        self.ensure_r2(tag=f'L{round_num}R2万阙')

        # 已经到达第 5 轮，压轴 R2 后全部 5 轮收尾完毕！
        if round_num >= 5:
            self.logger.info('[卡5R] 5 轮轴(5R10下7EE)全部收尾完毕！')
            self.current_round = 6
            return

        # 6. 切夏空协奏回路充能 (平A 13次 + E + 强化重击 0.9s 带Q) -> 切回卡提
        self.run_post_r2_ciaccona_cycle(f'L{round_num}夏空协奏充能')

        # 7. 卡提充能 (完整变奏A4 + 下落 + E完整释放保护 + R1变身EE开下一轮)
        next_round = round_num + 1
        self.execute_cart_intro_and_r1_transform(f'L{round_num}卡提变奏R1变身', next_round=next_round)
        self.current_round = next_round
    def run_cleanup_loop(self):
        """打满 5 轮大招后，若剩余时间怪物未死，平滑执行终结清场收割，绝不重复半截第5轮"""
        self.logger.info('[卡夏5R] 5轮已打满，执行压哨收尾清场模式！')
        self.macro_mark('清场模式开始')

        # 小卡持续双 E + 重击 + 普攻连续收尾
        e_key = self.key_of('E') or 'e'
        for _ in range(2):
            self.task.send_key(e_key, down_time=0.04)
            self.macro_wait(0.20)

        # 蓄力重击出剑
        self.task.mouse_down()
        self.macro_wait(0.88)
        self.task.mouse_up()
        self.macro_wait(0.10)

        # 连点普攻打扫战场
        self.click_until(time.time() + 2.0)
        self.macro_mark('清场模式结束')

    # ==================== 核心执行入口 ====================
    def do_perform(self):
        if not self.is_in_kxq_cycle:
            super().do_perform()
            return

        self.task.skip_combat_check = True

        if self.cur is None:
            self.cur = self

        try:
            self.macro_start()

            if self.opener_pending():
                self.run_opener_round()
            elif self.current_round in (2, 3, 4, 5):
                self.run_loop_round(self.current_round)
            else:
                # 第 5 轮已打满后平滑执行终结收尾，绝不重复调用第 5 轮
                self.run_cleanup_loop()

            self.cur = self
            self.is_current_char = True
            self.logger.info(
                f'[卡夏5R] 轮次执行完成: 当前第 {self.current_round} 轮 | 轴线: {self.macro_timeline()} '
                f'(屏蔽脱战: {self._ignored_combat_lost} 次, 补偿: {self._comp_count} 次)'
            )
        except CycleAbort as e:
            self.logger.warning(f'[卡夏5R] CycleAbort 中断 ({e}) | 时间线: {self.macro_timeline()}')
            self.release_all()
            self.cur = self
            self.is_current_char = True
            restarted = getattr(self.task, '_ww_task_restarted_at', -1)
            f_press = getattr(self.task, '_f_last_press', -1)
            if restarted > self.macro_t0 or f_press > self.macro_t0:
                self.opener_armed = True
                self.current_round = 1
                self.last_opener = -1
            return
        except Exception as e:
            self.logger.warning(f'[卡夏5R] 异常中断 ({type(e).__name__}: {e}) | 时间线: {self.macro_timeline()}')
            self.release_all()
            raise
