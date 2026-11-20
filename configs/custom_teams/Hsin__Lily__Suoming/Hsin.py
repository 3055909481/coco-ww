import base64
import ctypes
import threading
import time

import cv2
import numpy as np

from src.char.BaseChar import SwitchPriority
from src.char.Hsin import Hsin as NativeHsin

# ==================== 物理 F 键监视 ====================
# 跟 Iuno__JingRan__ShoreKeeper/JingRan.py 里那份逐字相同, 说明见 Chisa__Suisui__YangYangSp/YangYangSp.py。
F_VK = 0x46              # 'F'
F_POLL_INTERVAL = 0.05   # 一次按键按住的时间远大于这个, 不会漏采
F_LOG_DEBOUNCE = 1.0     # 按住不放时别刷日志
F_TASK_NAME = 'FStartCombatTask'


# 监视线程: 椰果版用主程序 src/combat/AxisWatch.py 那份; 原版 OKWW 没有这个模块, 导入失败就用下面自带的同一份
# (逻辑和 AxisWatch.py 一致, 写到 task 上的标记也一样, 线程按标记只起一次)
try:
    from src.combat.AxisWatch import f_settings, start_enable_watch, start_f_watch
except ImportError:
    def f_settings(task):
        executor = getattr(task, 'executor', None)
        for group in ('trigger_tasks', 'onetime_tasks'):
            for other in getattr(executor, group, None) or ():
                if type(other).__name__ == 'FStartCombatTask':
                    try:
                        return bool(other.enabled), float(getattr(other, 'f_start_delay', 0) or 0)
                    except Exception:
                        return True, 0.0
        return True, 0.0

    def start_enable_watch(task, logger=None):
        if getattr(task, '_ww_enable_watch_started', False):
            return
        task._ww_enable_watch_started = True
        if not hasattr(task, '_ww_task_restarted_at'):
            task._ww_task_restarted_at = -1

        def watch():
            was_on = None
            while True:
                try:
                    now_on = bool(getattr(task, 'enabled', False))
                    if was_on is False and now_on:
                        task._ww_task_restarted_at = time.time()
                        if logger:
                            logger.info('auto combat re-enabled -> opener re-armed')
                    was_on = now_on
                    time.sleep(0.2)
                except Exception:
                    return

        threading.Thread(target=watch, name='ok_ww_enable_watch', daemon=True).start()

    def start_f_watch(task, logger=None):
        if getattr(task, '_f_watch_started', False):
            return
        task._f_watch_started = True
        if not hasattr(task, '_f_last_press'):
            task._f_last_press = -1

        def watch():
            try:
                user32 = ctypes.windll.user32
                user32.GetAsyncKeyState.restype = ctypes.c_short
            except Exception as e:
                task._f_watch_started = False
                if logger:
                    logger.error(f'f watch: GetAsyncKeyState unavailable ({e}), '
                                 f'the "press F to re-arm the opener" shortcut stays off')
                return
            last_log = 0
            was_down = False
            while True:
                try:
                    # 只读高位: 低位读一次就清而且整个进程共享
                    down = bool(user32.GetAsyncKeyState(0x46) & 0x8000)
                    if down and not was_down and f_settings(task)[0]:
                        now = time.time()
                        if getattr(task, '_three_fire_macro_busy', False) or getattr(task, '_ww_macro_busy', False):
                            if logger and now - last_log >= 1.0:
                                last_log = now
                                logger.info('physical F pressed while the macro is running '
                                            '(manual execution) - opener NOT re-armed')
                            was_down = down
                            time.sleep(0.05)
                            continue
                        task._f_last_press = now
                        task._f_pressed_at = now
                        if logger and now - last_log >= 1.0:
                            last_log = now
                            logger.info('physical F pressed, opener re-armed')
                    was_down = down
                    time.sleep(0.05)
                except Exception:
                    return

        threading.Thread(target=watch, name='ok_ww_f_watch', daemon=True).start()


# ==================== 自动战斗开关监视 ====================
# 【玩家手动开关自动战斗, 必须从启动轴重跑】。手动停任务不走 combat_end(), opener_armed 会跨局留在
# False, 而角色对象常被粘性复用、reset_state() 不跑。只有独立线程能观察到 enabled 的 True->False->True。
ENABLE_POLL_INTERVAL = 0.2


# ==================== SendInput 左键(心的两段长按用) ====================
# 结构体照搬 Iuno__JingRan__ShoreKeeper/JingRan.py。
_INPUT_MOUSE = 0
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort), ('dwFlags', ctypes.c_ulong),
                ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_ulong),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', ctypes.c_ulong), ('wParamH', ctypes.c_ushort), ('wParamL', ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('ki', _KEYBDINPUT), ('mi', _MOUSEINPUT), ('hi', _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('u', _INPUTUNION)]


def send_input_left(up):
    """系统级左键按下/抬起。返回是否发出去了(发不出去调用方退回 PostMessage)。"""
    try:
        inp = _INPUT(type=_INPUT_MOUSE)
        inp.u.mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTUP if up else _MOUSEEVENTF_LEFTDOWN, 0, 0)
        return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) == 1
    except Exception:
        return False


# SendInput 键盘: 按住左键期间要按的技能键走这个。PostMessage 的 send_key 每次先投 WM_ACTIVATE, 会把按住打断
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002


def send_input_key(vk, up):
    """系统级键盘按下/抬起(虚拟键码 + 扫描码)。返回是否发出去了。"""
    try:
        scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
        inp = _INPUT(type=_INPUT_KEYBOARD)
        inp.u.ki = _KEYBDINPUT(vk, scan, _KEYEVENTF_KEYUP if up else 0, 0, 0)
        return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) == 1
    except Exception:
        return False


class MacroBroken(Exception):
    """某一步切人没成功 / HUD 长时间不在, 后面的按键会落在错的人身上 —— 整圈放弃, 交回框架。"""


class Hsin(NativeHsin):
    """心。心锁莉(心/锁暝/莉莉)里她兼任【整条轴的持宏者】, 三个人的 do_perform 都转到 drive()。

    ================= 两条轴(AXIS_KIND) =================
    '5R'(默认): E:/轴视频/【同奏】2心锁莉-矩阵5R进阶.mp4(长风ei「2心锁莉-5R进阶轴」, 1080p 键鼠, 键盘浮层 + 左栏文字轴
          + 结尾流程图; 141~187s 是第三圈的 1 倍速完整演示)。作者 2 链心: 启动轴心第一段进场应世心就满, 直接重击。
          0 链心照样能跑(花图标没亮会 A-闪-A 补, 慢 1~2s)。
    '12W': E:/轴视频/【同奏】心锁莉-12w轮椅轴.mp4, v0.1.3 原样(下面「12W 轴的形状」)。
    代码【按角色切人】, 编队怎么排都行。

    ================= 5R 轴的形状 =================
    启动轴: 心 E -> 锁暝(普通) Q -> 莉莉(普通) R -> 演出尾巴连按闪避出梦乡 -> 2A -> E -> Q -> [点 A 到梦香满] 按住:
            织梦重击 -> 终章 -> 协奏满变奏心 (seg5_open_head / seg5_ly_open)
            心(变奏·应世相, 2 链满应世心) 按住步红尘 -> 连按 R 转相 -> 【同奏】切锁暝 (seg_xn1)
    循环轴: 莉莉(变奏·永眠花开) E -> 点一下 A -> 心(普通) 2A -> E -> 锁暝(普通) 2A -> 斜前闪(Boss 能处决就换成处决)
            -> 2A -> Q -> 莉莉(普通) [点 A 到梦香满] 按住: 织梦重击 -> 终章 -> Q -> R -> 演完变奏锁暝
            (seg5_ly_loop / seg_xn0 / seg5_so0 / seg5_ly_heavy)
            锁暝(变奏·碎霆 +200, 妄执正好满) E(无舍念) -> 【按住普攻】刻心念 -> 协奏满变奏心 (seg5_so_b)
            心(变奏·应世相) 点 A -> 闪避 -> [花图标亮了] 按住出步红尘 -> 连按 R 转相 -> 【同奏】切锁暝 (seg_xn1)
    之后两条一样:
      锁暝 (同奏变奏, 进念深) [R 没好边 A 边按] R -> 演完 -> 带着 5s 同奏切心 (seg5_so_r)
      心   (变奏·照世相, 统御众机) Q -> A 到第 1 颗雕像 -> 点到第 2 颗 -> 闪避 -> A 到第 7 颗 -> 按住(临寰宇)
           -> 万阙垂天 -> 协奏满变奏莉莉 (seg_xn2)
    宏在【莉莉变奏入场、HUD 回来】时返回, 循环轴从她的 E 开始。

    ================= 5R 为什么这么打 =================
    - 【一圈被莉莉 R 卡住】演示圈莉莉第二次上场时 R 还差 3.6s, 织梦重击+终章打完正好转好; R2 到 R2 25.3s(游戏时间),
      矩阵 120s 里 5 发万阙垂天。所以每一段都不能拖, 莉莉 R 没好照旧边 A 边等(wait_r_lit)。
    - 【启动轴莉莉先 R】R 给摇篮曲(25s): 之后每个队友变奏入场叠一层攻击 buff。R 演完人在梦乡里(普攻/E/声骸都没了),
      闪避把她拉出来(摇篮曲留着), 再正常 2A-E-Q-重击。循环轴的 R 在莉莉段最后, 离场后留在场上, 下一次永眠花开结束梦乡。
    - 【锁暝第一次上场 2A-闪-2A-Q】攒妄执: 闪避把连段拉回 A1(A1A2 攒得最快), 第二个 A2 转伞时 Q(带轻云套的话早了缺一丝协奏)。
      攒到 ~600, 莉莉变奏进来的碎霆 +200 正好满, 一进场就能 E 无舍念。
    - 【莉莉 E 隔圈换重击】E 一次换一种(魇影/童话), ly_heavy 两种都认。四星协奏武器: 武器的协奏被动第一个循环还没转好,
      E 挪到莉莉第二次上场(R5_LY_4STAR)。

    ================= 12W 轴的形状 =================
    启动轴: 莉 2A -> A2 往后滑的时候 E -> 心(普通) 2A -> E -> 莉(普通) 按住普攻: 织梦重击 -> 终章 -> Q -> R (seg_ly_open)
    循环轴: 莉(变奏·永眠花开) E -> 心(普通) 2A -> E -> 莉(普通) 按住: 织梦重击 -> 终章 -> Q -> R(没好边 A 边等) (seg_ly_loop)
    之后两条一样:
      锁暝 (变奏·闪裂, 念起 -> 念深) [R 在 CD 上就边 A 边等] R -> 演完 -> 点一下 A -> Q -> 【同奏】切心 (seg_lq_so_a)
      心   (变奏·应世相) 点 A -> 双手交叉那下闪避 -> [花图标亮了] 按住出步红尘 -> Q -> 连按 R 转相 -> 演完照世相 E
           -> 【同奏】切锁暝 (seg_xn1)
      锁暝 (旋雷破·同奏 +200 妄执) 点 A -> 侧闪(Boss 能处决就换成处决) -> 点 A 到妄执满 -> E(无舍念) -> 【按住普攻】刻心念 -> 协奏满变奏心 (seg_so_b)
           【协奏不满不切】: 接着 A, 妄执满了再出无舍念/刻心念, 光圈出来才变奏心 (so_ensure_intro)
      心   (变奏·照世相, 统御众机) [锁暝没处决上、能处决就先处决] A 到第 1 颗雕像 -> 接着点到第 2 颗 -> 闪避
           -> A 到第 7 颗 -> 按住(临寰宇) -> 万阙垂天 -> 协奏满变奏莉莉 (seg_xn2)
    宏在【莉莉变奏入场、HUD 回来】时返回, 下一次框架调的就是她(循环轴从她的 E 开始)。返回点不落在任何演出里。

    ================= 12W 为什么这么打 =================
    - 【一圈被 25s CD 卡住】三个人的 R 和心的转相都是 25s 冷却; 视频循环里莉莉终章打完 R 还差零点几秒 ->
      莉莉 R 没亮就边 A 边等(wait_r_lit), 锁暝 R 同样(so_r_asap)。
    - 【莉莉的梦香】上限 300: 普攻回一点、E +130、永眠花开(变奏) +130。满了按住普攻 = 织梦重击(引导, 切走就断),
      引导攒满铃音自动接终章(不能切人); 终章给延奏 buff。所以莉莉在心那 2A 之后切回来, 一按住就是织梦重击。
      R 进梦乡: 普攻/E/声骸都没了, 下一次变奏入场(永眠花开)才结束梦乡 —— 所以 Q 放在 R 前, R 演完直接走。
    - 【莉莉 -> 锁暝 要真的变奏】锁暝变奏·闪裂才进念深, 念深才解锁锁暝的 R; R 演完人在梦乡里打不了普攻,
      协奏只能在 R 之前补: 放 R 前协奏不到 LY_PRE_R_CON 就点 A 补(R 自己给 20)。
    - 【同奏串】锁暝 R 给自己 5s 同奏 -> 带同奏切心 = 变奏·应世相; 心放过变奏·应世相, 下一次转相给同奏 -> 锁暝旋雷破·同奏
      (念深 +200 妄执)。【心要共鸣模态·同奏】(电磁模态没有这两样)。
    - 【锁b 中间的侧闪】闪避把解伞形连段拉回 A1(A1A2 攒妄执最快); 往后闪会离开怪, 视频是按住 D 闪。
    - 【刻心念按住】(用户 11-19 选的长按): 按住普攻一直转陀螺, 转圈是时停、倒计时不走, 松手才收招 +40 协奏。
    - 【心2 2A-闪-3A】照抄心锁守 V1 的统御众机闪法: 第 2 颗雕像(A2 楼阁打中)就闪, 闪避不打断已经出手的楼阁。

    ================= 判据 =================
    - 心(应世心条 / 花图标 / 转相 / 万阙垂天 / 雕像)、锁暝(妄执满 = suoming_forte_e, R 亮)、头像光圈: 照抄心锁守 v2.0.0,
      那边 2K/1080 实机验过。协奏满 = 光圈 + 协奏环互相确认(con_glow, 用户 10-28 定的通用规则)。
    - 莉莉: 梦香满 = 重击位满的提示(is_mouse_forte_full)或回路满; 引导中 = 框架 Lily.heavy_in_progress(梦香提示没了、
      花还在); 引导结束(花没了) = 终章出手, 松手。R 亮 = 框架 Lily.liberation_available(lily_liberation 模板, 梦乡里不算)。
      这几个是框架原版莉莉的判据, 【这条轴第一次用, 未实机】, 读不到都有固定时长兜底。

    ================= 改这个文件之前先读 =================
    - 引擎那一半(macro_* / quick_switch / press_key_until_cd / fire_r / 光圈 ...)、心 xn_*、锁暝 so_* 是从
      心锁守 v2.0.0 的 Hsin.py 按方法名原样抽出来的, LQ_ 开头的常量也是那边(心锁守轮切轴)的。
    - 【自定义 __init__ 里跟内置版不同的字段会被打回内置值】, 要保住就在 reset_state() 里重申。
    - 普攻一律走 tap()(按住 A_TAP 秒): task.click() 只按 10ms, 打不出来。
    - 长按期间不调 click()/mouse_down()/send_key() —— 它们先投 WM_ACTIVATE, 会把按住打断。
    - 椰果版和原版 OKWW 都能跑(监视线程优先用椰果版主程序的 src.combat.AxisWatch, 原版没有就用文件里自带的一份)。
    """

    # ==================== 选轴 ====================
    # '5R' = 2心锁莉-5R进阶轴(默认) / '12W' = 12w 轮椅轴(v0.1.3 原样, 备份在 team_backup/2026-11-19_..._v0.1.3_12w)
    AXIS_KIND = '5R'

    # ==================== 全局节奏(引擎用) ====================
    A_INTERVAL = 0.12
    KEY_INTERVAL = 0.10
    A_TAP = 0.06                 # 普攻按住这么久才算一下; task.click() 的 10ms 会被吞
    SI_KEY_TAP = 0.03            # SendInput 点技能键按住多久

    # ---- 切人 ----
    SWITCH_TIME_OUT = 2.5
    SWITCH_KEY_DOWN_TIME = 0.02
    SWITCH_KEY_INTERVAL = 0.0
    UNTRUSTED_CONFIRM = 3
    TRACE_SWITCH = True
    # 宏表里的切人格: 在这一格自己的 gap 里盯到场(正常 0.1s 就到, 剩下的照等, 时序不变);
    # 没到就每 SWITCH_REPRESS 补按, 最多再多等 SWITCH_EXTRA(游戏的换人 CD 约 1 秒)
    SWITCH_EXTRA = 1.50
    SWITCH_REPRESS = 0.05
    SWITCH_CONFIRM = 2

    # ---- 大招 / 演出 ----
    LIB_KEY_INTERVAL = 0.03
    LIB_ANIM_TIME_OUT = 8.0
    HUD_NO_ANIM_GRACE = 0.35
    LIB_GONE_CONFIRM = 3         # HUD 连续这么多帧不在 = 演出真的开始了
    HUD_UP_CAP = 1.0             # 按 R 之前等 HUD 在场的上限(防假成功)
    # 【演出中途 HUD 会闪一两帧】(录像: 心转相 0.55s / 3.6s 处、守R 1.9s 处各闪一次)。等"HUD 回来"时
    # 要连读到这么多帧才算, 而且演出开始后不到 min_gone 秒的"回来"一律不认(见 wait_hud_back)
    HUD_BACK_CONFIRM = 2

    # ---- 杂项 ----
    CON_SAMPLE_MAX = 24
    FIELD_SETTLE_TIME_OUT = 1.5
    E_RESEND_STEP = 0.06
    SKILL_BAR_SETTLE = 0.40      # 刚切进来多久没见过技能亮就当它在 CD 上

    # ==================== 防重入 ====================
    OPENER_MIN_GAP = 60.0

    # ==================== 保底 ====================
    # 原则(秧千穗那次定下的): 只花时间的保底默认开(切人重试 / 等 HUD / 判据没成立就再按【那一个本该出去的键】);
    # 会改状态的只在判据明确说"这一下没出去"时才补, 不凭猜测多按
    SWITCH_RETRIES = 2           # 切人超时后先等 HUD 回来再切, 最多再试这么多次(被击飞/硬直时游戏不放人)
    SWITCH_RETRY_TIME_OUT = 3.00
    HUD_LOST_ABORT = 8.00        # 队伍 HUD 连续这么久没见过 = 关卡结束/过场/窗口没了, 整圈放弃(最长的演出 4.3s)
    DRIVE_HUD_CAP = 1.50         # 框架调进来时 HUD 这么久都不在就什么都不做(结算画面上框架还会一直调进来)
    R_TRIES = 3                  # R 亮着但没放出去(被吞/被打断)就再按, 最多这么多次
    R_BLIND_EVERY = 3            # R 读成暗时每这么多轮攒能量盲按一次(防读数不准)
    R_BLIND_WINDOW = 0.40
    R_BUILD_SLICE = 0.60         # 攒能量每轮的连点时长
    ROWS_HUD_READ_EVERY = 2.0    # 宏表回放时至少这么久读一次 HUD(喂看门狗), 只在格子空档里读, 不推后下一格
    # 【协奏真满看头像光圈】协奏满(切人能变奏)那一帧, 另外两个人的头像外面套上一圈细白环往外扩。
    # 框架的协奏环读数快满就报 1.00(开局守比真满早 1.8s 就读到), 光圈跟真满同一帧出来。
    # 只看另外两格: 刚切进来的人自己那格还残留一圈。读法: 头像中心极坐标展开, 只取上半圈(避开数字牌/名字条/属性图标),
    # 半径 80~118(4K)里找白色细脊, 统计落在同一半径上的角度占比。
    # 10-24 录屏 17.55.06 离线(2K + 缩到 1080): 开局守真满那帧两格都 ~0.9, 之前 <= 0.13; 单格误读最高 0.34;
    # 亮背景会把一格压到 0.1~0.3, 所以两格合起来判。1080 下心万阙垂天后那次只有 0.11/0.12(认不出就走读数老路)。
    GLOW_BOTH = 0.15             # 两格都 >= 这个, 而且有一格 >= GLOW_ONE
    GLOW_ONE = 0.35
    GLOW_SOLO = 0.60             # 一格就够
    GLOW_CONFIRM = 2
    # 【协奏满 = 光圈 + 协奏环互相确认】(用户 10-28 定的通用规则, 照爱琳莫 Linnai.con_both):
    # 光圈连 GLOW_CONFIRM 帧, 而且环在最近 RING_WINDOW 秒里读到过 >= RING_HI(0.99 是环快满时的哨兵值, 也算)。
    # 不要求同一帧: 特效会把环读成 0。光圈本局一次都没认出来过(分辨率/缩放不对)的地方照旧只看读数
    RING_HI = 0.99
    RING_WINDOW = 1.00

    # ==================== 协奏读数 ====================
    SK_CON_ENOUGH = 1.0          # 0.99 是环没闭合的哨兵值, 只看读数时要真满
    SK_CON_FULL_CONFIRM = 3
    SK_CON_POLL = 0.03

    # ==================== 处决(判据照抄心锁守 / 嘉露莫) ====================
    # 只认怪身上的 F 提示(屏幕中间 f_break >= 0.90, 不是拾取 F); 不走框架的 task.f_break(): 那个会连点左键、至少卡 0.5s
    BREAK_READS = 3
    BREAK_CENTER_MIN = 0.90
    BREAK_RING_MIN = 0.92        # 框架 check_f_break 同一个阈值
    BREAK_RESEND = 0.30          # 按完这么久提示还在 = 被吞了(硬直/上一个动作没收), 再按
    BREAK_PRESSES = 5
    BREAK_GONE_FRAMES = 3        # 提示单帧会掉到 0.85~0.88(特效闪), 连这么多帧不见才算处决起手了
    # 【处决落点】处决能控住 Boss, 放在最容易被打断的段前面, 而且不能打断流程。
    # 11-19 实机 5 局 17 圈一次没处决: 唯一一处在万阙垂天之后、只读 3 帧中间提示, 一次都没读到;
    # 而飞廉之猩的外圈从循环第 1 圈锁b 满了以后一直满到它死(90 多秒)。被打断最多的是锁b: A 到妄执满 12 圈里 6 圈
    # 慢 0.5~3.3s, 两次散掉多 6.5s / 8.7s; 其次心2 临寰宇被打断 1 次(这一圈 R2 没放)。
    # ① 锁b 侧闪那一刻: 侧闪只为把连段拉回 A1, 处决完也从 A1 起, 处决顶替侧闪, 控住的时间正好盖住 A 到妄执满、无舍念、刻心念
    # ② 锁b 没处决上(外圈还没满 / 妄执在侧闪前就满了): 心2 变奏入场能 A 的那一刻、第一下 A 之前
    # 万阙垂天之后那一处关掉(XN2_BREAK): 外圈打满要 ~35s, 在那儿用掉下一圈锁b 就没得用。
    # 判据: 顶部 Boss 头像外圈满(f_break_full, 不跟镜头走)连 2 帧 + 全宽读到中间提示, 才按 F; HUD 连 2 帧不在 = 处决演出开始了。
    # 只看外圈不行: 11-19 14:12 逐帧扫, 飞廉之猩外圈 84s 满、中间提示 93s 才出(这段按 F 是空按); 之后两样基本同时在,
    # 只有锁暝转刻心念、大招演出、刚变奏入场时提示被特效/镜头挡掉。没提示就照原流程(锁b 侧闪)
    SO_B_BREAK = True
    XN2_ENTRY_BREAK = True
    BREAK_RING_FRAMES = 2
    BREAK_PROMPT_READS = 3       # 全宽 ~50ms 一次, 读这么多帧还没有就不按
    BREAK_F_TRIES = 3
    BREAK_F_EVERY = 0.30         # 按完这么久 HUD 还在 = 被吞了(上一下普攻还没收招), 再按
    BREAK_HUD_GONE = 2
    # 【矩阵里处决不藏 HUD】11-19 18:53 实机 4 圈锁0: 第一下 F 就处决上了(倒计时停 ~1.4s), HUD 一直在,
    # 旧判据(HUD 消失)记成"没出", 又按了闪避和 2A —— 全落在处决演出里被吞, 锁暝出来只剩 ~1s 普攻, 第 3 圈锁b 妄执没满补了 3.4s。
    # 现在: HUD 在、Boss 外圈连 BREAK_RING_OFF 帧不满 = 处决演完了(录屏: 外圈在按 F 后 1.37s 清空, 跟倒计时恢复同一刻)。
    # 按了 F 过 BREAK_NOHUD_CAP 外圈还满 = 没处决上
    BREAK_RING_OFF = 2
    BREAK_NOHUD_CAP = 2.20
    BREAK_ANIM_MIN = 0.30        # 演出中途 HUD 会闪一两帧, 消失这么久之内的"回来"不认
    BREAK_ANIM_CAP = 4.00        # 处决演出等 HUD 回来的上限(心/锁暝的处决演出都没量过, 实机看 `处决演出(ok,anim,..s)`)

    # ==================== 带方向的闪避 ====================
    WD_W_LEAD = 0.05             # 按住方向键 -> 右键 -> 松方向键
    WD_DODGE_HOLD = 0.07
    WD_W_TAIL = 0.05

    # ==================== 心 ====================
    # ---- 应世心条(4K 坐标, hcenter): 回路条中间徽记左右两半的紫色格子 ----
    # 读数 = 有紫色的列占比(HSV 色相 125~160、饱和度>=70、亮度>=90)。第一版用 RGB 阈值, 实机 11:24 那局
    # 背景一暗紫格子跟着暗, 左半满格读成 0.58、右半 0.53 读成 0.31, "到线"一直不成立; HSV 两种场景左半都是 0.99
    XN_GAUGE_L = (1627, 1999, 1837, 2011)
    XN_GAUGE_R = (1965, 1999, 2175, 2011)
    XN_GAUGE_VALID = 0.50        # 左半 >= 这个才算"在应世相、读数可信"(变奏入场之后左半就是满的)
    XN_YING_READY = 0.70         # 右半到这么多就按重击(作者宏/手打按下去那一刻都是 0.73, 0.3s 内出步红尘)
    XN_YING_LATE = 0.58          # 到了 XN1_LATE_AT 右半 >= 这个也按 —— 差的那截普通重击能补上
    XN_CONSUMED = 0.15           # 按住之后左半掉到这个以下 = 整条清空 = 步红尘出手
    # 【应世心满 = 重击位出现 hsin_h1(花图标)】。满的时候条变成粉红高柱(紫色判据读成 0.4 上下, 不能用),
    # 实机录屏满的那几秒模板 0.96~0.99、步红尘一出手当帧 0; 作者两份录像从没打满过, 一帧都不中。2K/1080p 一样
    XN_FULL_THRESHOLD = 0.70
    # 心两段长按走 SendInput(游戏在前台时): PostMessage 的按下沿落在普攻收招里会被丢掉, 后面一直按着也不算数
    XN_HOLD_SENDINPUT = True

    # ---- 插段(莉莉 E 之后切过来, 普通入场·应世相): 2A -> E -> 切回莉莉 ----
    # 视频(1 倍速演示 v172.1~173.6): 到场就点, 点 1.0s(两段) -> E -> 切莉莉, 在场 1.5s。这两下垫应世心, E 回能量
    XN0_A_LEAD = 0.05            # 到场 -> 第一下 A
    XN0_A_UNTIL = 1.05           # 从第一下 A 起点到这一刻
    XN0_A_EVERY = 0.17
    XN0_E = True
    XN0_E_CAP = 1.00             # 按到应世相 E 进 CD 的上限(冷却 12s, 一圈只在这里用; 垫应世心点到 A3/A4, 收招要盖过去)
    # 【垫应世心】0 链循环心1 变奏入场只到 ~3/4 条, 要在心1 里 A-闪-A 再攒才亮花; 心1 在莉莉 R 之后, 攒的时间全算进
    # 莉 R -> 心 R2 的窗口。心0 先垫一截, 把这段挪到莉莉 R 之前。
    # 11-19 实机: 同一只 Boss(飞廉之猩)上, 莉 R -> 心 R2 游戏时间 22.0~24.4s 七次全是 297.2w, 25.6s / 28.4s 287.0w,
    # 29.4s 266.2w —— 25s 左右有个 buff 到期。不垫 23.3~24.4s(贴着边, 慢一点就掉), 垫了 22.0~23.0s(心1 短 ~1.2s)。
    # 启动轴/循环/达妮娅之间的大档(346.7w / 297.2w)是三只 Boss 不一样, 跟这个无关。
    # 实测左半只到 0.39~0.63、每圈都点满 3s cap; 一圈总时长不变(时间从心1 挪到心0)。
    XN0_FILL = True
    XN0_FILL_L = 0.80            # 左半连 2 帧到这么多(= 整条 40%)就 E 走; 心1 变奏入场 +~65% 正好满
    XN0_FILL_CAP = 3.00          # 从第一下 A 起到这一刻还不够: 照常 E 走(心1 进场照旧闪避补)

    # ---- 第一段(锁暝 R 的同奏 -> 变奏·应世相): A -> 闪避 -> 按住步红尘 -> Q -> 转相 -> 照世相 E -> 同奏切锁暝 ----
    # 0 链(用户 11-19: 先按 0 链做): 进场应世心 = 变奏 60 + 剩的, 视频到场就点 A、到场 +1.06s(心双手交叉那下)闪避、
    # 闪避后 0.14s 按住 0.8s 出步红尘。闪避把连段拉回去, 按住接的那一下补满最后一截。
    # 【只认花图标】(心锁守 10-24 实机): 条读到 0.84 就按住出的是普通重击, 满之前按下的那次按住不会变成步红尘。
    # 所以: 到场就亮(2 链开局 / 上一圈垫满了) -> 不点不闪, XN1_FULL_HOLD_AT 直接按住; 闪避前亮了 -> 不闪直接按住;
    # 闪避后亮了(连 2 帧) -> 按住; 闪避后 XN1_POST_DODGE_CAP 还不亮照按(按成普通重击由 xn_hold_burn 补 2A 重按)
    XN1_FULL_FROM = 0.20         # 从切人键起这一刻开始读花图标(刚切进来那几帧技能栏还是锁暝的)
    XN1_FULL_HOLD_AT = 0.50      # 上场就满: 从切人键起这一刻按住
    XN1_TAP_FROM = 0.40          # 从到场起点 A(变奏入场那一刀 ~0.66s 收完, 里面点的被吞, 不要紧)
    XN1_TAP_EVERY = 0.25
    XN1_A_HOLD = 0.08
    XN1_DODGE_AT = 1.05          # 从到场起这一刻闪避(视频 1.06s)
    XN1_DODGE_TO_A = 0.12        # 闪避后花还没亮: 这么久后开始点 A
    XN1_POST_DODGE_CAP = 2.00    # 闪避后点 A 等花图标的上限, 到了照按
    XN1_POST_DODGE_BLIND = 1.00  # 闪避后这么久条一次都没读出来(读不到应世心): 不猜, 照按
    XN1_FILL_EVERY = 0.40        # 重按前补 A 的间隔(要接得上连段)
    # 声骸在步红尘之后、转相之前(视频"接着释放声骸, 然后大招变身"): 松手时离转相解锁还有 0.4~0.9s, 按 Q 不耽误转相
    XN1_ECHO = True
    XN1_ECHO_CAP = 0.80
    # 转相演完按一下照世相 E 再切: 照世相 E 和应世相 E 冷却分开算, 不记 last_res(心0 的应世相 E / 补协奏看的是它)
    LQ_XN1_E = True
    LQ_XN1_E_CAP = 1.20
    XN1_HOLD = 1.80              # 按住时长(宏表)。步红尘免疫打断, 演出里一直按着没有坏处
    XN1_RETRIES = 2              # 按满 XN1_HOLD 条都没清空(= 出的是普通重击) -> 补两下 A 再按, 最多这么多次
    XN1_RETRY_A = 2
    # 【按住期间条才满 = 这一下多半已经是普通重击, 满了也不出步红尘】10-24 第三次实机 L3: 怪死换波打空, 0.66 就按住,
    # 按下后 0.89s 花图标亮、一直到 1.8s 松手都没出, 又补 2A 重按才出(多花 ~2.8s)。正常按住到出手 0.34~0.81s。
    # 现在: 按下够 XN1_STALE_MIN 且花图标亮了 XN1_STALE_FULL 还没清空 -> 立刻松开重按(不补 A)
    # 按下那一帧花图标没亮、按住期间才亮(连 2 帧) -> 不等, 立刻松开重按(18:29 录屏亮了 0.4s 才撞到 1.8s 松手)
    XN1_STALE_MIN = 0.95
    XN1_STALE_FULL = 0.45
    XN1_STALE_MAX = 1
    # 转相(R1)解锁 = 应世心条清空(步红尘出手) + 1.86~2.06s(10-24 实机)。
    # 【不认转相图标, 步红尘松手就连按 R 到演出出来】(用户 10-24 定的): 应世相里转相没解锁时按 R 什么都不发生,
    # 连按能踩上解锁那一帧; 认图标那条路偶尔读漏(没看到清空 -> 等到按住后 2.6~3.0s 才按)。
    # 窗口从松手(按住 XN1_HOLD 后)起算: 松手时离清空 1.1~1.5s, 还差 0.4~0.9s 解锁
    XN1_R1_SPAM = 2.50
    # 补救(xn_r1_rescue)里还在用: 清空后等解锁 / 连读几帧亮才按
    XN1_R1_UNLOCK = 2.06
    XN1_R1_MARGIN = 0.08
    XN1_R1_READS = 2
    XN_R_WINDOW = 1.50           # R 连按到 HUD 消失的窗口
    # 【转相一定要放出来】(心汐守用户 10-05 定的): 锁暝响应同奏进念深(才有 R)、心第二段的变奏·照世相全靠它。
    # 第一次没出就补到出为止: 转相亮 -> 按; 应世心满 -> 再按住出步红尘、等解锁; 都不是 -> 普攻攒应世心
    XN1_R1_RESCUE_CAP = 8.00
    XN1_R1_RESCUE_A = 0.80       # 攒应世心一轮的普攻时长
    XN1_R1_LOST_ROUNDS = 3       # 连着这么多轮既看不到转相图标也读不到应世心条 = 已经不在应世相(转相其实出了)
    XN_R_ANIM_MIN = 3.90         # 转相/万阙垂天演出藏 HUD 4.3s, 中途 0.55s / 3.6s 处 HUD 会闪一帧
    XN_R_ANIM_CAP = 7.00

    # ---- 第二段(变奏·照世相, 直接进统御众机 + 300照世心): 2A -> 闪 -> 3A -> 按住到临寰宇 -> Q -> 万阙垂天 ----
    # 视频: 切人后 2.45s 第一下 A(变奏入场 + 楼阁升起那段打不了), 第 1 颗翻完闪避, 第 8 颗翻面时临寰宇已经出手。
    # 心汐守那版是 4A 再按住(A3/A4 吃雕像慢), 从统御众机到临寰宇 5.9s, 视频 ~2.4s 快一大截
    XN2_LEAD = 2.50
    # 每段普攻的楼阁一段翻 2 颗(A1 1 颗, A2 #2#3, A3 #4#5, A4 #6#7), 闪避不打断已经出手的楼阁。
    # 【闪避在 A2 楼阁落下那一下(第 2 颗), 不早不晚】
    # v0.2.3 数到 3 才闪(A2 打完): 闪避收招单独占 0.66s, #3->#4 0.78s(教学3 0.15s), #1 -> #7 2.42s(教学 2.04s)。
    # v0.2.4 读到第 1 颗 0.05s 后就闪: 18:57 录屏 A2 还没出手就被闪避吞了, 闪后从 A2 重打, #1->#2 0.75s(平时 0.46~0.57),
    # #1 -> #7 2.55s 反而更慢。教学3 #1->#2 0.50s 跟不闪一样快, #2->#3 0.36s, #3->#4 0.15s = 闪在 A2 楼阁落下前后。
    # 现在: 读到第 1 颗后接着点 A, 读到第 2 颗(A2 楼阁打中)或第 1 颗后 XN2_DODGE_AFTER 就闪
    XN2_DODGE_COUNT = 2
    XN2_DODGE_AFTER = 0.45       # 18:44 四圈 #1->#2 0.46~0.57s, 第 2 颗读不到也在它前后闪(A2 已经出手)
    XN2_DODGE_MIN = 1.00         # 从第一下 A 起这么久之前不认第 1 颗(入场闪光会让几颗雕像一起"亮", 防误读)
    # 18:44 四圈第 1 颗都在第一下 A 后 1.06~1.11s 翻(前 ~0.6s 的 A 落在变奏入场里被吞)。读不到第 1 颗就到这一刻闪
    # (正常这时候 A2 刚好落下), 别拉太晚: 晚到 A2 打完就回到 v0.2.3(闪避收招单独占一段)
    XN2_DODGE_CAP = 1.60
    XN2_DODGE_HOLD = 0.07        # 闪避 = 右键
    # 0.12 -> 0.03, 闪避后普攻 0.18 -> 0.10 一下 (10-24 第二次实机: 闪避后第 5 颗要 0.70s 才翻, 视频 0.30s;
    # 最后一颗 +1.82s vs 视频 ~+1.1s。闪避收招里按的被吞、不排队, 点得密一点才能踩上第一个能接的帧)
    XN2_DODGE_TO_A = 0.03
    XN2_POST_DODGE_A_EVERY = 0.10
    # 2.00 -> 2.80: 闪避提前到 #2, 闪后要翻 #3~#7; 撞上限早按住会剩几颗不翻(见 XN2_HOLD_AT)
    XN2_EMPTY_CAP = 2.80         # 闪避之后普攻到 XN2_HOLD_AT 颗 / 人像出来的上限, 到了照按
    # 【第 7 颗翻完就按住(预输入), 不等第 8 颗】教学视频第 8 颗翻面到临寰宇出手 < 0.1s(按住早就开始了);
    # 我们等第 8 颗读到再按, 按住到出手还要 0.39s, #7 -> 临寰宇 0.9s vs 教学 0.5s。
    # 10-24 第四次实机在第 5 颗就按住: 按住期间已经排队的楼阁照样翻了 #5~#7, 但那圈闪避切断了 A3, 链打到头还剩
    # #8, 心停手 3s 不出 —— 按住只接着打已经出手的那段, 剩下的不会自己补。所以只剩最后 1 颗才按, 再加 XN2_HOLD_STALL 兜底
    XN2_HOLD_AT = 7
    # 按住后雕像这么久没再翻、也没出手 = 链打到头了还没打空(或判据漏读), 松开回普攻补到人像再按(原来干等满 3s)
    XN2_HOLD_STALL = 1.00
    # 雕像坐标/阈值照抄心雷穗 v1.1.0(2K/1080 实机验过): 4K x, 下半红菱形 y / 上半亮白 y, 半宽
    XN_STATUE_XS = (1647, 1719, 1794, 1866, 1938, 2013, 2085, 2160)
    XN_STATUE_Y = 1980
    XN_STATUE_UP_Y = 1953
    XN_STATUE_HW = 9
    XN_STATUE_RED = 0.08         # 红菱形占比(没翻 0.00, 翻了 0.10~0.55)
    XN_STATUE_GLOW = 0.30        # 上半身亮白占比(没翻 <= 0.12, 翻面那一刻 0.45~0.61, 翻完常亮 0.14~0.5)
    XN_STATUE_CONFIRM = 2
    # 同一帧新亮 >= 这么多颗 = 闪光(心汐守 10-07: 同奏入场闪光让几颗雕像一起亮), 这一帧不算
    XN_STATUE_BURST = 3
    XN2_HOLD_CAP = 3.00
    XN2_READY_REPRESS = 0.60     # 按住时人像出来这么久还没出手就松开重按(人像出来再按住 0.40~0.45s 出手)
    XN2_REPRESS_MAX = 2
    XN2_TY_A_EVERY = 0.18
    XN2_READY_CONFIRM = 2
    XN_READY_MIN = 0.80
    XN_READY_GLYPH = (
        'AAAAAAAGGDxjHgwAAAAAAAAAABQoWcH31Vg3Jg0AAAAAACGmh+r9+X82NzlcMgAAAAZ47tD9/c06R2NGZ2cWAAA32P38/f2+N0xtaDY3MQIa'
        '3/v9/v3x4DhNbFk2OEcVYf39/f34yMM4RGJWQjmCKmH9+vW6sa2FPT09WGFLyjZ5/c7E4fWaQD0/UHOX0POeYfmXotjqXz09PkBvmojMUTfc'
        'vZnUhD08PDw9mcfbvzMBauG+pJZJREA9P9L05XAFACb1+/38refMZD3D+oQjAAAAbfn9/tDY/LQ7tOMxAgAAAABU2f7is/y+QMd0AgAAAAAA'
        'AAhcpG3GgygPAAAAAA=='
    )
    XN_TY_MIN = 0.60             # 统御众机雕像 0.71~1.00 / 其他 <= 0.38
    XN_TY_GLYPH = (
        'AAAAAAAMJCEsJBYBAAAAAAAAABiR1cJ40tzbpyYAAAAAABK+4+Pt2unk48BaJAAAAAhQg6aMnN1/l6RzUVEbAAAzV3Dw9crHyvngXlFQRQIJ'
        'VFqs8dbtovLV8JdRUFAdIlmK7ZPT7IT7v6Dtbk9bOzdhj5en/O2E/PmHoXdelTpIsV1Xrv3thPz7iE9XwJuUJsCvW6797YX9+4dTsfjxriWy'
        '9KGQ++2E/fqGnvfU33sBpvjsmt/thPzfmeylk9cPADOnpLzM7IT4tc54Y+FhAAAASGKt+eyD5uiKUbyiBQAAAAAkjf3tg/j6h3aCCQAAAAAA'
        'AAZbpFvGrUwKAAAAAA=='
    )
    XN_TY_SLOT = (2826, 1893, 82, 79)   # 重击位(4K, 跟 hsin_h1 的标注框一样)
    XN2_READY_HOLD = 1.20        # 补救时人像出来后按住到 HUD 消失的上限(实测 0.40~0.45s 出手)
    XN2_BLIND_HOLD = 2.00        # 补救时没看到人像(撞上限/没进统御众机)按住试一次的上限
    XN2_RELEASE_AFTER_GONE = 0.30
    XN2_ANIM_MIN = 1.40          # 临寰宇藏 HUD ~2.0s
    XN2_ANIM_CAP = 4.00
    # 按住没出(被打断 / 人像误读) -> 普攻到人像再按, 最多这么多次。
    # 心没拿到变奏(锁暝没带同奏/协奏没满)时没进统御众机, 第一次补救前先按一下 E(满 300 照世心是万阙连衡, 不满也给照世心)
    XN2_RETRIES = 2
    XN2_RETRY_CAP = 1.50         # 补的那一轮普攻到人像的上限(同奏下人像不一定认得到, 到点照按)
    # 1.20 -> 0.30: 临寰宇演完 HUD 回来后不点普攻干看 hsin_lib2 的上限。10-24 实机认出来都在 0.05~0.24s,
    # 过了这个点还没认出 = 能量不够, 马上边点普攻边看(下面 XN2_R2_BUILD_CAP), 不再干站 1.2s
    XN2_LIB2_CAP = 0.30
    # 【万阙垂天图标没认出 = 大招能量不够, 先攒能量, 不照按】统御众机那段普攻没打中怪, 能量不满, R 位不是万阙垂天:
    # 10-07 14:16 L1 照按 R 连发 30 下没放出来; 心卜穗 10-06 09:31 照按放成了借相(R 进 1.5s 冷却, 心切回应世相)。
    # 现在照世相普攻攒, 每帧看 hsin_lib2, 认出来再放; 撞上限就这一轮不放
    XN2_R2_BUILD_CAP = 5.00
    # 2 -> 1: hsin_lib2 在临寰宇之前一帧都不中(0.95+ / 之前 <0.7), 读到一帧就按, 省一帧
    XN2_LIB2_READS = 1
    # 【万阙垂天要认图标】v0.2.6 改成临寰宇后盲按, v0.2.8 用户要回来: 伤害丢失(统御众机普攻没打中)时能量不满,
    # 盲按会放成借相、心切回应世相, 这一轮就没万阙垂天了。True = 盲按(认不出才认图标重按)
    XN2_R2_BLIND = False
    # 万阙垂天演完 -> 切莉莉之前协奏要真满(莉莉要变奏入场: 永眠花开 +130 梦香, 一进场 E 就能凑满织梦重击)。
    # 不满就补: 心的应世相 E(心0 用过, 离这里 ~15s, 冷却 12s 早好了) -> 普攻, 每下之后读一次
    XN2_CON_READ = 0.20
    XN2_CON_CAP = 1.50
    XN_E_CD = 12.0               # 共鸣技能·应世相冷却
    XN2_ECHO = False             # 心的声骸在心1 步红尘之后放, 这里不放
    # 万阙垂天之后视频不重击也不 E, 白屏时停一完就变奏莉莉(流程图"心 2A-闪-3A-重击 R")。开了 = 时停后按一下 E 偷伤害
    XN2_E_AFTER_R2 = False
    XN2_E_CAP = 1.20             # 万阙垂天收招里按的 E 会被吞(心锁守 10-24 实机), 按到进 CD 的上限
    # 【处决】视频: 有处决提示可以在大招后补一下普攻 + 处决。HUD 回来就读 BREAK_READS 帧提示, 没有就不等;
    # 有: 等白屏时停(HUD 回来后 ~0.60s, 心锁守 19:06/教学3 录屏量的)过去, 点一下 A, 再按 F 到提示消失
    XN2_BREAK = False            # 关: 处决挪到锁b 侧闪 / 心2 入场(见上面【处决落点】)
    XN2_R2_FREEZE = 0.60
    XN_BREAK_A_TO_F = 0.15
    XN_BREAK_ANIM = 1.50         # 处决动画(没量过心的, 照守岸人谐度破坏 90F), 等完再等 HUD 回来

    # ==================== 锁暝(LQ_ 开头的是心锁守轮切轴那几段, 原样用) ====================
    SO_A_EVERY = 0.20            # 普攻点按间隔(动作里的点击被丢掉)
    # ---- 第一次上场(莉莉变奏进来 = 变奏·闪裂, 念起 -> 念深): R -> 演完 -> 点一下 A -> Q -> 同奏切心 ----
    # 视频 1 倍速: R 演完 A ~0.5s(游戏时间) -> Q -> 切心
    SO_R_LEAD = 0.25             # 切人键 -> 开始按 R(变奏入场的锁里按的被吞)
    SO_R_ANIM_MIN = 2.50         # 锁暝大招藏 HUD(实机 4.9s)
    SO_R_ANIM_CAP = 8.00
    LQ_SO_A_TAPS = 1             # R 演完点几下普攻再放 Q
    # R 没好(CD / 能量不够)不站着等(so_r_asap): 一直点 A, 每下 A 后顺手按一下 R(没好按了没反应), 读到亮再连按一小段。
    # 旧版读到亮就连按 2.5s, 能量差一点图标也有白色读成亮 -> 锁暝站着按 2.5s(心锁守 10-25 18:25 L2 R_NOFIRE x50)
    LQ_SO_R_CAP = 9.00           # 放 R 的总上限
    LQ_SO_R_ENTRY = 1.70         # 切人键起这么久是闪裂入场动作(实机 R 在切人后 ~1.55s 才出得来): 读到亮就一直按 R
    LQ_SO_R_BURST = 0.30         # 入场之后读到亮: 连按 R 这么久, HUD 没消失 = 其实还没好
    LQ_SO_R_BACKOFF = 0.40       # 第 n 次连按没出之后 n 倍这么久只点 A(每下 A 后照样按一下 R)
    LQ_SO_R_E_FROM = 1.50        # 没变奏入场(念起, R 不解锁)时: 进场这么久后妄执满就点 E(锁妄念进念深); 变奏入场不点(念深里 E 是无舍念)
    # 声骸一直按到进 CD(R 演完 HUD 回来连按 2 下会被收招吞, 心锁守 10-24 实机)。同奏 5s 按游戏时间算, 多按一会儿不丢变奏
    SO_ECHO_CAP = 1.00
    SO_ECHO_RISE = 1.0           # CD 比按之前高出这么多秒才算放出去
    # ---- 第二次上场(心转相的同奏 -> 旋雷破·同奏, 念深 +200 妄执): A -> 侧闪 -> A 到妄执满 -> 无舍念 -> 按住刻心念 ----
    LQ_SO_B_A_LEAD = 0.10        # 切人键 -> 开始点 A(入场动作里点的被吞, 不要紧)
    # 视频(循环 v150.0~157.5): 到场点 A 1.5s -> 按住 D 右键侧闪(到场 +1.84s) -> 点 A 1.35s -> E x3 -> 刻心念。
    # 妄执在侧闪前就满了(上一圈剩得多)就不闪, 直接 E
    SO_B_DODGE = True
    SO_B_DODGE_AT = 1.85         # 从到场起
    SO_B_DODGE_KEY = 'd'
    SO_B_DODGE_TO_A = 0.10
    # 实机正常要 A x20 4.2~4.3s(心锁守开局), 中间挨一下就补不满 -> 没无舍念整圈散
    LQ_SO_B_FILL_CAP = 7.00
    # 无舍念: E 位是无舍念(suoming_forte_e)就每 SO_KX_E_EVERY 点一下, 读到出了(连 2 帧不是)就停
    SO_KX_E_EVERY = 0.10
    SO_KX_E_TAP = 0.05           # 一下 E 按住这么久(10ms 的会被吞)
    SO_KX_WUSE_CAP = 2.50
    SO_KX_A_FROM = 0.30          # 无舍念出来后这么久按住普攻(刻心念图标在无舍念后 ~0.3s 出来)
    # 【刻心念按住】(用户 11-19 选长按): 按住普攻 = 一直转陀螺, 伤害更高, 转圈是时停、倒计时不走 —— 多花的只是实时。
    # 心锁守 10-25 18:23 实机: 按住不会自己收招, 松手才收招 +40 协奏(光圈在松手后 0.41s 才出), 两次都按满 4.0s 上限。
    # 所以正常就是按满 LQ_SO_KX_HOLD_CAP 松手; 按下那一刻协奏已经满着(光圈一直在)按满 LQ_SO_KX_HOLD_BLIND 松
    LQ_SO_KX_HOLD_MIN = 0.50     # 按住不到这么久的光圈不认(防闪一下)
    LQ_SO_KX_HOLD_CAP = 4.00
    LQ_SO_KX_HOLD_BLIND = 2.40
    # 按住期间 E 位又连 2 帧是无舍念 = 无舍念没放出来(按 E 那一刻被砍中, 心锁守 10-25 18:45 实机):
    # 松开普攻, 连点 E 到放出来, 再重新按住(计时从头算)
    LQ_SO_KX_E_MAX = 3
    LQ_SO_KX_E_RESCUE_CAP = 6.00  # 从第一次按住起过了这么久就不再补
    LQ_SO_KX_FINISH_CAP = 1.00   # 松手后不按键等协奏真满的上限, 没满走 so_ensure_intro
    # 光圈本局认出过: 只有读数满时再给光圈这么久, 还没有就当没满
    SO_KX_GLOW_GRACE = 0.50
    # 【一定要变奏心】(心锁守用户 10-25): 协奏不满不切, 普攻 + 妄执满了再出无舍念/刻心念, 光圈出来才走。
    LQ_SO_B_ENSURE_CAP = 15.00   # 只防打不到怪(Boss 跑开/换波)一直耗着
    LQ_SO_B_KX_MAX = 2           # 这里面最多再出几次无舍念/刻心念
    LQ_SO_B_READ_GRACE = 2.00    # 光圈认得出过时, 读数满了这么久还没光圈也走
    SO_TO_XN_TIME_OUT = 4.00     # 刻心念收招没完游戏不放人

    # ==================== 莉莉 ====================
    LY_A_EVERY = 0.17            # 普攻点按间隔(视频 0.17s 一下)
    # ---- 启动段(普通入场): 2A -> A2 往后滑的时候 E -> 切心 ----
    # 视频: 到场就点, 第一下 A 后 0.87s 按 E(A2 往后滑那下), E 出手 0.13s(游戏时间)就切心
    LY_OPEN_A_LEAD = 0.05        # 到场 -> 第一下 A
    LY_OPEN_E_AT = 0.85          # 从第一下 A 起这一刻开始按 E
    LY_E_CAP = 1.00              # E 按到进 CD 的上限(冷却 13s)
    # ---- 循环段开头(心万阙垂天后变奏入场 = 永眠花开): E -> 切心 ----
    # 视频 1 倍速: 到场 +0.47s 开始连按 E, ~+1.0s 才出手(入场动作里按的被吞), +1.24s 切心
    LY_LOOP_E_LEAD = 0.30        # 从到场起
    LY_LOOP_E_CAP = 1.80
    # ---- 主段(心 2A-E 切回来, 普通入场): 按住普攻 -> 织梦重击(引导) -> 终章 -> Q -> R ----
    # 视频: 心那边按住普攻、按 E、带着按住切莉莉, 到场 ~0.4s 重击就转起来; 按住到松手 3.4~4.4s(游戏时间, 启动轴长、循环短)。
    # 这里不带着按住切人(切人键会把按住打断): 到场读梦香, 满了(连 LY_FULL_CONFIRM 帧)马上按住; 没满先点 A 补
    LY_FULL_CONFIRM = 2
    LY_FULL_GRACE = 0.08         # 到场这么久之内只读不点(满着就不多出一下 A)
    LY_FILL_CAP = 4.00           # 点 A 等梦香满的上限, 到了照按
    # 引导开始 = 梦香提示没了、花还在(框架 heavy_in_progress); 引导结束(花没了) = 终章出手(不能打断), 松手
    LY_HEAVY_START_CAP = 1.50    # 按住这么久重击位梦香满提示还亮着 = 按下沿被吞了, 松开重按
    LY_HEAVY_RETRIES = 1
    # 【两种重击隔圈换】莉莉 E 每按一次, 重击就在 梦之魇影(重击位花图标 = 框架 lily_heavy 模板) 和 梦之童话(水晶图标,
    # 框架没模板) 之间换; 一圈 E 一次 -> 启动轴魇影、L1 童话、L2 魇影…… 11-19 实机单数圈全是"没认出引导": 童话其实出了,
    # 1.5s 时回路条(梦铃的祷声在涨)被 is_forte_full 读成梦香满 -> 松开重按, 再按满 4.5s, 终章后多打 ~1.5s 普攻。
    # 现在: 引导开始 = 花亮了, 或者按下后梦香满提示连 LY_PROMPT_GONE 帧没了(梦香被吃掉, 两种都认); 重按只看提示不看回路条
    LY_PROMPT_GONE = 2
    # 童话没有能认的图标: 从引导开始按满这么久松手。11-19 实机 L1(循环, 带幽梦芳香): 提示没了 -> 终章出手 3.4~3.65s
    # (开头进梦有 ~0.9s 时停); 宁可晚松 —— 早松了终章没出, 后面的 Q 会把引导打断
    LY_TALE_HOLD = 3.90
    LY_TALE_HOLD_OPEN = 4.90     # 启动轴没有幽梦芳香(+100 梦铃), 魇影实测比循环长 1.0s, 童话照加
    LY_HEAVY_BLIND = 4.50        # 一直没认出引导: 按满这么久松手(视频按住 3.4~4.4s)
    LY_HEAVY_CAP = 6.50          # 认出引导之后的上限(框架原版莉莉 6s)
    LY_HEAVY_END_CONFIRM = 3     # 花连这么多帧不在才算引导结束(框架原版 settle 0.15s)
    LY_ECHO_CAP = 2.00           # Q 按到进 CD 的上限(终章里按的被吞, 要盖过终章)
    # R 前协奏: R 演完进梦乡打不了普攻, 协奏只能在 R 之前补(R 自己给 20)。框架原版莉莉也是先补到 0.80 再 R
    LY_PRE_R_CON = 0.80
    LY_PRE_R_CAP = 2.00
    LY_R_WAIT_CAP = 4.00         # R 还在 CD 上就边 A 边等(视频循环里终章打完 R 还差 ~0.2s)
    LY_R_WINDOW = 1.50           # R 连按到 HUD 消失的窗口
    LY_R_ENSURE_CAP = 5.00       # R 不亮(能量不够)攒的上限
    LY_R_ANIM_MIN = 3.00         # 莉莉大招藏 HUD(视频 ~4.4s)
    LY_R_ANIM_CAP = 8.00
    LY_CON_READ = 0.40           # R 演完读协奏(光圈 + 环)
    LY_CON_CAP = 3.00            # R 没放出去(还能打普攻)时普攻补协奏的上限
    # 进场还在梦乡(普通入场没结束梦乡): 梦乡里 R = 结束梦乡。隔这么久还在才再按, 防出了梦乡把真 R 按出去
    LY_SLUMBER_END_EVERY = 0.40
    LY_SLUMBER_END_CAP = 2.00
    # ---- 切莉莉(心万阙垂天后, 变奏入场) ----
    LY_TO_TIME_OUT = 4.00        # 万阙垂天收招 / 白屏时停没完游戏不放人
    LY_INTRO_ANIM_CAP = 5.00     # 变奏入场演出藏 HUD 的上限(没量过, 藏不藏都认)
    LY_INTRO_NOANIM = 0.35
    LY_SWITCH_ANIM_HINT = 1.00   # quick_switch 超过这么久才判到到场 = 入场演出已经在里面演完

    # ==================== 5R 进阶轴(AXIS_KIND='5R', 时刻都是视频倒计时量的游戏秒) ====================
    # ---- 启动轴开头: 心 E -> 锁暝 Q -> 莉莉 R(视频三下 0.4s 内按完) ----
    R5_OPEN_E_CAP = 0.80         # 心 E 按到进 CD 的上限
    R5_OPEN_SO_Q_CAP = 1.00      # 锁暝声骸按到进 CD 的上限
    # 视频 E -> 切锁暝+Q -> 切莉莉 -> R 按键 0.40s 内按完; 11-19 21:21 实机 E 按下 -> R 按下 0.615s, 其中锁 Q 读到 CD 涨了
    # 还要连第 2 帧(多按一下 Q, ~0.09s)、莉莉到场先干等 0.10s 才按 R。现在 Q 读 1 帧就切, 到场直接按 R
    # (连按 R5_LY_R_DIRECT 0.5s, 切人那几帧按的被丢不要紧; 梦乡/冷却照读)
    R5_OPEN_SO_Q_CONFIRM = 1
    R5_LY_OPEN_R_LEAD = 0.0      # 莉莉到场 -> 开始按 R
    R5_LY_OPEN_R_CAP = 4.00      # R 不亮攒能量的上限(开局能量是满的, 正常一下就出)
    # 【莉莉 R 先直接连按】R 框读数会单帧掉(特效挡住图标、切人那几帧技能栏重画), 原来要连 2 帧读到亮才按, 掉一帧就去
    # 攒 0.6s 普攻: 11-19 20:17 L1 / 20:42 启动轴两次都是 R 早就亮着(box 0.186), 一帧没认到多花 0.8~0.9s。魇影圈 Q 刚
    # 出那 ~0.1s R 框是暗的(终章收尾锁技能), 读到亮再按又晚 ~0.1s。现在不在梦乡、R 不在长冷却就直接连按这么久
    # (HUD 消失 = 放出去), 没放出去才走老的读亮/攒能量。连按对放不出的 R 没副作用(按键被丢)
    R5_LY_R_DIRECT = 0.50
    # ---- 莉莉 R 演完出梦乡: 连按闪避 ----
    # 视频: R 演出藏 HUD 4.8s(倒计时几乎不走), 演出开始 +4.1s 起连按闪避(演出里的被丢), HUD 回来后又闪了 3 下, 接着 2A。
    # 不出梦乡普攻/E/声骸都没有, 后面整段白打 —— 闪完还读到在梦乡就按 R(梦乡里 R = 结束梦乡, R 刚放过在冷却, 不会再放一次)
    R5_LY_OUT_FROM = 4.00        # 从 R 演出开始(HUD 消失)起
    R5_LY_OUT_EVERY = 0.15
    R5_LY_OUT_AFTER_BACK = 2     # HUD 回来后至少再闪这么多下
    R5_LY_OUT_CAP = 1.50         # HUD 回来后还读到在梦乡的上限, 到了走 ly_end_slumber
    # ---- 莉莉启动段: 2A -> E(A2 往后滑) -> Q -> 重击(LY_OPEN_A_LEAD / LY_OPEN_E_AT 共用) ----
    R5_LY_OPEN_Q_CAP = 1.00
    # 【按住普攻带过 E、Q】视频作者点 5 下 A 就按住左键 8.5s, E(按住后 0.1s)、Q 都在按着的时候按, Q -> 重击亮 0.81、
    # E -> 切心 5.5 游戏秒。11-19 21:21 实机(v0.2.5, E-Q 之后才点 A 读梦香满再按): Q -> 梦香满 1.41(A x7) -> 引导 0.80,
    # E -> 切心 6.76, 启动轴万阙垂天比视频晚 1.41 秒几乎全在这。按住走 SendInput, E/Q 也走 SendInput 键盘(PostMessage
    # 发键先投 WM_ACTIVATE 会打断按住); E 没读到进 CD 就松手走老路(R5_LY_OPEN_HOLD_EQ=False 也是老路)
    R5_LY_OPEN_HOLD_EQ = True
    # 带过 E/Q 时一直没认出引导的松手时刻(从按下算): 5 下 A 后梦香还差 E + ~1s 连段, 魇影引导 4.08s(启动轴没有幽梦芳香)
    R5_LY_HEAVY_BLIND_OPEN = 6.30
    # 终章出手(不能切人)到协奏满: 视频花没了 ~0.4s 就切心。光圈连 2 帧就走, 这么久没有就普攻补
    R5_LY_FIN_CON = 0.90
    R5_LY_CON_CAP = 2.00
    R5_LY_TO_XN_TIME_OUT = 3.00  # 终章没收招游戏不放人
    # ---- 循环莉莉(变奏·永眠花开): E -> A -> 切心 ----
    R5_LY_LOOP_A = 1             # E 之后点几下 A 再切(视频点一下, 跟切人键几乎同时)
    # 【四星协奏武器】武器的协奏被动(E 触发)第一个循环还在冷却: 第一个循环莉莉第一次上场不按 E 只 A, E 挪到第二次上场
    # (按住重击之前)。用户莉莉带四星协奏武器, True; 换专武(安魂)改 False
    R5_LY_4STAR = True
    R5_LY_LOOP_A_NO_E = 3        # 不按 E 时至少点几下 A
    # 不按 E 时点 A 点到到场后这么久再切。视频: 切莉莉键后 +0.66 起点 A 一直点到 +1.53 切心(6~7 下); 11-19 21:21 实机
    # 只点 3 下(+0.57~+0.91)就切, 第二次上场 E 后按住 1.1s 梦香才满、1.77s 才进引导(视频 0.82s), 第一个循环莉莉主段多 ~1s
    R5_LY_LOOP_NO_E_UNTIL = 1.40
    # ---- 5R 莉莉主段(锁暝切回来): 到场就按住, 不先点 A 等梦香满 ----
    # 视频作者在锁暝那边就按住左键带过切人, 莉莉到场 ~0.3s 重击转起来, 到场 -> 松开 3.74s; 梦香没满时按住的普攻连段
    # 自己把梦香打满再转重击(启动轴字幕"按住普攻打满回路")。11-19 20:17 实机先读梦香再按: 到场 -> 按下 0.47~0.51s
    # (L2~L4 都点了 2 下 A), 四星武器那圈先 E 再点 6 下 A 1.10s; 按下 -> 松开 3.70s 跟视频一样, 差的就是这一截。
    # 不带着按住切人(切人键走 PostMessage, 会不会断 SendInput 的按住没验过), 到场确认了就按
    R5_LY_HOLD_AT_ENTRY = True   # False = 回到先点 A 读梦香满再按
    # 按下时梦香可能还差一截要连段补, 童话(没图标)又一直没认出引导时按满这么久才松(比 LY_HEAVY_BLIND 多给补梦香的时间)。
    # 一直按着时梦香一满就转重击, 满提示可能一帧都不亮(11-19 20:42 L1 四星圈: E 后按住, +1.45s 进梦时停, 提示 4.9s 没亮过,
    # 4.90 松 = 引导只按了 3.46s, R前协奏 0.82; 20:39 L1 提示亮了 0.6s 才转, 引导开始 +1.77s)。按 E 后 +1.45 进梦 + 童话 3.9 算
    R5_LY_HEAVY_BLIND = 5.30
    # 变奏入场 E 出了的圈(梦香到场就满, 按下 0.4~0.65s 就进引导)童话圈的 blind。11-19 21:21 L3: 童话的满提示一帧都没认到
    # (mouse_forte 160 次 None), 倒计时看按下 +0.7s 进梦时停, 按满 5.31s 才松 —— 终章早出完了。童话 引导 -> 终章 3.4~3.65s
    R5_LY_HEAVY_BLIND_FULL = 4.60
    # Q 的 CD 涨上来读 1 帧就去按 R(视频 Q 后 0.06s 就 R; 实机连 2 帧确认, Q 出了到确认 0.26s)
    R5_LY_ECHO_CONFIRM = 1
    # 【R 前协奏不到 0.86 先补 A】11-19~20 全天: 四星圈 L1 R 前一帧读数 0.80~0.83 的 6 圈 3 圈 R 后没满(0.98~0.99,
    # 锁暝普通入场 -> 没碎霆 +200 妄执/+10 协奏, 锁b 跟着不满), >= 0.86 的全满。只有 L1 会读到这么低, 补一下 A ~0.23s
    R5_LY_PRE_R_CON = 0.86
    # ---- 锁暝第一次上场(普通入场): 2A -> 斜前闪 -> 2A -> Q -> 切莉莉 ----
    # 视频两圈: 到场点 A 0.6~0.85s -> 到场 +1.00/+1.02s 闪避(W+D / W+A) -> 点 A 1.2~1.3s -> Q(+2.69/+2.74) -> 0.1s 后切
    R5_SO0_A_EVERY = 0.17
    R5_SO0_DODGE_AT = 1.00       # 从到场起
    R5_SO0_DODGE_KEYS = ('w', 'd')
    # 【锁0 能处决就处决, 顶替那一闪】v0.2.14 起用户定: 处决放回锁暝闪避位(锁0 到场就一直点 A, 提示出得来)。
    # 11-19~20 v0.2.11 及以前锁0 处决 36 圈锁b 没满 12 圈、闪避 9 圈全满; 当时锁0 的 Q 也切了 A4 尾巴(v0.2.14 才加 Q_GAP),
    # 两件事叠在一起没拆开过 —— 实机还是处决圈锁b 不满再看。v0.2.12~0.2.13 放过锁R 演完(R5_SO_R_BREAK), 已关
    R5_SO0_BREAK = True
    # 循环轴锁R 演完、同奏切心2 之前处决(v0.2.12~0.2.13 的做法)。关着; 心2 入场(XN2_ENTRY_BREAK)照旧兜底
    R5_SO_R_BREAK = False
    # 【锁R 演完先点 A 再看处决提示】只放技能不打普攻, 外圈满了中间也不出 F 提示(11-20 00:18 三圈全是"锁R外圈满没提示")。
    # 只在 R5_SO_R_BREAK 开着时点; 不攒协奏: 00:45/00:47 录屏锁0 进场照旧 0.41(第一下 A 后 0.23s 就切走, A1 ~0.4s 才打上)
    R5_SO_R_A_TAPS = 2
    R5_SO_R_BREAK_PROMPT_WAIT = 0.35   # 点完 A 外圈满就再读这么久提示(A 打上才出)
    # 闪避之后点 A 这么久再 Q。处决上了从处决演完(外圈清空)起算: 处决里倒计时是停的, 等完不花游戏时间,
    # 但演出里按的 A 全被吞, 不从演完起算锁暝就只剩 ~1s 普攻, 妄执差一截(11-19 18:53 第 3 圈)
    R5_SO0_A2_FOR = 1.30
    # 【A 停了等一下再 Q, 别把 A4 尾巴打断】视频闪避右键 -> Q 1.70s、最后一下 A -> Q 0.30s; 我们原来 1.45s / 0.13s。
    # 11-20 00:45/00:47 录屏: 我们的 Q 正好落在红色斩击上, 声骸出来就把后面的地面连击切了, 协奏环在按 Q 那一刻停涨;
    # 视频红色地面环连击一直打到 Q 后 ~0.3s。闪前视频只涨 2.5%(我们 5.3%), 锁0~锁b 总量却一样, 差的就是闪后这段
    R5_SO0_Q_GAP = 0.25
    R5_SO0_Q_CAP = 1.00
    # 【锁0 走之前协奏要够】锁b 刻心念松手那 +40 正好卡在满的边上, 差的就是锁0 攒的那点。11-19 22:19~22:52 处决圈 14 次:
    # 锁0 切莉莉那一刻协奏 < 0.50 的 4 次(0.48~0.50)锁b 全是"读数满没光圈"(保变奏多 1.1~2.2s), >= 0.50 的 10 次全满。
    # F 每圈都在第一下 A 后 1.06~1.14s 按, 代码这边是齐的; 差在游戏里: 处决前 A3 那下进没进条(回路条正好一半/多一点)、
    # 处决演完后连段起得早晚(1.3s 一到就 Q, 没打完的那下被吞; 处决后回路条右半 有的圈涨 6~8 格、有的圈几乎不涨)。
    # 【别在锁0 补 A 攒协奏】v0.2.9 试过 Q 前协奏 < 0.53 就接着点 A: 23:26 实机 L2~L4 每圈 A x4 0.8s 只涨 0.02~0.03,
    # 一圈白多 0.8 游戏秒; L3(0.47->0.49)锁b 照样没满、L4(0.46->0.49)照样满 —— 锁0 走时的协奏预测不了锁b, 已删
    # ---- 锁暝第二次上场(莉莉变奏进来 = 碎霆 +200 妄执): E 无舍念 -> 按住刻心念 -> 变奏心 ----
    # 视频: 变奏入场 HUD 回来就是满妄执(粉条满格), +0.4s E, +1.4s 刻心念。不满(锁0 被打断/打空)就点 A 补到满
    R5_SO_B_ARRIVE_CAP = 3.00    # 等变奏入场演完、场上是锁暝的上限
    R5_SO_B_FILL_CAP = 5.00      # 妄执没满点 A 补的上限, 到了也照按 E(刻心念那段有保变奏兜底)
    # 【刻心念长按只在收招给 +40 协奏】按住那 4s 协奏读数一直在 0.60~0.72 不动, 松手后 0.36~0.46s 一下跳满。
    # 11-19 18:53~20:42 五局 12 圈: 无舍念/刻心念/锁0 的按键每圈一样, 3 圈松手后读数到 1.00 却没光圈(差 1~2%, 打中数
    # 有出入), 原来等满 SO_KX_GLOW_GRACE 0.5s 才去点 A, 再 A x3 0.59s 光圈才出, 一圈多 ~1.1s。正常圈光圈在读数满后
    # ~0.05s 就出, 读数满了这么久还没光圈就马上点 A 补(12W 的 SO_KX_GLOW_GRACE 不动)。
    # 23:26 实机 L3 又没满: 等协奏 0.73s + 保变奏 A x4 0.81s, 比满的圈(光圈在松手后 0.40~0.47s)多 1.1s。宽限再收到 0.10
    # (光圈连帧计数跨调用接着数, 刚好晚一点出的话保变奏第一帧就认到), 保变奏点 A 跟锁0 一样 0.17s 一下
    R5_SO_KX_GLOW_GRACE = 0.10
    R5_SO_B_A_EVERY = 0.17
    # 【松手就接着点 A, 不干等】视频刻心念是点按, 收招后作者一直点 A 到切人(13 下 2.2s), 念起普攻也给协奏; 我们长按松手后
    # 原来不按键等光圈, 读数满没光圈才去点 A。11-20 00:18 录屏量协奏环: 刻心念按住前 L1 0.617 没满 / L2 0.633 满 /
    # L3 0.656 满 —— +40 刚好卡在边上, 没满那圈松手后干等 0.6s 再 A x7 1.36s 才出光圈。松手后这么久起点 A
    # (收招 +40 在松手后 0.36~0.46s 到, 早点的会落在收招动作里被吞; 再早怕被当成接着按住)
    R5_SO_KX_TAP_FROM = 0.30
    # ---- 心 ----
    # 【心0 点 A 要盖过 A3 那一截再 E】心1 进场的应世心 = 心0 留下的 + 变奏 60 + 变奏打中的; 心0 是唯一能多攒的地方。
    # 条 32 格(左右各 16)。视频心0: 切人起点 A 1.4s, +1.0 时 2 格、+1.3 跳到 5 格(A3 打上)、+1.4 E、+1.5 切走;
    # 心1 进场 27 格、闪避时 29 格。11-19 18:53 实机(DVR 逐帧, L2/L3 一样): 我们 +1.26 就按 E, 走的时候只有 3 格,
    # A3 那一截被 E 吃掉; 心1 进场 23 格、闪避时 ~25 格(右半 ~0.56, 够不上闪后试按)
    R5_XN0_A_UNTIL = 1.30        # 从第一下 A 起(到场 +0.05)点到这一刻再 E(12W 的 XN0_A_UNTIL 1.05 不动)
    # 第一段(步红尘-转相)视频不按声骸也不按照世相 E, 转相演完直接同奏切锁暝; 心的声骸在第二段进场
    R5_XN2_ECHO_AT = 0.35        # 心2 切人键 -> 按 Q(变奏入场演出里能放, 视频到场 0.4s 进 20s 冷却)
    R5_XN2_ECHO_CAP = 1.00
    # 【心2 切莉莉前补协奏只点 A, 不按 E】E 留给下一圈心0。11-19 22:19~22:52 十一局: 启动轴尾补协奏按了 E 的 8 局里
    # L1 心0 五次 `E_没见过亮`(冷却还剩 ~8s), 循环里两次心0 E 晚 0.3~0.4s 才出; 心0 没 E 的圈心1 闪后右半平均
    # 0.72(有 E 的 0.88, 差 ~2.5 格), 试按就出成普通重击。补协奏开头读数 0.83~1.00, 心的 A 几下就满; 上限放宽
    R5_XN2_CON_E = False
    R5_XN2_CON_CAP = 2.00
    # 心1 点 A 补应世心: 视频到场就点、0.17s 一下(闪避前 6 下); 12W 是 +0.40 起 0.25s 一下(闪避前 3 下)
    R5_XN1_TAP_FROM = 0.15
    R5_XN1_TAP_EVERY = 0.17
    # 【心1 闪避后直接按住, 不等花图标】视频逐帧: 闪避后 0.14s 按住, 那时条右端还差 ~4 格、花没亮, 按下 0.35s
    # 条开始清空(步红尘出手), 再 2.1s 转相。11-19 19:20 实机我们点 A 等花亮: 贴着 Boss 也要点 1.9s(A 一下只涨
    # 一格左右), 按住比视频晚 1.5~1.9s, 心1 一圈慢的就是这段。点 A 的节奏不是瓶颈。
    # 试按 R5_XN1_EARLY_HOLD 秒条没清空 = 出的是普通重击 -> 松手回到点 A 等花再按(原来那条路)。
    # Boss 离得远 A 打空(19:20 L1 进场条满也点了 2.7s)时试按白花 ~1s, 这种只能认
    R5_XN1_EARLY_AFTER = 0.14    # 闪避 -> 按住(这中间不点 A)
    # 【试按门槛】右半到这么多才试, 不到就接着点 A, 连 2 帧到了再试(不等花)。11-19 22:19~22:52 v0.2.8 十一局 22 次循环心1:
    # 试按那一刻右半 >= 0.81 的 14 次里 12 次 0.36~0.52s 出步红尘(另 2 次: Boss 远条一直不动 / 按下后才满);
    # <= 0.77 的 7 次全是普通重击 —— 按下后条才一格格补满, 普通重击收完再按, 一次多 ~1.5s。原来 0.58 太松
    R5_XN1_EARLY_R = 0.82
    R5_XN1_EARLY_HOLD = 1.10     # 试按时长: 视频按下到开始清空 0.35s, 我们判清空本身晚 ~0.3s
    # 【试按"按下后才满"别急着重按】按下那一帧花没亮、随后亮了, 原来连 2 帧就松开重按(~0.25s)。可条那时已经够了,
    # 原来那一下自己会出步红尘: 23:26 实机 L1/L4 没重按 0.39s 出; L2 0.26s 重按、0.32s 出(还是原来那下);
    # L3 0.25s 重按把它打断了, 1.00s 才出(多 0.6s)。步红尘 0.36~0.52s 出手, 过了这么久还没清空才算普通重击再重按
    R5_XN1_REPRESS_MIN = 0.60
    # 【循环轴心1 闪避带方向(W+A 斜前闪)】视频键盘浮层: 心1 点 A 时按着 W, 闪避是 W+A, 右键后 0.20s 按住。
    # 我们原来是不带方向的右键 = 往后跳。闪后按住心会往前冲, 冲到 Boss 身上那一下打中才把最后几格补满出步红尘;
    # Boss 远一点就打不着, 出成普通重击(普通重击打中才满, 再重按)。11-19 v0.2.2 起 19 次试按只成 7 次(0.39~0.51s
    # 出手), 失败的多花 1.0~1.5s; 失败跟条差几格无关(DVR: 成功那次右半差 3 格, 21:58 L1 失败那次只差 2 格),
    # 录屏看是冲过去还没碰到 Boss。启动轴心1 每次都闪前就满, 不闪, 不传这个。
    R5_XN1_DODGE_KEYS = ('w', 'a')
    # 带方向的闪避 dodge_dir 比右键多 0.05s 松方向键的尾巴: 0.09 = 右键按下后 ~0.21s 按住, 跟原来/视频一样
    R5_XN1_EARLY_AFTER_DIR = 0.09

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opener_armed = True
        self.last_opener = -1
        self.macro_t0 = 0.0
        self.macro_marks = []
        self.cur = None
        self.holding_attack = False
        self.si_holding = False
        self.switch_press_at = 0.0
        self.lib_press_at = 0.0
        self.loop_count = 0
        self.hud_seen_at = 0.0         # 最后一次读到队伍 HUD 在场的时刻(HUD_LOST_ABORT 用)
        self.hud_watch = False         # 宏跑着的时候才查 HUD 丢没丢
        self.drive_log_at = 0.0
        self.timeline_logged = True
        self.ly_intro_done_at = 0.0
        self.so_tongzou_at = -99.0     # 锁暝 R 出手的时刻(给自己 5s 同奏)
        self.ring_hi_at = 0.0          # 协奏环最近一次读到 >= RING_HI 的时刻(con_glow 用)
        # reset_state() 只在 load_chars() 时跑, 而角色对象会被粘性复用 —— 这里也要启一次
        try:
            start_enable_watch(self.task, self.logger)
        except Exception:
            pass

    def reset_state(self):
        super().reset_state()
        self.opener_armed = True
        # 内置非治疗角色默认 True, 换类时会被打回, 所以在这里重申(这条轴不在切人时按处决)
        self.check_f_on_switch = False
        start_f_watch(self.task, self.logger)
        start_enable_watch(self.task, self.logger)

    # ==================== 队伍判定 ====================

    @property
    def is_in_xsl(self) -> bool:
        """本队 = 心 + 锁暝 + 莉莉。按类名判。"""
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Lily', 'Suoming'}:
                members += 1
        return members == 2

    def teammate(self, name):
        for char in self.task.chars:
            if char is not None and char.name == name:
                return char
        return None

    def opener_pending(self):
        return self.opener_blocked_by() is None

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。不查任何技能可用性(场下读数是旧的)。"""
        if not self.is_in_xsl:
            return 'team is not hsin/lily/suoming'
        if not self.opener_armed:
            restarted = getattr(self.task, '_ww_task_restarted_at', -1)
            if restarted > self.last_opener:
                self.logger.info('auto combat 重开过 — 启动轴重新武装, 从头跑')
                self.opener_armed = True
                return None
            return 'already armed-down (ran once this combat)'
        f_press = getattr(self.task, '_f_last_press', -1)
        if f_press > self.last_opener:
            waited = time.time() - f_press
            delay = f_settings(self.task)[1]
            if waited < delay:
                return f'F pressed {waited:.2f}s ago, waiting {delay:.2f}s for the mobs'
            return None
        if self.last_opener >= 0 and time.time() - self.last_opener < self.OPENER_MIN_GAP:
            return f'ran {time.time() - self.last_opener:.1f}s ago (< {self.OPENER_MIN_GAP:.0f}s)'
        if not self.task.use_liberation:
            return 'task.use_liberation is off'
        return None

    # ==================== 宏原语(引擎, 照抄心锁守 v2.0.0) ====================

    def macro_wait(self, sec):
        """精确等待, 【不点击】。切成 0.1 秒一片, 片间检查任务是否被停用。"""
        if sec <= 0:
            self.task.executor.check_enabled(check_pause=False)
            return
        end = time.time() + sec
        while True:
            left = end - time.time()
            if left <= 0:
                return
            time.sleep(min(0.1, left))
            self.task.executor.check_enabled(check_pause=False)

    def wait_until_time(self, deadline):
        self.macro_wait(deadline - time.time())

    def macro_start(self):
        self.macro_t0 = time.time()
        self.macro_marks = []

    def macro_mark(self, name):
        self.macro_marks.append(f'{name}@{time.time() - self.macro_t0:.2f}')
        # 【看门狗】每一步都会打标记, 在这里顺手查: HUD 很久没出现 = 关卡结束/过场/窗口没了,
        # 再按下去就是在结算画面上乱按(景月守实机 2026-09-28 19:17 见过)。不读屏, 只比时间戳
        if self.hud_watch and time.time() - self.hud_seen_at > self.HUD_LOST_ABORT:
            self.hud_watch = False
            raise MacroBroken(f'队伍 HUD {time.time() - self.hud_seen_at:.1f}s 没出现(关卡结束/过场/窗口没了?)')

    def hud_ok(self):
        """队伍 HUD 在不在(读当前帧)。见到就记下时刻, 给看门狗用。"""
        ok = bool(self.task.in_team()[0])
        if ok:
            self.hud_seen_at = time.time()
        return ok

    def macro_timeline(self, since=0):
        return ' '.join(self.macro_marks[since:])

    def macro_spam(self, window, interval=None, tag='A'):
        """连点一个窗口。打出几段由 window 决定(动画期间的点击被丢掉, 连点保证总有一下落在动画刚结束时)。"""
        interval = self.A_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        cap = int(window / max(interval, 0.01)) + 2
        while time.time() - start < window and n < cap:
            self.click()
            n += 1
            remaining = window - (time.time() - start)
            if remaining <= 0:
                break
            # click 自己按住 A_TAP 秒, 不扣的话连点周期从 interval 变成 interval + A_TAP
            self.macro_wait(min(max(0.0, interval - self.A_TAP), remaining))
        self.macro_mark(f'{tag}(x{n})')
        return n

    def spam_key(self, send, window, interval=None, tag='key', poke=False, presses=None):
        """在一个窗口里反复发同一个技能键(动画期间的按键被丢掉、不排队, 单发必丢)。

        poke: 发键期间要不要照常点普攻(两条节奏各走各的)。presses: 只发这么多次, 均匀铺在窗口里。
        """
        interval = self.KEY_INTERVAL if interval is None else interval
        if presses is not None and presses > 0:
            interval = max(interval, window / presses)
        start = time.time()
        n = 0
        clicks = 0
        next_click = 0.0
        while True:
            send()
            n += 1
            if presses is not None:
                if n >= presses:
                    break
                left = interval
            else:
                left = window - (time.time() - start)
                if left <= 0:
                    break
            step_end = time.time() + min(interval, left)
            while True:
                now = time.time()
                if now >= step_end:
                    break
                if poke and now >= next_click:
                    self.click()
                    clicks += 1
                    next_click = now + self.A_INTERVAL
                self.macro_wait(min(0.02, step_end - time.time()))
        self.macro_mark(f'{tag}(x{n},A x{clicks})')
        return n

    def game_foreground(self):
        try:
            return bool(self.task.executor.interaction.hwnd_window.is_foreground())
        except Exception:
            return False

    def attack_down(self, direct):
        """按下左键。direct = 走 SendInput(失败自动退回 PostMessage)。返回实际走没走 SendInput。"""
        if direct and send_input_left(False):
            self.si_holding = True
        else:
            direct = False
            self.task.mouse_down()
        self.holding_attack = True
        return direct

    def release_attack(self):
        # 【SendInput 按下的必须 SendInput 松开】: PostMessage 的 mouse_up 只进游戏窗口的消息队列,
        # 系统里那个左键会一直按着 —— 宏异常退出时整台电脑的左键都是按住的
        if self.si_holding:
            if not send_input_left(True):
                self.logger.warning('SendInput 左键松开没发出去 — 如果左键卡住, 手动点一下')
            self.si_holding = False
            self.holding_attack = False
        elif self.holding_attack:
            self.task.mouse_up()
            self.holding_attack = False

    def tap(self, hold=None):
        """点一下普攻, 【真的按住 hold 秒】。"""
        hold = self.A_TAP if hold is None else hold
        self.task.mouse_down()
        self.holding_attack = True
        try:
            self.macro_wait(hold)
        finally:
            self.task.mouse_up()
            self.holding_attack = False

    def si_vk(self, key):
        """键位字符串 -> 虚拟键码(给 send_input_key)。认不出返回 None。"""
        vk = -1
        try:
            vk = int(self.task.executor.interaction.get_key_by_str(key)) & 0xff
        except Exception:
            pass
        if vk <= 0 and isinstance(key, str) and len(key) == 1:
            try:
                vk = int(ctypes.windll.user32.VkKeyScanW(ord(key))) & 0xff
            except Exception:
                vk = -1
        return vk if 0 < vk < 0xff else None

    def si_tap_key(self, vk):
        """SendInput 点一下键(按住 SI_KEY_TAP 秒)。返回发出去没有。"""
        if not send_input_key(vk, False):
            return False
        try:
            self.macro_wait(self.SI_KEY_TAP)
        finally:
            send_input_key(vk, True)
        return True

    def click(self, *args, **kwargs):
        """本队里所有的 self.click() 都改成按住 A_TAP(BaseChar.click 强制 10ms, 等于没点)。不在本队走原版。"""
        if not self.is_in_xsl:
            return super().click(*args, **kwargs)
        self.tap()

    def quick_switch(self, target, click_during=False, time_out=None, read_con=False, assume_con=False):
        """按数字键直切给指定角色(不走 switch_next_char 选人)。

        read_con: 按键之后读一次协奏决定按不按"带变奏"记账; assume_con: 不读屏直接按满协奏记。
        Returns: 是否真的切过去了。
        """
        if target is None:
            return False
        if target is self.cur:
            self.macro_mark(f'>{target.name[:3]}(already)')
            return True
        entry = time.time()
        time_out = self.SWITCH_TIME_OUT if time_out is None else time_out
        source = self.cur if self.cur is not None else self
        slot = target.index + 1
        self.switch_press_at = time.time()
        self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
        if assume_con:
            con_full = True
        elif read_con:
            # 不用 char.is_con_full(): 那层读到过一次满就一直缓存成满
            try:
                con_full = float(self.task.get_current_con()) >= 1.0
            except Exception:
                con_full = False
        else:
            con_full = False
        con_cost = time.time() - entry

        start = time.time()
        last = start
        next_click = 0.0
        trace = [] if self.TRACE_SWITCH else None
        team_size = len([c for c in self.task.chars if c is not None])
        untrusted_hits = 0
        clicks = 0
        while time.time() - start < time_out:
            now = time.time()
            if now - last >= self.SWITCH_KEY_INTERVAL:
                self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
                last = now
            if click_during and now >= next_click:
                self.click()
                clicks += 1
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            in_team, index, count = self.task.in_team()
            if in_team:
                self.hud_seen_at = time.time()
            hit = in_team and index == target.index
            trusted = in_team and count >= team_size
            untrusted_hits = untrusted_hits + 1 if (hit and not trusted) else 0
            if trace is not None:
                trace.append(f'{time.time() - start:.2f}:'
                             f'{(index if trusted else f"?{index}") if in_team else "x"}')
            if hit and (trusted or untrusted_hits >= self.UNTRUSTED_CONFIRM):
                self.handoff(source, target, con_full)
                self.macro_mark(f'>{target.name[:3]}{"^" if con_full else ""}'
                                f'({time.time() - entry:.2f}|con{con_cost:.2f}|A x{clicks})')
                if trace and time.time() - entry > 0.4:
                    self.logger.info(f'slow switch trace -> {target.name}: {" ".join(trace)}')
                return True
        # 一直是旧角色的 index = 游戏在拒绝切人; 带 ? 的都是目标 = count 判据挡住了; 全是 x = HUD 不在
        self.logger.warning(f'switch to {target.name} slot {slot} timed out '
                            f'after {time_out:.1f}s (stuck in an animation?)'
                            + (f' trace: {" ".join(trace)}' if trace else ''))
        self.macro_mark(f'>{target.name[:3]}FAIL')
        return False

    def handoff(self, from_char, to_char, con_full):
        """复刻 switch_next_char() 尾部那套交接记账。不补的话 get_current_char() 返回 None, 主循环当场崩。"""
        self.task.in_liberation = False
        from_char.switch_out(con_full=con_full)
        to_char.is_current_char = True
        to_char.has_intro = con_full
        to_char.has_sub_dps_intro = con_full and from_char.is_sub_dps
        to_char.last_switch_in_time = time.time()
        if con_full:
            now = time.time()
            self.task.add_freeze_duration(now, to_char.intro_motion_freeze_duration, -100)
            from_char.last_outro_time = now
        self.cur = to_char
        self.ring_hi_at = 0.0

    def switch_retry(self, target, tag, **kw):
        """quick_switch, 超时了先等 HUD 回来再切, 最多再试 SWITCH_RETRIES 次。只花时间。"""
        if self.quick_switch(target, **kw):
            return True
        for i in range(self.SWITCH_RETRIES):
            self.wait_hud_up(cap=2.0, tag=f'{tag}重切前HUD')
            self.macro_mark(f'{tag}切{target.name[:3]}重试#{i + 1}')
            kw2 = dict(kw)
            kw2['time_out'] = self.SWITCH_RETRY_TIME_OUT
            if self.quick_switch(target, **kw2):
                return True
        return False

    def wait_hud_back(self, tag='anim', time_out=None, min_gone=0.0, tail=0.0, hold_from=None):
        """等演出结束(队伍 HUD 回来), 【期间一下都不点】。先确认消失过。

        min_gone: 从第一次读到消失起, 这么久之内读到的"回来"不认(演出中途 HUD 会闪一两帧)。
        回来要连读 HUD_BACK_CONFIRM 帧。
        hold_from: 消失后这么久按住普攻(预输入重击), 返回时还按着, 调用方负责松开。
        """
        time_out = self.LIB_ANIM_TIME_OUT if time_out is None else time_out
        start = time.time()
        gone_at = None
        back_hits = 0
        back = False
        while time.time() - start < time_out:
            self.task.next_frame()
            if (hold_from is not None and gone_at is not None and not self.holding_attack
                    and time.time() - gone_at >= hold_from):
                si = self.xn_attack_down()
                self.macro_mark(f'{tag}按住预输入({"SI" if si else "PM"},{time.time() - gone_at:.2f}s)')
            if not self.hud_ok():
                if gone_at is None:
                    gone_at = time.time()
                back_hits = 0
            elif gone_at is not None:
                if time.time() - gone_at >= min_gone:
                    back_hits += 1
                    if back_hits >= self.HUD_BACK_CONFIRM:
                        back = True
                        break
            elif time.time() - start >= self.HUD_NO_ANIM_GRACE:
                back = True
                break
            self.macro_wait(0.01)
        if gone_at is not None:
            self.add_freeze_duration(gone_at, time.time() - gone_at)
        self.macro_mark(f'{tag}({"ok" if back else "timeout"},'
                        f'{"anim" if gone_at is not None else "NOANIM"},{time.time() - start:.2f}s)')
        self.macro_wait(tail)
        return back

    def wait_hud_up(self, cap=None, tag='HUD'):
        """等队伍 HUD 【现在】在场。按 R 之前用 —— HUD 本来就不在的时候判"演出开始了"是假成功。"""
        cap = self.HUD_UP_CAP if cap is None else cap
        start = time.time()
        while time.time() - start < cap:
            self.task.next_frame()
            if self.hud_ok():
                if time.time() - start > 0.05:
                    self.macro_mark(f'{tag}在场({time.time() - start:.2f}s)')
                return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}一直不在({cap:.1f}s)')
        return False

    def on_field_char(self, time_out=None):
        """现在场上是谁 —— 只在拿不到入口角色时才用(开局 HUD 渐入时 in_team() 会说谎, 要 count 达标)。"""
        time_out = self.FIELD_SETTLE_TIME_OUT if time_out is None else time_out
        expected = len([c for c in self.task.chars if c is not None])
        end = time.time() + time_out
        while True:
            self.task.next_frame()
            in_team, index, count = self.task.in_team()
            if in_team:
                self.hud_seen_at = time.time()
            if in_team and count >= expected:
                for char in self.task.chars:
                    if char is not None and char.index == index:
                        return char
                return None
            if time.time() >= end:
                self.logger.warning(f'team HUD never settled in {time_out:.1f}s '
                                    f'(in_team={in_team} index={index} count={count} '
                                    f'expected={expected}), cannot tell who is on field')
                return None
            self.macro_wait(0.03)

    def mark_busy(self, busy):
        """F 键监视线程靠它区分"战斗中手动处决"和"重开副本"。"""
        self.task._ww_macro_busy = busy
        self.task._three_fire_macro_busy = busy

    def need(self, ok, what):
        if not ok:
            raise MacroBroken(what)

    def press_key_until_cd(self, send, available, cap, tag, ready_grace=0.15, poke=False):
        """一直按某个技能键到它进 CD。返回放出去了没有(本来就在 CD 上返回 False)。

        【先见过"亮"才认"暗"】: 刚切人那几帧技能栏还是上一个人的, 读到的是上一个人的 CD。
        """
        start = time.time()
        n = live = clicks = 0
        seen_ready = False
        next_click = 0.0
        while time.time() - start < cap:
            self.task.next_frame()
            hud = bool(self.hud_ok())
            if hud:
                if available():
                    seen_ready = True
                elif seen_ready and live > 0:
                    self.macro_mark(f'{tag}_ok(x{n},A x{clicks},{time.time() - start:.2f}s)')
                    return True
                elif not seen_ready and time.time() - start >= ready_grace:
                    self.macro_mark(f'{tag}_没见过亮(x{n},{time.time() - start:.2f}s)')
                    return False
            send()
            n += 1
            if hud:
                live += 1
            step_end = time.time() + self.E_RESEND_STEP
            while True:
                now = time.time()
                if now >= step_end:
                    break
                if poke and now >= next_click:
                    self.click()
                    clicks += 1
                    next_click = now + self.A_INTERVAL
                self.macro_wait(min(0.02, step_end - now))
        self.macro_mark(f'{tag}_CD?(x{n},seen{int(seen_ready)},{cap:.1f}s)')
        self.logger.info(f'{tag}: never read as on-cooldown after {n} presses (seen ready={seen_ready})')
        return False

    def press_res_until_cd(self, char, cap, tag, ready_grace=None, poke=False):
        ready_grace = self.SKILL_BAR_SETTLE if ready_grace is None else ready_grace
        return self.press_key_until_cd(char.send_resonance_key, char.resonance_available, cap, tag,
                                       ready_grace=ready_grace, poke=poke)

    def fire_r(self, char, tag, window):
        """发大招到队伍 HUD【连续】消失 LIB_GONE_CONFIRM 帧。返回放出去没有。

        发键之前先等 HUD 在场(HUD 本来就不在会假成功)。确认期间继续按 R 没有副作用 —— 演出里的按键被丢掉。
        """
        self.wait_hud_up(tag=f'{tag}前HUD')
        start = time.time()
        self.lib_press_at = start
        n = gone = 0
        fired = False
        while time.time() - start < window:
            char.send_liberation_key()
            n += 1
            self.task.next_frame()
            if not self.hud_ok():
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM:
                    fired = True
                    break
            else:
                gone = 0
            self.macro_wait(self.LIB_KEY_INTERVAL)
        self.task.skip_combat_check = True
        if fired:
            char.record_liberation_use()
            self.macro_mark(f'{tag}_fired(x{n},{time.time() - start:.2f}s)')
            return True
        self.macro_mark(f'{tag}_NOFIRE(x{n})')
        try:
            lit = bool(char.liberation_available())
        except Exception:
            lit = 'err'
        # lit=True: 键被上一个动作吞了; lit=False: 能量不够
        self.logger.warning(f'{tag}: {char.name} liberation never started an animation '
                            f'after {n} presses (icon lit={lit})')
        return False

    def lib_ready_reads(self, char, frames):
        """HUD 在场时连续 frames 帧读到 char 的 R 可用。HUD 不在的帧不算, 最多读 frames*3 帧。"""
        hits = 0
        for _ in range(frames * 3):
            self.task.next_frame()
            if self.hud_ok():
                try:
                    ready = bool(char.liberation_available())
                except Exception:
                    ready = False
                if not ready:
                    return False
                hits += 1
                if hits >= frames:
                    return True
            self.macro_wait(0.02)
        return False

    def ensure_r(self, char, tag, window, cap, build):
        """R 一定要放出来。读 R 亮 -> 按; 亮着却没放出去就再按, 最多 R_TRIES 次;
        不亮 -> build(轮次) 攒一小段再读, 每 R_BLIND_EVERY 轮盲按一次防读数不准。上限 cap 只防打不到怪。
        """
        start = time.time()
        tries = rounds = 0
        while time.time() - start < cap:
            if self.lib_ready_reads(char, 2):
                tries += 1
                if self.fire_r(char, f'{tag}R' if tries == 1 else f'{tag}R重按#{tries - 1}', window):
                    if rounds:
                        self.logger.info(f'{tag}: {char.name} R 攒了 {time.time() - start:.1f}s 能量才放出去')
                    return True
                if tries >= self.R_TRIES:
                    break
                continue
            rounds += 1
            if rounds == 1:
                self.macro_mark(f'{tag}R没亮(攒能量)')
            build(rounds)
            if rounds % self.R_BLIND_EVERY == 0 and not self.lib_ready_reads(char, 2) and \
                    self.fire_r(char, f'{tag}R盲按#{rounds}', self.R_BLIND_WINDOW):
                return True
        self.logger.warning(f'{tag}: {char.name} 的 R 保底 {time.time() - start:.1f}s 还是没放出去 '
                            f'(亮了按 {tries} 次, 攒能量 {rounds} 轮), 不放了')
        return False

    def read_cd(self, name='resonance', char=None):
        """读技能框里的 CD 数字(秒)。不在 CD / 读不到都返回 0(框架没 CD 时返回负数, 截成 0)。"""
        try:
            index = None if char is None else char.index
            return max(0.0, float(self.task.get_cd(name, index)))
        except Exception:
            return 0.0

    def read_con(self, want, settle=None, click=False, tag=None):
        """读协奏: 自己刷帧、给结算留时间, 连着几帧达到 want 就立刻返回。返回期间读到的最大值。"""
        settle = 0.50 if settle is None else settle
        start = time.time()
        best = 0.0
        hits = 0
        next_click = 0.0
        while True:
            now = time.time()
            if click and now >= next_click:
                self.click()
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            self.hud_ok()
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            best = max(best, con)
            hits = hits + 1 if con >= want else 0
            if hits >= self.SK_CON_FULL_CONFIRM or time.time() - start >= settle:
                break
            self.macro_wait(self.SK_CON_POLL)
        if tag:
            self.macro_mark(f'{tag}(con{best:.2f},{time.time() - start:.2f}s)')
        return best

    def wait_con_full(self, who, cap, tag, poke=True, cur=None, strict=False):
        """边打边等协奏真满(连着几帧; 给了 cur 就光圈也算; strict 且光圈认得出过就只认光圈)。返回满没满。"""
        strict = strict and cur is not None and self.glow_trusted()
        start = time.time()
        samples = []
        hits = 0
        next_click = 0.0
        while time.time() - start < cap:
            now = time.time()
            if poke and now >= next_click:
                self.click()
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            self.hud_ok()
            if cur is not None and self.con_glow(cur):
                self.macro_mark(f'{tag}(满,光圈,{time.time() - start:.2f}s)')
                return True
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            if len(samples) < self.CON_SAMPLE_MAX:
                samples.append(f'{time.time() - start:.2f}:{con:.2f}')
            hits = hits + 1 if con >= self.SK_CON_ENOUGH else 0
            if hits >= self.SK_CON_FULL_CONFIRM and not strict:
                self.macro_mark(f'{tag}(满,{time.time() - start:.2f}s)')
                return True
            self.macro_wait(self.SK_CON_POLL)
        self.macro_mark(f'{tag}(没满,{time.time() - start:.2f}s)')
        self.logger.info(f'{tag}: {who} 协奏 {" ".join(samples)}')
        return False

    def glow_slot(self, frame, slot):
        """队伍栏第 slot 格(0 起)头像外面那圈细白环 -> 落在同一半径上的上半圈角度占比(0~1)。"""
        H, W = frame.shape[:2]
        s = H / 2160
        cx = W - (3840 - 3586.5) * s
        cy = (500 + 264 * slot) * s
        R = int(round(125 * s)) + 3
        xa, ya = int(cx) - R, int(cy) - R
        if xa < 0 or ya < 0 or xa + 2 * R + 1 > W or ya + 2 * R + 1 > H:
            return 0.0
        crop = frame[ya:ya + 2 * R + 1, xa:xa + 2 * R + 1, :3]
        white = np.ascontiguousarray(crop.min(axis=2)).astype(np.float32)
        polar = cv2.warpPolar(white, (R, 360), (cx - xa, cy - ya), R, cv2.WARP_POLAR_LINEAR)
        rows = polar[225:331]
        d = max(1, int(round(4.5 * s)))
        r = np.arange(int(80 * s), int(118 * s))
        ridge = rows[:, r] - np.maximum(rows[:, r - d], rows[:, r + d])
        k = ridge.argmax(axis=1)
        hit = ridge[np.arange(len(rows)), k] > 40
        if not hit.any():
            return 0.0
        hist = np.bincount(r[k][hit], minlength=int(r[-1]) + 2)
        win = max(2, int(round(4 * s)))
        return float(np.convolve(hist, np.ones(win), 'valid').max()) / len(rows)

    def glow_now(self, cur):
        """当前帧 cur 以外两格的光圈够不够"协奏满"。同一帧只算一次, 顺手数连着几帧(换人从头数)。"""
        frame = self.task.frame
        if frame is None:
            return False
        same_cur = getattr(self, 'glow_cur', None) == cur.index
        if same_cur and getattr(self, 'glow_frame', None) is frame:
            return self.glow_last
        others = [self.glow_slot(frame, i) for i in range(3) if i != cur.index]
        lo, hi = min(others), max(others)
        on = (lo >= self.GLOW_BOTH and hi >= self.GLOW_ONE) or hi >= self.GLOW_SOLO
        self.glow_hits = ((getattr(self, 'glow_hits', 0) if same_cur else 0) + 1) if on else 0
        self.glow_frame, self.glow_cur, self.glow_last = frame, cur.index, on
        return on

    def con_glow(self, cur):
        """协奏真满 = 光圈连 GLOW_CONFIRM 帧 + 协奏环最近 RING_WINDOW 秒内读到过 >= RING_HI(10-28 通用规则, 不要求同一帧)。
        读当前帧(调用方负责 next_frame)。光圈认出过一次就记下"这台机器光圈认得出"(glow_trusted), 之后 strict 的地方不再只信读数。"""
        now = time.time()
        try:
            ring = float(self.task.get_current_con())
        except Exception:
            ring = 0.0
        if ring >= self.RING_HI:
            self.ring_hi_at = now
        self.glow_now(cur)
        glow = getattr(self, 'glow_hits', 0) >= self.GLOW_CONFIRM
        if glow:
            self.glow_ok = True
        return glow and now - self.ring_hi_at <= self.RING_WINDOW

    def glow_trusted(self):
        return getattr(self, 'glow_ok', False)

    def con_ready(self, cur, settle, tag=None, strict=False):
        """交棒前读协奏: 光圈连 GLOW_CONFIRM 帧 = 满; 认不出光圈时读数连 SK_CON_FULL_CONFIRM 帧 1.00 也算满
        (strict 且光圈认得出过: 读数不算, 只认光圈)。settle 内都没有 = 不满。读数最大值留在 self.con_last(给日志)。"""
        strict = strict and self.glow_trusted()
        start = time.time()
        best = 0.0
        hits = 0
        why = None
        while True:
            self.task.next_frame()
            self.hud_ok()
            if self.con_glow(cur):
                why = '光圈'
                break
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            best = max(best, con)
            hits = hits + 1 if con >= self.SK_CON_ENOUGH else 0
            if hits >= self.SK_CON_FULL_CONFIRM and not strict:
                why = f'con{best:.2f}'
                break
            if time.time() - start >= settle:
                break
            self.macro_wait(self.SK_CON_POLL)
        self.con_last = best
        self.con_why = why
        if tag:
            self.macro_mark(f'{tag}({why or f"没满con{best:.2f}"},{time.time() - start:.2f}s)')
        return why is not None

    # ==================== 处决 ====================

    def break_prompt(self):
        """怪身上有处决提示(屏幕中间那个 F, 排除拾取 F)。读当前帧。不看顶部的谐度满(f_break_full): 那个比提示早好几秒, 那时按 F 是空按。"""
        task = self.task
        try:
            hit = task.find_one('f_break', box=task.box_of_screen(0.2, 0.2, 0.75, 0.8, hcenter=True, vcenter=True),
                                target_height=720, threshold=self.BREAK_CENTER_MIN)
            return bool(hit) and not task.is_pick_f()
        except Exception as e:
            self.logger.info(f'处决提示读失败 {e}')
            return False

    def break_prompt_wide(self):
        """处决提示挂在 Boss 身上跟着满屏跑, 全宽读(避开顶上 Boss 条和底下按键字母)。~50ms 一次, 只在要按 F 那一刻读。"""
        task = self.task
        try:
            hit = task.find_one('f_break', box=task.box_of_screen(0, 0.08, 1, 0.92),
                                target_height=720, threshold=self.BREAK_CENTER_MIN)
            return bool(hit) and not task.is_pick_f()
        except Exception as e:
            self.logger.info(f'处决提示读失败 {e}')
            return False

    def try_break(self, tag, prompt_wait=0.0):
        """Boss 头像外圈满(连 BREAK_RING_FRAMES 帧)且全宽读到中间提示就按 F 处决(最多 BREAK_F_TRIES 下)。
        HUD 连 BREAK_HUD_GONE 帧不在 = 处决上了, 等演出完 HUD 回来; HUD 不藏(矩阵): 外圈清空 = 处决演完了, 马上返回。
        外圈不满 / 没提示: 读几帧就走, 不按。按了 BREAK_NOHUD_CAP 外圈还满: 没处决上。调用方没处决上就照原流程。
        prompt_wait: 外圈满了读提示读这么久(调用方刚点过 A, 打上了提示才出); 0 = 只读 BREAK_PROMPT_READS 帧。
        返回处决上没有(返回时处决演出已经结束)。"""
        ring = 0
        for _ in range(self.BREAK_RING_FRAMES + 1):
            self.task.next_frame()
            if self.hud_ok() and self.break_ring():
                ring += 1
                if ring >= self.BREAK_RING_FRAMES:
                    break
            else:
                ring = 0
            self.macro_wait(0.02)
        if ring < self.BREAK_RING_FRAMES:
            return False
        prompt = False
        p_start = time.time()
        reads = 0
        while reads < self.BREAK_PROMPT_READS or time.time() - p_start < prompt_wait:
            if self.break_prompt_wide():
                prompt = True
                break
            reads += 1
            self.task.next_frame()
        if not prompt:
            self.macro_mark(f'{tag}外圈满没提示(不按)')
            return False
        presses = gone = off = 0
        first_f = last_f = None
        while gone < self.BREAK_HUD_GONE:
            now = time.time()
            if first_f is not None and now - first_f >= self.BREAK_NOHUD_CAP:
                self.macro_mark(f'{tag}处决没出(F x{presses},外圈一直满)')
                return False
            if gone == 0 and presses < self.BREAK_F_TRIES and (last_f is None or now - last_f >= self.BREAK_F_EVERY):
                self.task.send_key('f', down_time=0.05)
                presses += 1
                last_f = time.time()
                if first_f is None:
                    first_f = last_f
            self.macro_wait(0.03)
            self.task.next_frame()
            hud = self.hud_ok()
            gone = 0 if hud else gone + 1
            # 矩阵里处决不藏 HUD: 外圈在处决演出结束那一刻清空
            off = off + 1 if hud and not self.break_ring() else 0
            if off >= self.BREAK_RING_OFF:
                dur = time.time() - first_f
                self.add_freeze_duration(first_f, dur)
                try:
                    self.task.can_break = False
                except Exception:
                    pass
                self.macro_mark(f'{tag}处决(F x{presses},HUD没藏,外圈清空{dur:.2f}s)')
                return True
        try:
            self.task.can_break = False
        except Exception:
            pass
        self.macro_mark(f'{tag}处决(F x{presses})')
        self.wait_hud_back(tag=f'{tag}处决演出', min_gone=self.BREAK_ANIM_MIN, time_out=self.BREAK_ANIM_CAP)
        return True

    def break_ring(self):
        """boss 头像外圈满了(共振度满, 能处决)。读当前帧。没有 boss 血条的怪永远 False。"""
        try:
            return bool(self.task.find_one('f_break_full', threshold=self.BREAK_RING_MIN))
        except Exception:
            return False

    def break_press(self, tag):
        """提示在就按 F, 按完 BREAK_RESEND 提示还在再按; 提示连 BREAK_GONE_FRAMES 帧不见 = 起手了。
        返回(起手前最后一下 F 的时刻 或 None, 按了几下)。"""
        presses = 0
        last_f = 0.0
        gone = 0
        while True:
            if gone == 0 and time.time() - last_f >= self.BREAK_RESEND:
                if presses >= self.BREAK_PRESSES:
                    return None, presses
                self.task.send_key('f', down_time=0.05)
                presses += 1
                last_f = time.time()
            self.macro_wait(0.03)
            self.task.next_frame()
            if self.hud_ok() and self.break_prompt():
                gone = 0
            else:
                gone += 1
                if gone >= self.BREAK_GONE_FRAMES:
                    return last_f, presses

    # ==================== 心 ====================

    def xn_gauge(self):
        """应世心条 -> (左半, 右半) 有紫色的列占比。读当前帧, 调用方负责 next_frame。"""
        frame = self.task.frame
        out = []
        for x1, y1, x2, y2 in (self.XN_GAUGE_L, self.XN_GAUGE_R):
            box = self.task.box_of_screen_scaled(3840, 2160, x1, y1, x2, y2, hcenter=True)
            crop = frame[box.y:box.y + box.height, box.x:box.x + box.width, :3]
            if crop.size == 0:
                return 0.0, 0.0
            hsv = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_BGR2HSV)
            h, sat, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
            purple = (h >= 125) & (h <= 160) & (sat >= 70) & (v >= 90)
            out.append(float(purple.any(axis=0).mean()))
        return out[0], out[1]

    def xn_ying(self):
        """应世心 -> (满没满, 左半紫, 右半紫)。满 = 重击位的花图标 hsin_h1; 没满时条是紫色格子。读当前帧。"""
        try:
            full = bool(self.task.find_one('hsin_h1', threshold=self.XN_FULL_THRESHOLD))
        except Exception:
            full = False
        L, R = self.xn_gauge()
        return full, L, R

    def xn_icon(self, name):
        """心技能栏的图标模板(hsin_lib1 = 转相 / hsin_lib2 = 万阙垂天解锁)。读当前帧。"""
        try:
            return bool(self.task.find_one(name, threshold=0.7))
        except Exception:
            return False

    def xn_attack_down(self):
        return self.attack_down(self.XN_HOLD_SENDINPUT and self.game_foreground())

    def xn_hold_burn(self, tag, hold=None, repress_min=0.0):
        """按住重击 hold 秒(不给就是 XN1_HOLD), 期间看应世心条有没有被清空(= 步红尘出手)。
        repress_min: 按下时花没亮、后来亮了, 按住够这么久还没清空才松开重按(5R 循环轴试按用, 12W 是 0 = 一亮就重按)。

        返回 (状态, 清空时刻, 按下时刻)。状态:
          'burned'  清空了(时刻可能是 None: 按住期间没读到那一帧, 松手后读到是空的)
          'full'    松手后条还在 = 出的是普通重击(条差太多/被打断) -> 调用方补 A 再按
          'unknown' 条一次都没读出来, 不知道 -> 不重按(重按落在步红尘演出里会再起一个重击)
        """
        hold = self.XN1_HOLD if hold is None else hold
        si = self.xn_attack_down()
        start = time.time()
        press_at = start
        self.macro_mark(f'{tag}按住({"SI" if si else "PM"})')
        seen_full = False
        burned_at = None
        low_at = None
        icon_since = None
        stale = 0
        lit_at_press = None
        lit_hits = 0
        trace = []
        try:
            while time.time() - press_at < hold:
                self.task.next_frame()
                if self.hud_ok() and burned_at is None:
                    now = time.time()
                    full, L, R = self.xn_ying()
                    if len(trace) < 30:
                        trace.append(f'{now - start:.2f}:{"满" if full else ""}{L:.2f}/{R:.2f}')
                    if lit_at_press is None:
                        lit_at_press = full
                    lit_hits = lit_hits + 1 if full else 0
                    if (not lit_at_press and lit_hits >= 2 and stale < self.XN1_STALE_MAX
                            and now - press_at >= repress_min):
                        stale += 1
                        self.release_attack()
                        self.macro_wait(0.05)
                        self.xn_attack_down()
                        press_at = time.time()
                        lit_at_press = True
                        icon_since = None
                        self.macro_mark(f'{tag}按下后才满,重按({now - start:.2f}s)')
                        continue
                    icon_since = (icon_since or now) if full else None
                    if (full and stale < self.XN1_STALE_MAX and now - press_at >= self.XN1_STALE_MIN
                            and now - icon_since >= self.XN1_STALE_FULL):
                        stale += 1
                        self.release_attack()
                        self.macro_wait(0.05)
                        self.xn_attack_down()
                        press_at = time.time()
                        icon_since = None
                        self.macro_mark(f'{tag}满了没出,重按({now - start:.2f}s)')
                        continue
                    if full or L >= self.XN_GAUGE_VALID:
                        seen_full = True
                        low_at = None
                    elif seen_full and L < self.XN_CONSUMED:
                        # 连着两帧空才算(特效闪一下会压暗一帧); 时刻记第一帧 —— 转相解锁从这一刻算
                        if low_at is None:
                            low_at = now
                        else:
                            burned_at = low_at
                            self.macro_mark(f'{tag}步红尘出手({burned_at - start:.2f}s)')
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        if burned_at is not None:
            self.macro_mark(f'{tag}松开')
            return 'burned', burned_at, start
        # 按住期间没读到清空: 松手后再读两帧, 条还在 = 真没出步红尘
        full = empty = 0
        for _ in range(6):
            self.task.next_frame()
            if self.hud_ok():
                is_full, L, R = self.xn_ying()
                if is_full or L >= self.XN_GAUGE_VALID:
                    full += 1
                    seen_full = True
                elif seen_full and L < self.XN_CONSUMED:
                    empty += 1
                if full >= 2 or empty >= 2:
                    break
            self.macro_wait(0.02)
        state = 'full' if full >= 2 else ('burned' if empty >= 2 else 'unknown')
        self.macro_mark(f'{tag}松开({state})')
        self.logger.info(f'{tag}: 按住 {hold:.1f}s 没读到应世心清空 -> {state} — {" ".join(trace)}')
        return state, None, start

    def xn_r1_ready(self):
        """转相能不能按: R 图标是转相(hsin_lib1)而且亮着。读当前帧。

        不查 CD(check_cd=False): 那一路每换一帧就 OCR 一次技能栏, 解锁那 0.3s 的窗口里读不了几次; 转相也没有 CD 数字。
        """
        try:
            lit = bool(self.available('liberation', check_color=True, check_cd=False))
        except Exception:
            lit = False
        return lit and self.xn_icon('hsin_lib1')

    def xn_r1_rescue(self, xn, tag):
        """转相第一次没放出去: 补到放出去为止(上限 XN1_R1_RESCUE_CAP)。返回放出去没有。

        每一轮看现在卡在哪:
          转相图标亮着(连两帧)       -> 按 R(被吞了就再来)
          应世心是满的(花图标 / 右半到线) -> 再按住出步红尘, 清空后等解锁
          都不是                     -> 普攻攒应世心
          HUD 不在(被击飞/演出)       -> 等它回来
        一直既看不到转相图标也读不到应世心条 = 已经进照世相了(转相其实出了只是没看到演出), 不再按 R ——
        照世相里按 R 放的是借相。
        """
        start = time.time()
        rounds = 0
        lost = 0
        self.macro_mark(f'{tag}转相补救开始')
        while time.time() - start < self.XN1_R1_RESCUE_CAP:
            rounds += 1
            self.task.next_frame()
            if not self.hud_ok():
                self.wait_hud_up(cap=1.0, tag=f'{tag}转相补救等HUD')
                continue
            lit = 0
            for _ in range(self.XN1_R1_READS):
                if self.xn_r1_ready():
                    lit += 1
                self.task.next_frame()
            if lit >= self.XN1_R1_READS:
                lost = 0
                if self.fire_r(xn, f'{tag}转相补#{rounds}', self.XN_R_WINDOW):
                    self.macro_mark(f'{tag}转相补救成功({time.time() - start:.2f}s)')
                    return True
                continue
            full, L, R = self.xn_ying()
            if full or L >= self.XN_GAUGE_VALID or self.xn_icon('hsin_lib1'):
                lost = 0
            else:
                lost += 1
                if lost >= self.XN1_R1_LOST_ROUNDS:
                    self.macro_mark(f'{tag}转相补救: 已不在应世相(当转相出过了)')
                    return True
            if full or (L >= self.XN_GAUGE_VALID and R >= self.XN_YING_READY):
                state, burned_at, _hold_at = self.xn_hold_burn(f'{tag}补步红尘#{rounds}')
                if state == 'burned':
                    # 清空后 2.06s 解锁: 等到点(期间亮了就提前走), 下一轮去按
                    anchor = burned_at if burned_at is not None else time.time() - 0.3
                    due = anchor + self.XN1_R1_UNLOCK + self.XN1_R1_MARGIN
                    while time.time() < due:
                        self.task.next_frame()
                        if self.hud_ok() and self.xn_r1_ready():
                            break
                        self.macro_wait(0.01)
                continue
            self.macro_spam(self.XN1_R1_RESCUE_A, self.XN1_FILL_EVERY, tag=f'{tag}补应世心A#{rounds}')
        self.macro_mark(f'{tag}转相补救(cap,{rounds}轮)')
        return False

    def xn_hold_until_gone(self, cap, tag, watch_ready=False, stall=None):
        """按住左键到队伍 HUD 消失(临寰宇出手)。消失后再按 XN2_RELEASE_AFTER_GONE 秒松手。返回出手没有。

        watch_ready: 按住期间看重击位, 人像出来 XN2_READY_REPRESS 还没出手 = 这一下按住断了(被打断), 松开重按。
        stall: 按住期间数雕像, 这么多秒没再翻一颗也没出手就松手返回(交给调用方普攻补完再按)。
        """
        si = self.xn_attack_down()
        start = time.time()
        self.macro_mark(f'{tag}按住({"SI" if si else "PM"})')
        gone = 0
        fired = False
        ready_since = None
        repress = 0
        last_count = None
        moved_at = start
        try:
            while time.time() - start < cap:
                self.task.next_frame()
                if not self.hud_ok():
                    gone += 1
                    if gone >= self.LIB_GONE_CONFIRM:
                        fired = True
                        break
                else:
                    gone = 0
                    if stall is not None:
                        try:
                            count = self.xn_statue_count()
                        except Exception as e:
                            count = last_count
                            self.logger.info(f'xn_statue_count failed ({e})')
                        if count != last_count:
                            last_count = count
                            moved_at = time.time()
                        elif time.time() - moved_at >= stall:
                            self.macro_mark(f'{tag}停在{count}颗({time.time() - start:.2f}s)')
                            break
                    if watch_ready and repress < self.XN2_REPRESS_MAX:
                        if self.xn_slot_state() == 'ready':
                            ready_since = ready_since or time.time()
                            if time.time() - ready_since >= self.XN2_READY_REPRESS:
                                repress += 1
                                ready_since = None
                                self.release_attack()
                                self.macro_wait(0.05)
                                self.xn_attack_down()
                                self.macro_mark(f'{tag}人像出来没出手,重按#{repress}({time.time() - start:.2f}s)')
                        else:
                            ready_since = None
                self.macro_wait(0.02)
            if fired:
                self.macro_mark(f'{tag}临寰宇出手({time.time() - start:.2f}s)')
                self.macro_wait(self.XN2_RELEASE_AFTER_GONE)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}松开({"出手" if fired else "没出"})')
        return fired

    @staticmethod
    def glyph_vec(a):
        v = a.astype(np.float32).flatten()
        v = v - v.mean()
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    def xn_slot_glyph(self):
        """重击位 16x16 灰度(只取圆内像素), 已归一化。心不在场/HUD 不在返回 None。读当前帧。"""
        in_team, index, _count = self.task.in_team()
        if not in_team or index != self.index:
            return None
        self.hud_seen_at = time.time()
        ref = getattr(self, '_slot_ref', None)
        if ref is None:
            yy, xx = np.mgrid[:96, :96]
            disk = ((((xx - 48) ** 2 + (yy - 44) ** 2) <= 25 ** 2)).astype(np.float32)
            mask = cv2.resize(disk[18:70, 22:74], (16, 16), interpolation=cv2.INTER_AREA) > 0.99

            def tpl(chunks):
                raw = base64.b64decode(''.join(chunks))
                return self.glyph_vec(np.frombuffer(raw, np.uint8).reshape(16, 16).astype(np.float32)[mask])

            ref = self._slot_ref = (mask, disk, tpl(self.XN_TY_GLYPH), tpl(self.XN_READY_GLYPH))
        mask, disk, _ty, _rd = ref
        x, y, w, h = self.XN_TY_SLOT
        box = self.task.box_of_screen_scaled(3840, 2160, x, y, x + w, y + h, hcenter=True)
        frame = self.task.frame
        cx, cy = int(box.x + box.width / 2), int(box.y + box.height / 2)
        half = int(box.height * 1.1)
        icon = frame[max(0, cy - half):cy + half, max(0, cx - half):cx + half, :3]
        g = cv2.cvtColor(cv2.resize(icon, (96, 96), interpolation=cv2.INTER_AREA),
                         cv2.COLOR_BGR2GRAY).astype(np.float32) * disk
        v = cv2.resize(g[18:70, 22:74], (16, 16), interpolation=cv2.INTER_AREA)
        return self.glyph_vec(v[mask])

    def xn_slot_state(self):
        """统御众机里重击位: 'statue'(照世心还没打空) / 'ready'(人像, 临寰宇可按) / '?' / None(HUD 不在)。读当前帧。"""
        try:
            v = self.xn_slot_glyph()
            if v is None:
                return None
            _mask, _disk, ty, rd = self._slot_ref
            self._ty_score = float(v @ ty)
            self._rd_score = float(v @ rd)
            if self._rd_score >= self.XN_READY_MIN:
                return 'ready'
            if self._ty_score >= self.XN_TY_MIN:
                return 'statue'
            return '?'
        except Exception as e:
            self.logger.info(f'xn_slot_state probe failed ({e})')
            return '?'

    def dodge(self, tag):
        """闪避(右键)。"""
        self.task.mouse_down(key='right')
        try:
            self.macro_wait(self.XN2_DODGE_HOLD)
        finally:
            self.task.mouse_up(key='right')
        self.macro_mark(f'{tag}闪避')

    def xn_statue_reset(self):
        self._statue_streak = [0] * len(self.XN_STATUE_XS)
        self._statue_latch = [False] * len(self.XN_STATUE_XS)

    def xn_statue_count(self):
        """照世心雕像从右往左连续翻了几颗(翻过的记住, 每次进统御众机先 xn_statue_reset)。读当前帧。
        照抄心雷穗 v1.1.0, 加了一条: 同一帧新记住 >= XN_STATUE_BURST 颗 = 闪光, 这一帧不算。"""
        frame = self.task.frame
        hw = self.XN_STATUE_HW
        newly = []
        for k, x in enumerate(self.XN_STATUE_XS):
            if self._statue_latch[k]:
                continue
            red = glow = 0.0
            for y, kind in ((self.XN_STATUE_Y, 'red'), (self.XN_STATUE_UP_Y, 'glow')):
                box = self.task.box_of_screen_scaled(3840, 2160, x - hw, y - hw, x + hw, y + hw, hcenter=True)
                crop = frame[box.y:box.y + box.height, box.x:box.x + box.width, :3]
                if crop.size == 0:
                    continue
                hsv = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_BGR2HSV)
                h, sat, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
                if kind == 'red':
                    red = float((((h < 12) | (h > 160)) & (sat > 90) & (v > 110)).mean())
                else:
                    glow = float(((v > 235) & (sat < 60)).mean())
            hit = red >= self.XN_STATUE_RED or glow >= self.XN_STATUE_GLOW
            self._statue_streak[k] = self._statue_streak[k] + 1 if hit else 0
            if self._statue_streak[k] >= self.XN_STATUE_CONFIRM:
                newly.append(k)
        if len(newly) >= self.XN_STATUE_BURST:
            for k in newly:
                self._statue_streak[k] = 0
        else:
            for k in newly:
                self._statue_latch[k] = True
        n = 0
        for latched in reversed(self._statue_latch):
            if not latched:
                break
            n += 1
        return n

    def xn_ty_attack(self, tag, start, until_count, min_t, cap_t, every=None):
        """统御众机里点普攻(every 一下, 默认 XN2_TY_A_EVERY), 每帧数雕像、看重击位。时间从 start 起算:
        min_t 之前不停; 翻满 until_count 颗(或人像出来)就停; 到 cap_t 照停。返回 (原因, 翻了几颗)。"""
        every = self.XN2_TY_A_EVERY if every is None else every
        n = 0
        next_click = 0.0
        count = 0
        ready_hits = 0
        counts = []
        last_count = -1
        why = 'cap'
        while time.time() - start < cap_t:
            if time.time() >= next_click:
                self.tap()
                n += 1
                next_click = time.time() + max(0.0, every - self.A_TAP)
            self.task.next_frame()
            st = self.xn_slot_state()
            if st is not None:
                try:
                    count = self.xn_statue_count()
                except Exception as e:
                    self.logger.info(f'xn_statue_count failed ({e})')
                if count != last_count:
                    last_count = count
                    if len(counts) < 12:
                        counts.append(f'{count}@{time.time() - start:.2f}')
            ready_hits = ready_hits + 1 if st == 'ready' else 0
            if ready_hits >= self.XN2_READY_CONFIRM:
                why = 'ready'
                break
            if count >= until_count and time.time() - start >= min_t:
                why = 'count'
                break
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}(A x{n},翻{count},{why},{time.time() - start:.2f}s)')
        if counts:
            self.logger.info(f'{tag}: 照世心雕像翻面(颗@秒) — {" ".join(counts)}')
        return why, count

    def xn_tongyu_phase(self, tag, cap):
        """统御众机里一直普攻到重击位换成人像(照世心打空)。返回 'ready'(人像连读几帧) / 'cap'(到上限没看到人像)。"""
        start = time.time()
        n = 0
        next_click = 0.0
        hits = 0
        why = 'cap'
        last = None
        statue_seen = False
        while time.time() - start < cap:
            now = time.time()
            if now >= next_click:
                self.tap()
                n += 1
                next_click = time.time() + max(0.0, self.XN2_TY_A_EVERY - self.A_TAP)
            self.task.next_frame()
            st = self.xn_slot_state()
            last = st
            statue_seen = statue_seen or st == 'statue'
            hits = hits + 1 if st == 'ready' else 0
            if hits >= self.XN2_READY_CONFIRM:
                why = 'ready'
                break
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}照世心{"打空" if why == "ready" else "没看到打空(cap)"}'
                        f'(A x{n},{time.time() - start:.2f}s{"" if statue_seen else ",没见过雕像"})')
        if why != 'ready':
            self.logger.info(f'{tag}: {cap:.1f}s 没看到重击位换人像(最后读成 {last}, '
                             f'雕像{getattr(self, "_ty_score", -1):.2f} 人像{getattr(self, "_rd_score", -1):.2f})')
        return why

    def xn_wait_lib2(self, cap, build=False):
        """等 R 位变成万阙垂天(hsin_lib2 连读几帧)。build: 期间点普攻攒大招能量。返回 (认出没有, 用时)。"""
        start = time.time()
        hits = 0
        next_tap = 0.0
        while time.time() - start < cap:
            if build and time.time() >= next_tap:
                self.tap()
                next_tap = time.time() + max(0.0, self.A_INTERVAL - self.A_TAP)
            self.task.next_frame()
            if self.hud_ok():
                hits = hits + 1 if self.xn_icon('hsin_lib2') else 0
                if hits >= self.XN2_LIB2_READS:
                    return True, time.time() - start
            self.macro_wait(0.02)
        return False, time.time() - start

    def xn_ensure_con(self, xn, tag, use_e=True, cap=None):
        """心切莉莉之前协奏要真满: 读一下, 不满就 E -> 普攻, 每下之后读一次。返回满没满。
        use_e=False(5R): 只点普攻, E 留给下一圈心0; cap 不给就是 XN2_CON_CAP。"""
        cap = self.XN2_CON_CAP if cap is None else cap
        start = time.time()
        if self.con_ready(xn, self.XN2_CON_READ, strict=True):
            return True
        samples = [f'0:{self.con_last:.2f}']
        taps = 0
        used_e = False
        while time.time() - start < cap:
            if use_e and not used_e and time.time() - getattr(xn, 'last_res', 0) >= self.XN_E_CD:
                used_e = True
                self.spam_key(xn.send_resonance_key, 0.26, interval=0.06, tag=f'{tag}补协奏E')
                xn.record_resonance_use()
            else:
                self.tap()
                taps += 1
                self.macro_wait(max(0.0, self.A_INTERVAL - self.A_TAP))
            full = self.con_ready(xn, 0.03, strict=True)
            if len(samples) < 20:
                samples.append(f'{time.time() - start:.2f}:{self.con_last:.2f}')
            if full:
                self.macro_mark(f'{tag}补协奏(满,E{int(used_e)},A x{taps},{time.time() - start:.2f}s)')
                return True
        self.macro_mark(f'{tag}补协奏(cap,E{int(used_e)},A x{taps},{time.time() - start:.2f}s)')
        self.logger.warning(f'{tag}: 心补了 {time.time() - start:.1f}s 协奏还是没满, 莉莉拿不到变奏'
                            f' — {" ".join(samples)}')
        return False

    def seg_xn0(self, xn, nxt, tag, fill=None, a_until=None):
        """心·插段(莉莉 E 之后切过来, 普通入场·应世相): 点 2A(~1s) -> E -> 切 nxt(普通入场)。
        12W: nxt = 莉莉, 2A 后接着垫应世心(XN0_FILL); 5R: nxt = 锁暝, 不垫, 点 A 时长 a_until。"""
        fill = self.XN0_FILL if fill is None else fill
        a_until = self.XN0_A_UNTIL if a_until is None else a_until
        t_in = getattr(xn, 'last_switch_in_time', 0.0) or time.time()
        self.wait_until_time(t_in + self.XN0_A_LEAD)
        start = time.time()
        taps = 0
        while time.time() - start < a_until:
            self.tap()
            taps += 1
            left = a_until - (time.time() - start)
            self.macro_wait(max(0.0, min(self.XN0_A_EVERY - self.A_TAP, left)))
        self.task.next_frame()
        _full, L, _R = self.xn_ying()
        self.macro_mark(f'{tag}2A(A x{taps},左半{L:.2f})')
        if fill:
            self.xn0_fill(xn, start, tag)
        if self.XN0_E and self.press_res_until_cd(xn, self.XN0_E_CAP, f'{tag}E', ready_grace=0.15):
            xn.record_resonance_use()
        self.need(self.switch_retry(nxt, f'{tag}切{nxt.name[:3]}', read_con=True), f'hsin -> {nxt.name}')

    def xn0_fill(self, xn, start, tag):
        """心0 的 2A 之后接着点普攻垫应世心: 左半连 2 帧 >= XN0_FILL_L 或花图标亮(已经满了)就停, 从第一下 A 起
        XN0_FILL_CAP 封顶。满的时候条变粉红、紫色判据不准, 先看花图标。"""
        taps = hits = 0
        why = 'cap'
        L = R = 0.0
        next_tap = time.time()
        while time.time() - start < self.XN0_FILL_CAP:
            self.task.next_frame()
            if self.hud_ok() and self.so_on_field(xn):
                full, L, R = self.xn_ying()
                if full:
                    why = '满'
                    break
                hits = hits + 1 if L >= self.XN0_FILL_L else 0
                if hits >= 2:
                    why = '够'
                    break
            if time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + max(0.0, self.XN0_A_EVERY - self.A_TAP)
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}垫应世心({why},{L:.2f}/{R:.2f},A x{taps},{time.time() - start:.2f}s)')

    def xn1_dodge_fill(self, xn, tag, tap_from=None, tap_every=None, early_after=None, early_r=None,
                       dodge_keys=None):
        """心1 进场(0 链): 点 A -> 双手交叉那下闪避 -> 花图标(应世心满)连 2 帧就返回(调用方按住)。
        进场就亮: 不点不闪, 等到 XN1_FULL_HOLD_AT; 闪避前亮了: 不闪; 闪避后到 XN1_POST_DODGE_CAP 还不亮: 照按。
        tap_from / tap_every 不给就用 12W 的 XN1_TAP_FROM / XN1_TAP_EVERY。
        early_after 给了(5R): 闪避后这么久不点 A, 右半 >= early_r 就返回 'early'(调用方试按); 不够(或那一帧没读到)就接着
        点 A, 之后每帧看, 右半连 2 帧 >= early_r 也返回 'early', 花先亮了照旧返回 'full'。
        dodge_keys 给了(5R 循环): 带方向闪避(dodge_dir), 不给是右键往后跳。
        返回 'full' / 'early' / 'cap' / 'blind'。"""
        tap_from = self.XN1_TAP_FROM if tap_from is None else tap_from
        tap_every = self.XN1_TAP_EVERY if tap_every is None else tap_every
        early_r = self.XN_YING_LATE if early_r is None else early_r
        sw = self.switch_press_at
        t_in = getattr(xn, 'last_switch_in_time', 0.0) or sw
        t_in = max(t_in, sw)
        self.wait_until_time(sw + self.XN1_FULL_FROM)
        dodge_at = t_in + self.XN1_DODGE_AT
        next_tap = t_in + tap_from
        taps = hits = 0
        valid_seen = False
        dodged_at = None
        early_pending = False
        early_fill = False
        early_hits = taps_at_dodge = 0
        full = False
        L = R = 0.0
        arr = '?'
        why = 'cap'
        while True:
            now = time.time()
            if dodged_at is None and now >= dodge_at:
                if dodge_keys:
                    self.dodge_dir(dodge_keys, tag)
                else:
                    self.dodge(tag)
                dodged_at = time.time()
                taps_at_dodge = taps
                early_pending = early_after is not None
                next_tap = float('inf') if early_pending else dodged_at + self.XN1_DODGE_TO_A
                hits = 0
                continue
            if dodged_at is not None:
                if now - dodged_at >= self.XN1_POST_DODGE_CAP:
                    break
                if not valid_seen and now - dodged_at >= self.XN1_POST_DODGE_BLIND:
                    why = 'blind'
                    break
            if now >= next_tap:
                self.tap(self.XN1_A_HOLD)
                taps += 1
                next_tap = time.time() + max(0.0, tap_every - self.XN1_A_HOLD)
            self.task.next_frame()
            ok = fresh = False
            if self.hud_ok() and self.so_on_field(xn):
                full, L, R = self.xn_ying()
                fresh = True
                if arr == '?' and (full or L >= self.XN_GAUGE_VALID):
                    arr = '满' if full else f'{L:.2f}/{R:.2f}'
                valid_seen = valid_seen or full or L >= self.XN_GAUGE_VALID
                ok = full
            hits = hits + 1 if ok else 0
            if hits >= 2:
                why = 'full'
                break
            if early_pending and time.time() - dodged_at >= early_after:
                early_pending = False
                if fresh and (full or (L >= self.XN_GAUGE_VALID and R >= early_r)):
                    why = 'early'
                    break
                next_tap = time.time()
                early_fill = True
            elif early_fill and fresh:
                early_hits = early_hits + 1 if L >= self.XN_GAUGE_VALID and R >= early_r else 0
                if early_hits >= 2:
                    why = 'early'
                    break
            self.macro_wait(0.02)
        el = time.time() - sw
        where = '闪后' if dodged_at is not None else '闪前'
        if why == 'full' and taps == 0 and dodged_at is None:
            self.macro_mark(f'{tag}上场应世心就满({el:.2f}s)')
            self.wait_until_time(sw + self.XN1_FULL_HOLD_AT)
        elif why == 'full':
            self.macro_mark(f'{tag}应世心满({where},进场{arr},A x{taps},{el:.2f}s)')
        elif why == 'early':
            fill = f',闪后补A x{taps - taps_at_dodge}' if early_fill else ''
            self.macro_mark(f'{tag}闪后试按(进场{arr},{L:.2f}/{R:.2f},A x{taps}{fill},{el:.2f}s)')
        else:
            self.macro_mark(f'{tag}应世心{"读不到" if why == "blind" else "不够"}(照按,进场{arr},{L:.2f}/{R:.2f},A x{taps},{el:.2f}s)')
        return why

    def xn1_tap_until_full(self, xn, tag, tap_every):
        """点 A 到花图标连 2 帧(应世心满), 上限 XN1_POST_DODGE_CAP。试按出成普通重击之后用, 返回满没满。"""
        start = time.time()
        next_tap = start + 0.08      # 先读两帧: 普通重击自己可能已经把条补满了
        taps = hits = 0
        while time.time() - start < self.XN1_POST_DODGE_CAP:
            self.task.next_frame()
            lit = self.hud_ok() and self.so_on_field(xn) and self.xn_ying()[0]
            hits = hits + 1 if lit else 0
            if hits >= 2:
                break
            if time.time() >= next_tap:
                self.tap(self.XN1_A_HOLD)
                taps += 1
                next_tap = time.time() + max(0.0, tap_every - self.XN1_A_HOLD)
            self.macro_wait(0.02)
        ok = hits >= 2
        self.macro_mark(f'{tag}试按没出,点A等花({"满" if ok else "cap"},A x{taps},{time.time() - start:.2f}s)')
        return ok

    def seg_xn1(self, xn, so, tag, echo=None, e_after=None, tap_from=None, tap_every=None,
                early_after=None, early_r=None, early_hold=None, dodge_keys=None, repress_min=0.0):
        """心·第一段(变奏·应世相): A -> 闪避 -> 按住出步红尘 -> [Q] -> 连按 R 转相 -> 演完 -> [照世相 E]
        -> 同奏切锁暝。返回转相出没出(= 锁暝带不带变奏)。echo / e_after 不给就用 12W 的 XN1_ECHO / LQ_XN1_E;
        tap_from / tap_every / early_after / early_r 传给 xn1_dodge_fill。
        early_after 给了(5R): 闪避后试按 early_hold 秒, 清空了直接去按 R; 出成普通重击就点 A 等花再走原来的按住。"""
        echo = self.XN1_ECHO if echo is None else echo
        e_after = self.LQ_XN1_E if e_after is None else e_after
        why = self.xn1_dodge_fill(xn, tag, tap_from=tap_from, tap_every=tap_every,
                                  early_after=early_after, early_r=early_r, dodge_keys=dodge_keys)
        state = 'unknown'
        burned_at = None
        tried = False
        if why == 'early':
            state, burned_at, _hold_at = self.xn_hold_burn(f'{tag}试按', hold=early_hold, repress_min=repress_min)
            if state == 'full':
                self.xn1_tap_until_full(xn, tag, self.XN1_TAP_EVERY if tap_every is None else tap_every)
            else:
                # 'burned' 去按 R; 'unknown'(条一次都没读出来)不重按, 跟原来按住后读不到一样
                tried = True
        for attempt in range(0 if tried else 1 + self.XN1_RETRIES):
            if attempt > 0:
                # 上一次松手后条还在 = 出的是普通重击: 花图标亮着就直接重按, 没亮补两下 A 再按
                self.task.next_frame()
                lit = self.hud_ok() and self.xn_ying()[0]
                if not lit:
                    for _ in range(self.XN1_RETRY_A):
                        self.tap(self.XN1_A_HOLD)
                        self.macro_wait(self.XN1_FILL_EVERY)
                self.macro_mark(f'{tag}重击重按#{attempt}{"(亮着)" if lit else ""}')
            state, burned_at, _hold_at = self.xn_hold_burn(f'{tag}重击' if attempt == 0 else f'{tag}重击#{attempt}')
            if state != 'full':
                break
        fired = False
        if state == 'full':
            # 步红尘没出, 转相不会解锁, 连按 R 白等: 直接走补救
            self.logger.warning(f'{tag}: 重按 {self.XN1_RETRIES} 次应世心都没清空(步红尘没出), 转相多半放不出来')
        else:
            if echo:
                self.echo_if_ready(xn, f'{tag}Q', self.XN1_ECHO_CAP)
            fired = self.fire_r(xn, f'{tag}转相', self.XN1_R1_SPAM)
            if fired and burned_at is not None:
                self.macro_mark(f'{tag}转相出手(清空后{time.time() - burned_at:.2f}s)')
        if not fired:
            fired = self.xn_r1_rescue(xn, tag)
        if not fired:
            self.logger.warning(f'{tag}: 补了 {self.XN1_R1_RESCUE_CAP:.0f}s 转相还是没放出去 — 锁暝拿不到同奏, 照常往下走')
        else:
            self.wait_hud_back(tag=f'{tag}转相演出', min_gone=self.XN_R_ANIM_MIN, time_out=self.XN_R_ANIM_CAP)
            if e_after:
                # 照世相 E 跟应世相 E 冷却分开, 不记 last_res
                self.press_res_until_cd(xn, self.LQ_XN1_E_CAP, f'{tag}照世相E', ready_grace=0.15)
        # 同奏 5 秒: 转相一演完立刻切, 锁暝不用满协奏也是变奏入场(旋雷破·同奏)
        self.need(self.switch_retry(so, f'{tag}切锁暝', assume_con=fired), 'hsin -> suoming')
        return fired

    def xn_break(self, tag, back_at):
        """万阙垂天演完(HUD 回来 = back_at): 屏幕中间有处决提示就 A 一下 + 处决; 没提示不等。返回处决了没有。
        HUD 回来后还有 XN2_R2_FREEZE 的白屏时停, 里面按的键被吞, 有提示也等时停过去再按。"""
        seen = False
        for _ in range(self.BREAK_READS):
            self.task.next_frame()
            if self.hud_ok() and self.break_prompt():
                seen = True
                break
            self.macro_wait(0.02)
        if not seen:
            return False
        self.wait_until_time(back_at + self.XN2_R2_FREEZE)
        self.tap()
        self.macro_wait(self.XN_BREAK_A_TO_F)
        hit_at, n = self.break_press(tag)
        try:
            self.task.can_break = False
        except Exception:
            pass
        if hit_at is None:
            self.macro_mark(f'{tag}处决?(F x{n},提示一直在)')
            self.logger.warning(f'{tag}: 处决 F 按了 {n} 下提示一直没消失(一直被吞?), 不等了')
            return False
        self.wait_until_time(hit_at + self.XN_BREAK_ANIM)
        self.wait_hud_up(cap=1.0, tag=f'{tag}处决后HUD')
        self.macro_mark(f'{tag}处决(F x{n})')
        return True

    def seg_xn2(self, xn, intro, tag, echo_first=False, con_e=True, con_cap=None):
        """心·第二段(锁暝协奏满 -> 变奏·照世相, 直接进统御众机): [Q] -> 普攻到第 1 颗雕像翻面(A1 落下) -> 接着点到第 2 颗
        (A2 楼阁打中) -> 闪避 -> 普攻到第 7 颗 -> 按住到临寰宇(雕像停住/按住断了就松开补普攻再按) -> 演完
        -> 万阙垂天(认 hsin_lib2) -> 演完 -> 协奏补满。返回协奏满没满(交棒给调用方)。
        入场: 锁暝这圈没处决上、Boss 能处决就在第一下 A 之前处决(try_break)。
        echo_first(5R): 变奏入场演出里先放声骸(演出里能放, 第一下 A 本来就要等 XN2_LEAD)。
        con_e / con_cap 传给 xn_ensure_con(5R 补协奏不按 E)。"""
        if echo_first:
            self.wait_until_time(self.switch_press_at + self.R5_XN2_ECHO_AT)
            self.echo_if_ready(xn, f'{tag}Q', self.R5_XN2_ECHO_CAP)
        self.wait_until_time(self.switch_press_at + self.XN2_LEAD)
        if self.XN2_ENTRY_BREAK and not getattr(self, 'broke_this_lap', False):
            self.try_break(f'{tag}入场')
        self.broke_this_lap = False
        self.xn_statue_reset()
        why, count = self.xn_ty_attack(f'{tag}A1', time.time(), 1, self.XN2_DODGE_MIN, self.XN2_DODGE_CAP)
        if why == 'count':
            why, count = self.xn_ty_attack(f'{tag}A2', time.time(), self.XN2_DODGE_COUNT, 0.0, self.XN2_DODGE_AFTER)
        if why != 'ready':
            self.dodge(tag)
            self.macro_wait(self.XN2_DODGE_TO_A)
            self.xn_ty_attack(f'{tag}3A', time.time(), self.XN2_HOLD_AT, 0.0, self.XN2_EMPTY_CAP,
                              every=self.XN2_POST_DODGE_A_EVERY)
        fired = self.xn_hold_until_gone(self.XN2_HOLD_CAP, f'{tag}按住', watch_ready=True, stall=self.XN2_HOLD_STALL)
        for attempt in range(1, 1 + self.XN2_RETRIES):
            if fired:
                break
            # 按满上限没出(照世心没打空 / 被打断): 普攻到人像再按。没拿到变奏(没进统御众机)时先按一下 E
            if attempt == 1 and not intro:
                self.spam_key(xn.send_resonance_key, 0.2, interval=0.06, tag=f'{tag}补E(没变奏)')
                xn.record_resonance_use()
            why = self.xn_tongyu_phase(f'{tag}补#{attempt}', self.XN2_RETRY_CAP)
            fired = self.xn_hold_until_gone(self.XN2_READY_HOLD if why == 'ready' else self.XN2_BLIND_HOLD,
                                            f'{tag}重按#{attempt}', watch_ready=True)
        if not fired:
            self.logger.warning(f'{tag}: 临寰宇一直没出手, 这一轮不放万阙垂天(按 R 会放成借相)')
        else:
            self.wait_hud_back(tag=f'{tag}临寰宇演出', min_gone=self.XN2_ANIM_MIN, time_out=self.XN2_ANIM_CAP)
            if self.XN2_R2_BLIND:
                unlocked = True
            else:
                # 【R 只认 hsin_lib2】: 照世相里 R 一直亮(借相·应世), 只有临寰宇之后、能量够了才换成万阙垂天的图标
                unlocked, t = self.xn_wait_lib2(self.XN2_LIB2_CAP)
                self.macro_mark(f'{tag}万阙垂天{"解锁" if unlocked else "图标没认出(能量不够?)"}({t:.2f}s)')
                if not unlocked:
                    unlocked, t = self.xn_wait_lib2(self.XN2_R2_BUILD_CAP, build=True)
                    self.macro_mark(f'{tag}普攻攒大招能量({"认出万阙垂天" if unlocked else "cap"},{t:.2f}s)')
            fired = False
            back_at = time.time()
            for attempt in range(2):
                if not unlocked:
                    break
                if attempt == 0 and self.XN2_ECHO:
                    self.echo_if_ready(xn, f'{tag}Q', self.XN1_ECHO_CAP)
                fired = self.fire_r(xn, f'{tag}万阙垂天' if attempt == 0 else f'{tag}万阙垂天重按', self.XN_R_WINDOW)
                if fired:
                    self.wait_hud_back(tag=f'{tag}万阙垂天演出', min_gone=self.XN_R_ANIM_MIN, time_out=self.XN_R_ANIM_CAP)
                    back_at = time.time()
                    break
                # 没放出去: 认一下 R 位还是不是万阙垂天, 认得出才重按(收招吞了 / 能量差一点, 普攻攒一下)
                unlocked, t = self.xn_wait_lib2(1.50, build=True)
            if not fired:
                self.logger.warning(f'{tag}: 万阙垂天没放出去'
                                    f'({"能量攒不满/图标没认出, 不照按(会放成借相)" if not unlocked else "按了没出"})')
            else:
                if self.XN2_BREAK:
                    self.xn_break(f'{tag}处决', back_at)
                if self.XN2_E_AFTER_R2 and time.time() - getattr(xn, 'last_res', 0) >= self.XN_E_CD:
                    self.wait_until_time(back_at + self.XN2_R2_FREEZE)
                    if self.press_res_until_cd(xn, self.XN2_E_CAP, f'{tag}E偷伤害', ready_grace=0.15):
                        xn.record_resonance_use()
        return self.xn_ensure_con(xn, tag, use_e=con_e, cap=con_cap)

    # ==================== 锁暝 ====================

    def so_forte_full(self, so):
        """妄执满(E 位是锁妄念/无舍念, 框架的 suoming_forte_e 模板)。读当前帧。"""
        try:
            return bool(so.forte_available())
        except Exception:
            return False

    def so_on_field(self, so):
        """队伍 HUD 在场而且当前是 so(锁暝/谁都行; 刚切人那几帧技能栏还是上一个人的)。读当前帧。"""
        try:
            in_team, index, _count = self.task.in_team()
        except Exception:
            return False
        return bool(in_team) and index == so.index

    def wait_r_lit(self, char, tag, cap, every):
        """R 还在 CD 上就边点普攻边每帧看, 连 2 帧亮了马上走(交给 ensure_r 按)。一进来就亮不点。
        只在读到"不亮"之后才点 A: 原来第一帧没凑够 2 帧就先点了一下(每次 A x1), 守b 多出一段普攻动作才 Q/R。"""
        start = time.time()
        taps = hits = 0
        next_tap = 0.0
        lit = False
        while time.time() - start < cap:
            self.task.next_frame()
            not_ready = False
            if self.so_on_field(char):
                try:
                    ready = bool(char.liberation_available())
                except Exception:
                    ready = False
                hits = hits + 1 if ready else 0
                if hits >= 2:
                    lit = True
                    break
                not_ready = not ready
            if not_ready and time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + max(0.0, every - self.A_TAP)
            self.macro_wait(0.01)
        if taps:
            self.macro_mark(f'{tag}等R亮({"亮" if lit else "cap"},A x{taps},{time.time() - start:.2f}s)')
        return lit

    def so_r_asap(self, so, tag, cap, allow_e):
        """(LQ) 锁暝进场就放 R, R 没好(CD / 能量不够)就一直点 A, 好了马上出, 不站着等。
        每下 A 之后顺手按一下 R; 读到 R 亮: 入场动作里一直按, 入场之后连按 LQ_SO_R_BURST, HUD 没消失就回去点 A。
        allow_e: 没变奏入场(念起, R 锁着)时妄执满了点 E 进念深。放出去 = 按过 R 之后 HUD 连续 LIB_GONE_CONFIRM 帧不在。"""
        sw = self.switch_press_at
        start = time.time()
        taps = bursts = r_n = e_taps = 0
        next_tap = 0.0
        burst_until = backoff_until = 0.0
        last_r = -9.0
        next_e = sw + self.LQ_SO_R_E_FROM
        lit_hits = forte_hits = gone = 0
        fired = False
        while time.time() - start < cap:
            self.task.next_frame()
            now = time.time()
            if not self.hud_ok():
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM and now - last_r < 0.6:
                    fired = True
                    break
                self.macro_wait(0.01)
                continue
            gone = 0
            on = self.so_on_field(so)
            try:
                lit = on and bool(so.liberation_available())
            except Exception:
                lit = False
            lit_hits = lit_hits + 1 if lit else 0
            forte_hits = forte_hits + 1 if on and self.so_forte_full(so) else 0
            if now < burst_until:
                so.send_liberation_key()
                r_n += 1
                last_r = now
            elif lit_hits >= 2 and now >= backoff_until:
                bursts += 1
                burst_until = max(now + self.LQ_SO_R_BURST, sw + self.LQ_SO_R_ENTRY)
                # 一直读成亮却按不出 = 能量差一点: 连按越来越稀, 主要靠每下 A 后的那一下 R
                backoff_until = burst_until + self.LQ_SO_R_BACKOFF * bursts
                so.send_liberation_key()
                r_n += 1
                last_r = now
            elif allow_e and forte_hits >= 2 and not lit and now >= next_e:
                self.so_tap_e(so.get_resonance_key())
                so.record_resonance_use()
                e_taps += 1
                next_e = time.time() + 1.0
            elif now >= next_tap:
                self.tap()
                so.send_liberation_key()
                r_n += 1
                taps += 1
                last_r = time.time()
                next_tap = last_r + max(0.0, self.SO_A_EVERY - self.A_TAP)
            self.macro_wait(self.LIB_KEY_INTERVAL if time.time() < burst_until else 0.01)
        self.task.skip_combat_check = True
        el = time.time() - start
        e_note = f',E x{e_taps}' if e_taps else ''
        if fired:
            so.record_liberation_use()
            self.macro_mark(f'{tag}R_fired(A x{taps},R x{r_n},连按{bursts}次{e_note},{el:.2f}s)')
        else:
            self.macro_mark(f'{tag}R_NOFIRE(cap,A x{taps},R x{r_n},连按{bursts}次{e_note},{el:.2f}s)')
        return fired

    def so_echo(self, so, tag, cap=None, confirm=2, send=None):
        """一直按 Q 到声骸 CD 涨上来(比按之前高 SO_ECHO_RISE, 连 confirm 帧)。返回放出去没有。so 可以是任何人。
        send: 发键函数(默认 so.send_echo_key; 按住左键期间传 SendInput 的)。"""
        cap = self.SO_ECHO_CAP if cap is None else cap
        send = so.send_echo_key if send is None else send
        cd0 = self.read_cd('echo', so)
        start = time.time()
        n = hits = 0
        fired = False
        while time.time() - start < cap:
            send()
            n += 1
            self.task.next_frame()
            up = self.so_on_field(so) and self.read_cd('echo', so) > cd0 + self.SO_ECHO_RISE
            hits = hits + 1 if up else 0
            if hits >= confirm:
                fired = True
                break
            self.macro_wait(0.06)
        if fired:
            so.record_echo_use()
        else:
            self.logger.info(f'{tag}: 按了 {n} 下声骸 CD 没涨(基线 {cd0:.1f}s) — 被收招吞了, 还是读不到数?')
        self.macro_mark(f'{tag}({"出" if fired else "cap"},x{n},{time.time() - start:.2f}s)')
        return fired

    def so_tap_e(self, key):
        """点一下 E, 真的按住 SO_KX_E_TAP 秒。"""
        self.task.send_key_down(key)
        try:
            self.macro_wait(self.SO_KX_E_TAP)
        finally:
            self.task.send_key_up(key)

    def so_wuse(self, so, tag):
        """点 E 出无舍念: E 位是无舍念就每 SO_KX_E_EVERY 点一下, 读到出了(连 2 帧不是无舍念)就停, 上限 SO_KX_WUSE_CAP。
        返回 (E 键, 停下的时刻)。"""
        key = so.get_resonance_key()
        start = time.time()
        e_taps = gone = 0
        next_e = 0.0
        out = False
        while time.time() - start < self.SO_KX_WUSE_CAP:
            self.task.next_frame()
            if self.so_on_field(so):
                if self.so_forte_full(so):
                    gone = 0
                    if time.time() >= next_e:
                        self.so_tap_e(key)
                        e_taps += 1
                        next_e = time.time() + max(0.0, self.SO_KX_E_EVERY - self.SO_KX_E_TAP)
                else:
                    gone += 1
                    if gone >= 2 and e_taps:
                        out = True
                        break
            self.macro_wait(0.01)
        so.record_resonance_use()
        t_w = time.time()
        self.macro_mark(f'{tag}无舍念({"出" if out else "cap"},E x{e_taps},{t_w - start:.2f}s)')
        return key, t_w

    def so_kx_hold(self, so, key, t_w, tag):
        """(LQ) 无舍念出了之后按住普攻出刻心念, 一直按着转圈(时停, 倒计时不走), 转完收招 +40 协奏、光圈出来就松手。
        按下时协奏已经满着(光圈一直在)就按满 LQ_SO_KX_HOLD_BLIND。
        按住期间 E 位又是无舍念 = 没放出来(被打断 / 前面读错): 松开, 连点 E 到放出来, 再按住(最多 LQ_SO_KX_E_MAX 轮)。"""
        self.wait_until_time(t_w + self.SO_KX_A_FROM)
        strict = self.glow_trusted()
        si = self.xn_attack_down()
        first_held = held_at = time.time()
        why = 'cap'
        pre_full = None
        e_more = e_hits = con_hits = 0
        try:
            while time.time() - held_at < self.LQ_SO_KX_HOLD_CAP:
                self.task.next_frame()
                el = time.time() - held_at
                if not self.hud_ok():
                    self.macro_wait(0.02)
                    continue
                on = self.so_on_field(so)
                e_hits = e_hits + 1 if on and self.so_forte_full(so) else 0
                if (e_hits >= 2 and e_more < self.LQ_SO_KX_E_MAX
                        and time.time() - first_held < self.LQ_SO_KX_E_RESCUE_CAP):
                    self.release_attack()
                    e_more += 1
                    key, t_w = self.so_wuse(so, f'{tag}补#{e_more}')
                    self.wait_until_time(t_w + self.SO_KX_A_FROM)
                    si = self.xn_attack_down()
                    held_at = time.time()
                    pre_full = None
                    e_hits = con_hits = 0
                    continue
                try:
                    con = float(self.task.get_current_con())
                except Exception:
                    con = 0.0
                if pre_full is None:
                    # 单帧: con_glow 要连 GLOW_CONFIRM 帧, 第一帧永远是 False
                    pre_full = self.glow_now(so) or (not strict and con >= self.SK_CON_ENOUGH)
                if pre_full:
                    if el >= self.LQ_SO_KX_HOLD_BLIND:
                        why = '原来就满'
                        break
                elif self.con_glow(so):
                    if el >= self.LQ_SO_KX_HOLD_MIN:
                        why = '光圈'
                        break
                elif not strict:
                    con_hits = con_hits + 1 if con >= self.SK_CON_ENOUGH else 0
                    if con_hits >= self.SK_CON_FULL_CONFIRM and el >= self.LQ_SO_KX_HOLD_MIN:
                        why = '读数'
                        break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}刻心念按住({why},{"SI" if si else "PM"},{time.time() - held_at:.2f}s'
                        f'{f",补E x{e_more}" if e_more else ""})')
        return why

    def so_kx_wait_con(self, so, tag, cap, grace=None, tap_from=None, tap_every=None):
        """刻心念之后等协奏真满(光圈; 光圈认得出过就不信读数, 读数满了再给 grace, 默认 SO_KX_GLOW_GRACE)。返回满没满。
        tap_every: 松手 tap_from 秒后起每 tap_every 点一下 A(念起普攻补协奏), 一直点到光圈 / cap; 不给就不按键干等。"""
        grace = self.SO_KX_GLOW_GRACE if grace is None else grace
        start3 = time.time()
        hits = 0
        samples = []
        full = False
        strict = self.glow_trusted()
        con_full_at = None
        taps = 0
        next_tap = start3 + (tap_from or 0.0)
        while time.time() - start3 < cap:
            if tap_every and time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + max(0.0, tap_every - self.A_TAP)
            self.task.next_frame()
            if not self.hud_ok():
                self.macro_wait(0.02)
                continue
            if self.con_glow(so):
                full = True
                samples.append('光圈')
                break
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            if len(samples) < self.CON_SAMPLE_MAX and (not samples or time.time() - start3 >= len(samples) * 0.3):
                samples.append(f'{time.time() - start3:.2f}:{con:.2f}')
            hits = hits + 1 if con >= self.SK_CON_ENOUGH else 0
            if hits >= self.SK_CON_FULL_CONFIRM:
                if not strict:
                    full = True
                    break
                # 光圈认得出却只有读数满: 再给光圈 SO_KX_GLOW_GRACE, 还没有就当没满, 去普攻补
                con_full_at = con_full_at or time.time()
                if time.time() - con_full_at >= grace:
                    samples.append('读数满没光圈')
                    break
            self.macro_wait(self.SK_CON_POLL)
        glow = ',光圈' if samples and samples[-1] == '光圈' else (',读数满没光圈' if samples and samples[-1] == '读数满没光圈' else '')
        self.macro_mark(f'{tag}等协奏({"满" if full else "没满"}{glow}{f",A x{taps}" if tap_every else ""},'
                        f'{time.time() - start3:.2f}s)')
        if not full:
            self.logger.info(f'{tag}: 刻心念之后 {cap:.1f}s 协奏没满 — {" ".join(samples)}')
        return full

    def seg_lq_so_a(self, so, xn, tag):
        """(LQ) 锁暝·第一次上场(守变奏进来 = 变奏·闪裂, 念起 -> 念深): R [没好就一直 A 到好] -> 演完 -> 点一下 A
        -> Q(暗鸟) -> 同奏切心。返回心带不带变奏(R 出没出)。

        R 给锁暝 5s 同奏(游戏时间, 演出里不走), A + 暗鸟 ~0.45s, 带着同奏切走心是变奏·应世相入场。
        """
        self.wait_until_time(self.switch_press_at + self.SO_R_LEAD)
        fired = self.so_r_asap(so, tag, self.LQ_SO_R_CAP, allow_e=not so.has_intro)
        if fired:
            self.so_tongzou_at = time.time()
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.SO_R_ANIM_MIN, time_out=self.SO_R_ANIM_CAP)
            for i in range(self.LQ_SO_A_TAPS):
                if i:
                    self.macro_wait(max(0.0, self.SO_A_EVERY - self.A_TAP))
                self.tap()
            self.macro_mark(f'{tag}R后A(x{self.LQ_SO_A_TAPS})')
        else:
            self.logger.warning(f'{tag}: 锁暝 R 没放出去(能量不够 / 没进念深) — 心拿不到同奏')
        self.so_echo(so, f'{tag}Q暗鸟')
        full = fired
        if not fired:
            full = self.read_con(1.0, settle=0.15) >= 1.0
        self.need(self.switch_retry(xn, f'{tag}切心', assume_con=full), 'suoming -> hsin')
        return full

    def so_ensure_intro(self, so, tag, a_every=None):
        """(LQ) 锁暝协奏不满不切(心没变奏 = 进不了统御众机, 照世相 E 心1 用过还在冷却, 这一圈万阙垂天就没了):
        一直普攻, 妄执满了(E 位是无舍念)就再出一次无舍念 + 按住刻心念(+40 协奏), 光圈出来才走。
        普攻本身给的协奏很少(实机 3s 只涨 0.06), 主要靠刻心念; E 只在无舍念时点(妄执没满点 E 是破隙, 冷却 8s)。
        光圈认得出过就只认光圈, 读数满了 LQ_SO_B_READ_GRACE 还没光圈也走(防这台机器这一刻认不出)。返回满没满。
        a_every: 点 A 间隔, 不给就是 SO_A_EVERY。"""
        a_every = self.SO_A_EVERY if a_every is None else a_every
        start = time.time()
        strict = self.glow_trusted()
        taps = kx = forte_hits = con_hits = 0
        next_tap = 0.0
        read_full_at = None
        why = 'cap'
        while time.time() - start < self.LQ_SO_B_ENSURE_CAP:
            self.task.next_frame()
            if not self.hud_ok():
                self.macro_wait(0.02)
                continue
            if self.con_glow(so):
                why = '光圈'
                break
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            con_hits = con_hits + 1 if con >= self.SK_CON_ENOUGH else 0
            if con_hits >= self.SK_CON_FULL_CONFIRM:
                read_full_at = read_full_at or time.time()
                if not strict or time.time() - read_full_at >= self.LQ_SO_B_READ_GRACE:
                    why = f'读数{con:.2f}'
                    break
            else:
                read_full_at = None
            on = self.so_on_field(so)
            forte_hits = forte_hits + 1 if on and self.so_forte_full(so) else 0
            if forte_hits >= 2 and kx < self.LQ_SO_B_KX_MAX:
                kx += 1
                forte_hits = con_hits = 0
                read_full_at = None
                key, t_w = self.so_wuse(so, f'{tag}保变奏#{kx}')
                self.so_kx_hold(so, key, t_w, f'{tag}保变奏#{kx}')
                continue
            if time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + max(0.0, a_every - self.A_TAP)
            self.macro_wait(0.01)
        full = why != 'cap'
        self.macro_mark(f'{tag}保变奏({why},刻心念x{kx},A x{taps},{time.time() - start:.2f}s)')
        if not full:
            self.logger.warning(f'{tag}: 锁暝补了 {self.LQ_SO_B_ENSURE_CAP:.0f}s 协奏还是没满(打不到怪?), '
                                f'心只能普通入场')
        return full

    def so_dodge_side(self, tag):
        """锁暝侧闪: 按住 SO_B_DODGE_KEY -> 右键 -> 松开(不带方向是往后闪, 离开怪, 闪完 A1 打空)。"""
        task = self.task
        key = self.SO_B_DODGE_KEY
        task.send_key_down(key)
        try:
            self.macro_wait(self.WD_W_LEAD)
            task.mouse_down(key='right')
            self.macro_wait(self.WD_DODGE_HOLD)
            task.mouse_up(key='right')
            self.macro_wait(self.WD_W_TAIL)
        finally:
            task.send_key_up(key)
        self.macro_mark(f'{tag}侧闪')

    def seg_so_b(self, so, xn, tag):
        """锁暝·第二次上场(心转相的同奏 -> 旋雷破·同奏, 念深 +200 妄执): 点 A -> 侧闪(连段拉回 A1; Boss 能处决就换成处决, 处决完也从 A1 起) -> 点 A 到妄执满
        -> 点 E(无舍念) -> 按住普攻(刻心念) -> 协奏满变奏心。妄执在侧闪前就满了就不闪。返回协奏满没满。

        妄执满 = E 位换成无舍念(suoming_forte_e), 读到连 2 帧才停手; 没满时按 E 是破隙(冷却 8s, 这一圈就没无舍念了)。
        """
        sw = self.switch_press_at
        t_in = max(getattr(so, 'last_switch_in_time', 0.0) or sw, sw)
        self.wait_until_time(sw + self.LQ_SO_B_A_LEAD)
        start = time.time()
        dodge_at = t_in + self.SO_B_DODGE_AT
        taps = hits = 0
        next_tap = 0.0
        ok = dodged = broke = False
        while time.time() - start < self.LQ_SO_B_FILL_CAP:
            self.task.next_frame()
            if self.so_on_field(so):
                hits = hits + 1 if self.so_forte_full(so) else 0
                if hits >= 2:
                    ok = True
                    break
            if self.SO_B_DODGE and not dodged and time.time() >= dodge_at:
                dodged = True
                t_b = time.time()
                if self.SO_B_BREAK and self.try_break(tag):
                    broke = True
                    start += time.time() - t_b  # 处决演出不算进妄执的上限
                    next_tap = 0.0
                else:
                    self.so_dodge_side(tag)
                    next_tap = time.time() + self.SO_B_DODGE_TO_A
                continue
            if time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + max(0.0, self.SO_A_EVERY - self.A_TAP)
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}A到妄执满({"满" if ok else "cap"},{"处决过" if broke else "闪过" if dodged else "没闪"},'
                        f'A x{taps},{time.time() - start:.2f}s)')
        self.broke_this_lap = broke
        if ok:
            key, t_w = self.so_wuse(so, tag)
            self.so_kx_hold(so, key, t_w, tag)
            full = self.so_kx_wait_con(so, tag, self.LQ_SO_KX_FINISH_CAP)
        else:
            self.logger.warning(f'{tag}: 锁暝 {self.LQ_SO_B_FILL_CAP:.1f}s 妄执没满, 接着普攻到妄执满再出刻心念')
            full = False
        if not full:
            full = self.so_ensure_intro(so, tag)
        self.need(self.switch_retry(xn, f'{tag}切心', assume_con=full, time_out=self.SO_TO_XN_TIME_OUT),
                  'suoming -> hsin')
        return full

    # ==================== 莉莉 ====================

    def echo_if_ready(self, char, tag, cap, confirm=2):
        """声骸好着才按(so_echo: 按到 CD 涨上来); 读到还在冷却就不按 —— so_echo 对冷却中的声骸会白按满 cap。"""
        self.task.next_frame()
        if self.so_on_field(char) and self.read_cd('echo', char) > self.SO_ECHO_RISE:
            self.macro_mark(f'{tag}声骸冷却中')
            return False
        return self.so_echo(char, tag, cap=cap, confirm=confirm)

    def ly_in_slumber(self, ly):
        try:
            return bool(ly.in_slumberland())
        except Exception:
            return False

    def ly_end_slumber(self, ly, tag):
        """莉莉还在梦乡里(上一次没变奏入场, 永眠花开没把梦乡结束掉 / 开局前 50s 内放过 R): 普攻、E、声骸都没了,
        这一段全白打。梦乡里 R 键是"结束梦乡": 按一下, 隔 LY_SLUMBER_END_EVERY 还在才再按(出了梦乡 R 亮着会真放出去)。
        返回原来在不在梦乡。"""
        hits = 0
        for _ in range(3):
            self.task.next_frame()
            if self.hud_ok() and self.so_on_field(ly):
                hits = hits + 1 if self.ly_in_slumber(ly) else 0
            self.macro_wait(0.02)
        if hits < 2:
            return False
        start = time.time()
        n = out = 0
        next_r = 0.0
        while time.time() - start < self.LY_SLUMBER_END_CAP:
            if time.time() >= next_r:
                ly.send_liberation_key()
                n += 1
                next_r = time.time() + self.LY_SLUMBER_END_EVERY
            self.task.next_frame()
            out = out + 1 if (self.hud_ok() and self.so_on_field(ly) and not self.ly_in_slumber(ly)) else 0
            if out >= 2:
                break
            self.macro_wait(0.03)
        self.macro_mark(f'{tag}还在梦乡,按R结束({"ok" if out >= 2 else "cap"},R x{n},{time.time() - start:.2f}s)')
        self.logger.warning(f'{tag}: 莉莉进场时还在梦乡里(没变奏入场?), 按 R 结束梦乡')
        return True

    def ly_perfume_full(self, ly):
        """梦香满(重击位满的提示 / 回路满)。读当前帧; 场上不是莉莉(刚切人那几帧技能栏还是上一个人的)返回 False。"""
        if not self.so_on_field(ly):
            return False
        try:
            return bool(ly.is_mouse_forte_full() or ly.is_forte_full())
        except Exception:
            return False

    def ly_heavy_on(self, ly):
        """织梦重击引导中(框架 Lily.heavy_in_progress: 梦香提示没了、花还在)。读当前帧。"""
        try:
            return bool(ly.heavy_in_progress())
        except Exception:
            return False

    def ly_flower_icon(self, ly):
        """重击位的魇影花图标(框架 lily_heavy 模板, 不管提示在不在: 梦香满待按和引导中都是这朵花)。读当前帧。"""
        if not self.so_on_field(ly):
            return False
        try:
            return bool(self.task.find_one('lily_heavy', threshold=0.8))
        except Exception:
            return False

    def ly_fill_perfume(self, ly, tag):
        """到场读梦香: 满了(连 LY_FULL_CONFIRM 帧)马上返回; 没满点普攻补, 上限 LY_FILL_CAP。返回满没满。"""
        start = time.time()
        taps = hits = 0
        next_tap = start + self.LY_FULL_GRACE
        full = False
        while time.time() - start < self.LY_FILL_CAP:
            self.task.next_frame()
            lit = self.hud_ok() and self.ly_perfume_full(ly)
            hits = hits + 1 if lit else 0
            if hits >= self.LY_FULL_CONFIRM:
                full = True
                break
            if not lit and time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + max(0.0, self.LY_A_EVERY - self.A_TAP)
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}梦香({"满" if full else "cap"},A x{taps},{time.time() - start:.2f}s)')
        return full

    def ly_prompt(self, ly):
        """重击位的梦香满提示(框架 mouse_forte 模板)。读当前帧。不看回路条颜色: 引导里梦铃的祷声涨在回路条上,
        is_forte_full 会读成满。"""
        if not self.so_on_field(ly):
            return False
        try:
            return bool(ly.is_mouse_forte_full())
        except Exception:
            return False

    def ly_heavy(self, ly, tag, tale_hold, entry=False, blind=None, held_at=None):
        """按住普攻: 织梦重击(引导) -> 终章。返回 'done'(魇影, 花没了) / 'tale'(童话, 按满 tale_hold) / 'blind' / 'cap'。
        引导开始: 花亮了, 或者按下后梦香满提示连 LY_PROMPT_GONE 帧没了。魇影: 花连 LY_HEAVY_END_CONFIRM 帧没了 =
        终章出手(不能打断), 松手; 童话(引导开始后一直没见花): 从引导开始按满 tale_hold 松手。
        一直没认出引导: 梦香满提示还亮着才松开重按一次, 否则按满 blind(默认 LY_HEAVY_BLIND) 松。
        entry=True: 到场梦香没确认满就按下了(按住的普攻连段先把梦香打满再转重击) —— 满提示要连 LY_FULL_CONFIRM 帧才认,
        重按的 LY_HEAVY_START_CAP 从提示亮起算, 不从按下算。
        held_at: 左键已经按着了(SendInput, 这一刻按下的), 不再按; blind 也从这一刻算。"""
        blind = self.LY_HEAVY_BLIND if blind is None else blind
        lit_need = self.LY_FULL_CONFIRM if entry else 1
        if held_at is None:
            si = self.xn_attack_down()
            press_at = time.time()
            self.macro_mark(f'{tag}{"到场" if entry else ""}按住({"SI" if si else "PM"})')
        else:
            si = self.si_holding
            press_at = held_at
            self.macro_mark(f'{tag}一直按着({"SI" if si else "PM"},按下{time.time() - held_at:.2f}s前)')
        started_at = gone_at = lit_at = None
        flower_seen = prompt_seen = False
        off = gone = retried = lit_hits = ready = false_starts = 0
        how = ''
        why = 'blind'
        try:
            while True:
                self.task.next_frame()
                now = time.time()
                hud = self.hud_ok()
                # 花 + 提示 = 梦香满、还没转(待按); 花、没提示 = 引导中; 花没了 = 终章出手
                prompt_now = bool(hud and self.ly_prompt(ly))
                icon = bool(hud and self.ly_flower_icon(ly))
                flower = icon and not prompt_now
                flower_seen = flower_seen or flower
                if started_at is None:
                    if flower:
                        started_at, how = now, '花'
                        ready = 0
                    elif prompt_now:
                        lit_hits += 1
                        if lit_hits >= lit_need:
                            prompt_seen = True
                            if lit_at is None:
                                lit_at = now
                        gone = 0
                        since = now - (max(press_at, lit_at) if entry and lit_at is not None else press_at)
                        if prompt_seen and since >= self.LY_HEAVY_START_CAP and retried < self.LY_HEAVY_RETRIES:
                            # 梦香提示还亮着 = 梦香没被吃掉, 按下沿被吞了(收招/硬直)
                            retried += 1
                            self.release_attack()
                            self.macro_wait(0.05)
                            self.xn_attack_down()
                            press_at = time.time()
                            lit_at = None
                            lit_hits = 0
                            self.macro_mark(f'{tag}没进引导,重按#{retried}')
                            continue
                    elif hud and not prompt_seen:
                        lit_hits = 0
                    elif hud and prompt_seen:
                        gone += 1
                        if gone == 1:
                            gone_at = now
                        if gone >= self.LY_PROMPT_GONE:
                            started_at, how = gone_at, '提示没了'
                    if started_at is not None:
                        self.macro_mark(f'{tag}引导开始({how},{started_at - press_at:.2f}s)')
                    elif now - press_at >= blind:
                        why = 'blind'
                        break
                else:
                    if flower_seen:
                        if flower:
                            off = ready = 0
                        elif icon and prompt_now:
                            # 【提示比花晚认到】梦香刚满那 1~2 帧只认到花、提示模板(0.6~0.73 勉强过线)还没认到, 会被当成
                            # 引导开始; 接着认到提示 = 其实是待按。11-19 21:50 两局按住带过 E/Q 时都把这当成"花没了",
                            # 0.17s 就松手, 重击没出来。连 2 帧花+提示 = 退回没开始, 等提示消失(真开始引导)
                            off = 0
                            ready += 1
                            if ready >= 2:
                                false_starts += 1
                                self.macro_mark(f'{tag}花+提示=待按,不算引导({now - started_at:.2f}s)')
                                started_at = None
                                flower_seen = False
                                prompt_seen = True
                                lit_hits = lit_need
                                if lit_at is None:
                                    lit_at = now
                                gone = ready = 0
                        elif hud and not icon:
                            ready = 0
                            off += 1
                            if off >= self.LY_HEAVY_END_CONFIRM:
                                why = 'done'
                                break
                    elif now - started_at >= tale_hold:
                        why = 'tale'
                        break
                    if started_at is not None and now - started_at >= self.LY_HEAVY_CAP:
                        why = 'cap'
                        break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        lead = f'引导{time.time() - started_at:.2f}s' if started_at is not None else '没认出引导'
        fs = f',待按误认x{false_starts}' if false_starts else ''
        self.macro_mark(f'{tag}松开({why},{lead},{time.time() - press_at:.2f}s{fs})')
        return why

    def ly_pre_r_con(self, ly, tag, need=None):
        """放 R 之前协奏低于 need(默认 LY_PRE_R_CON)就点普攻补(R 自己给 20; 演完进梦乡打不了普攻)。"""
        need = self.LY_PRE_R_CON if need is None else need
        con = self.read_con(1.0, settle=0.12)
        if con >= need:
            self.macro_mark(f'{tag}R前协奏{con:.2f}')
            return
        start = time.time()
        taps = 0
        while time.time() - start < self.LY_PRE_R_CAP:
            self.tap()
            taps += 1
            self.macro_wait(max(0.0, self.LY_A_EVERY - self.A_TAP))
            con = self.read_con(1.0, settle=0.03)
            if con >= need:
                break
        self.macro_mark(f'{tag}R前补协奏({con:.2f},A x{taps},{time.time() - start:.2f}s)')

    def seg_ly_open(self, ly, xn, tag):
        """莉莉·启动段(普通入场): 2A -> A2 往后滑的时候 E -> 切心(普通入场)。"""
        t_in = getattr(ly, 'last_switch_in_time', 0.0) or time.time()
        self.wait_until_time(t_in + self.LY_OPEN_A_LEAD)
        self.ly_end_slumber(ly, tag)
        start = time.time()
        taps = 0
        while time.time() - start < self.LY_OPEN_E_AT:
            self.tap()
            taps += 1
            left = self.LY_OPEN_E_AT - (time.time() - start)
            self.macro_wait(max(0.0, min(self.LY_A_EVERY - self.A_TAP, left)))
        self.macro_mark(f'{tag}2A(A x{taps})')
        if self.press_res_until_cd(ly, self.LY_E_CAP, f'{tag}E', ready_grace=0.15):
            ly.record_resonance_use()
        self.need(self.switch_retry(xn, f'{tag}切心', read_con=True), 'lily -> hsin')

    def seg_ly_loop(self, ly, xn, tag):
        """莉莉·循环段开头(心万阙垂天后变奏入场 = 永眠花开, +130 梦香): E -> 切心(普通入场)。"""
        t_in = getattr(ly, 'last_switch_in_time', 0.0) or time.time()
        self.wait_until_time(t_in + self.LY_LOOP_E_LEAD)
        self.ly_end_slumber(ly, tag)
        if self.press_res_until_cd(ly, self.LY_LOOP_E_CAP, f'{tag}E'):
            ly.record_resonance_use()
        self.need(self.switch_retry(xn, f'{tag}切心', read_con=True), 'lily -> hsin')

    def seg_ly_heavy(self, ly, so, tag, opener=False):
        """莉莉·主段(心切回来, 普通入场): [梦香没满先点 A] -> 按住出织梦重击 -> 终章 -> Q -> [协奏不够点 A 补]
        -> R(没好边 A 边等) -> 演完 -> 协奏满变奏锁暝。返回锁暝带不带变奏。"""
        self.ly_fill_perfume(ly, tag)
        self.ly_heavy(ly, tag, self.LY_TALE_HOLD_OPEN if opener else self.LY_TALE_HOLD)
        self.echo_if_ready(ly, f'{tag}Q', self.LY_ECHO_CAP)
        self.ly_pre_r_con(ly, tag)
        self.wait_r_lit(ly, tag, self.LY_R_WAIT_CAP, self.LY_A_EVERY)
        return self.ly_r_and_switch(ly, so, tag)

    def ly5_r_direct(self, ly, tag, window):
        """5R 莉莉 R 不先读亮, 直接连按 window 秒(R 冷却还剩一点就加上剩的)。在梦乡(R = 结束梦乡)、R 冷却还长就不按。
        返回放出去没有; 没放出去调用方走 ensure_r。"""
        slumber = 0
        for _ in range(2):
            self.task.next_frame()
            if self.hud_ok() and self.so_on_field(ly) and self.ly_in_slumber(ly):
                slumber += 1
        if slumber:
            return False
        cd = self.read_cd('liberation', ly)
        if cd > window:
            return False
        return self.fire_r(ly, f'{tag}R', window + cd)

    def ly_r_and_switch(self, ly, so, tag, direct=0.0):
        """莉莉 R(没亮攒能量) -> 演完 -> 协奏满(光圈)变奏锁暝; R 没放出去普攻补协奏再切。返回锁暝带不带变奏。
        direct > 0: 先 ly5_r_direct 直接连按这么久, 没放出去再走 ensure_r。"""
        fired = direct > 0 and self.ly5_r_direct(ly, tag, direct)
        if not fired:
            fired = self.ensure_r(ly, tag, self.LY_R_WINDOW, self.LY_R_ENSURE_CAP,
                                  lambda r: self.macro_spam(self.R_BUILD_SLICE, self.A_INTERVAL, tag=f'{tag}攒R:A#{r}'))
        if fired:
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.LY_R_ANIM_MIN, time_out=self.LY_R_ANIM_CAP)
            full = self.con_ready(ly, self.LY_CON_READ, tag=f'{tag}协奏', strict=True)
            if not full:
                self.logger.warning(f'{tag}: 莉莉 R 演完协奏没满(con{self.con_last:.2f}), 梦乡里打不了普攻 — '
                                    f'锁暝多半普通入场(念起, R 要靠妄执满点 E 进念深)')
        else:
            self.logger.warning(f'{tag}: 莉莉 R 没放出去, 普攻补协奏再切锁暝')
            full = self.wait_con_full('莉莉', self.LY_CON_CAP, tag=f'{tag}补协奏', cur=ly, strict=True)
        self.need(self.switch_retry(so, f'{tag}切锁暝', assume_con=full), 'lily -> suoming')
        return full

    def switch_to_ly(self, ly, tag, full=None):
        """切给莉莉并等她的变奏入场演完。返回时 HUD 在场、场上是她。full: 交棒前读到的协奏满没满(None = 现读)。"""
        for attempt in range(1 + self.SWITCH_RETRIES):
            got = self.switch_to_ly_once(ly, tag if attempt == 0 else f'{tag}重切#{attempt}', full)
            if got is not None:
                return got
            self.wait_hud_up(cap=2.0, tag=f'{tag}重切前HUD')
        return False

    def switch_to_ly_once(self, ly, tag, full=None):
        """切一次莉莉。返回 True/False(到场了, HUD 回没回来), None = 莉莉没上场。

        入场演出藏不藏 HUD 没量过: 不管切人判没判到, 都等 HUD 回来再认一次场上是谁。演出里一下都不点。
        """
        src = self.cur
        sw_at = time.time()
        if full is None:
            try:
                full = float(self.task.get_current_con()) >= 1.0
            except Exception:
                full = False
        arrived = self.quick_switch(ly, assume_con=full, time_out=self.LY_TO_TIME_OUT)
        anim_in_switch = arrived and time.time() - sw_at >= self.LY_SWITCH_ANIM_HINT
        start = time.time()
        gone_at = None
        hits = 0
        back = False
        while time.time() - start < self.LY_INTRO_ANIM_CAP:
            self.task.next_frame()
            if not self.hud_ok():
                if gone_at is None:
                    gone_at = time.time()
                hits = 0
            elif gone_at is not None or anim_in_switch:
                hits += 1
                if hits >= self.HUD_BACK_CONFIRM:
                    back = True
                    break
            elif time.time() - start >= self.LY_INTRO_NOANIM:
                back = True
                break
            self.macro_wait(0.01)
        if gone_at is not None:
            self.add_freeze_duration(gone_at, time.time() - gone_at)
        self.macro_mark(f'{tag}莉入场({"anim" if gone_at is not None or anim_in_switch else "noanim"},'
                        f'{"ok" if back else "timeout"},{time.time() - start:.2f}s)')
        if not arrived:
            who = self.on_field_char(time_out=1.0)
            if who is not ly:
                self.macro_mark(f'{tag}莉没上场({who.name if who else "?"})')
                return None
            self.handoff(src if src is not None else self, ly, full)
            self.macro_mark(f'{tag}>Lil(演出里到场)')
        self.ly_intro_done_at = time.time()
        return back

    # ==================== 5R 进阶轴 ====================

    def dodge_dir(self, keys, tag):
        """带方向的闪避: 按住 keys -> 右键 -> 松开。不带方向是往后闪, 会离开怪。"""
        task = self.task
        held = []
        try:
            for k in keys:
                task.send_key_down(k)
                held.append(k)
            self.macro_wait(self.WD_W_LEAD)
            task.mouse_down(key='right')
            self.macro_wait(self.WD_DODGE_HOLD)
            task.mouse_up(key='right')
            self.macro_wait(self.WD_W_TAIL)
        finally:
            for k in reversed(held):
                task.send_key_up(k)
        self.macro_mark(f'{tag}闪避({"+".join(keys).upper()})')

    def seg5_open_head(self, so, ly, tag):
        """5R 启动轴开头(场上是心): E -> 切锁暝(普通) Q -> 切莉莉(普通)。"""
        if self.press_res_until_cd(self, self.R5_OPEN_E_CAP, f'{tag}心E', ready_grace=0.15):
            self.record_resonance_use()
        self.need(self.switch_retry(so, f'{tag}切锁暝'), 'opener: hsin -> suoming')
        self.so_echo(so, f'{tag}锁Q', cap=self.R5_OPEN_SO_Q_CAP, confirm=self.R5_OPEN_SO_Q_CONFIRM)
        self.need(self.switch_retry(ly, f'{tag}切莉'), 'opener: suoming -> lily')

    def ly5_slumber_out(self, ly, tag, gone_at):
        """莉莉 R 演出尾巴起连按闪避, 把她从梦乡里拉出来(gone_at = 演出开始)。HUD 回来后至少再闪 R5_LY_OUT_AFTER_BACK 下,
        而且连 2 帧读到不在梦乡才停; HUD 回来 R5_LY_OUT_CAP 还读到在梦乡就按 R 结束梦乡(ly_end_slumber)。"""
        n = after = back = out = 0
        next_d = gone_at + self.R5_LY_OUT_FROM
        back_at = None
        why = 'cap'
        while True:
            now = time.time()
            if back_at is not None and now - back_at >= self.R5_LY_OUT_CAP:
                break
            if back_at is None and now - gone_at >= self.LY_R_ANIM_CAP:
                why = 'HUD没回来'
                break
            if now >= next_d:
                self.task.mouse_down(key='right')
                try:
                    self.macro_wait(self.XN2_DODGE_HOLD)
                finally:
                    self.task.mouse_up(key='right')
                n += 1
                if back_at is not None:
                    after += 1
                next_d = time.time() + max(0.0, self.R5_LY_OUT_EVERY - self.XN2_DODGE_HOLD)
            self.task.next_frame()
            if self.hud_ok() and time.time() - gone_at >= self.LY_R_ANIM_MIN:
                if back_at is None:
                    back += 1
                    if back >= self.HUD_BACK_CONFIRM:
                        back_at = time.time()
                elif self.so_on_field(ly):
                    out = out + 1 if not self.ly_in_slumber(ly) else 0
                    if out >= 2 and after >= self.R5_LY_OUT_AFTER_BACK:
                        why = '出梦乡'
                        break
            elif back_at is None:
                back = 0
            self.macro_wait(0.01)
        if back_at is not None:
            self.add_freeze_duration(gone_at, back_at - gone_at)
        self.macro_mark(f'{tag}闪避出梦乡({why},x{n},HUD回来后x{after},{time.time() - gone_at:.2f}s)')
        if why == 'cap':
            self.ly_end_slumber(ly, tag)

    def ly5_hold_eq(self, ly, tag):
        """启动轴: SendInput 按住左键, 按着的时候用 SendInput 键盘按 E(到进 CD)、Q(声骸冷却中就不按)。
        返回按下时刻(左键还按着, 交给 ly_heavy(held_at=..)); 键位认不出 / 左键没走成 SendInput / E 没读到进 CD
        返回 None(已经松手, 调用方走老路: PostMessage 按 E、Q, 点 A 等梦香满再按)。"""
        vk_e = self.si_vk(ly.get_resonance_key())
        vk_q = self.si_vk(ly.get_echo_key())
        if vk_e is None or vk_q is None:
            self.macro_mark(f'{tag}按住带E/Q:键位认不出')
            return None
        si = self.xn_attack_down()
        held_at = time.time()
        keep = False
        try:
            if not si:
                self.macro_mark(f'{tag}按住带E/Q:左键没走SendInput')
                return None
            if not self.press_key_until_cd(lambda: self.si_tap_key(vk_e), ly.resonance_available,
                                           self.LY_E_CAP, f'{tag}按住E', ready_grace=0.15):
                return None
            ly.record_resonance_use()
            self.task.next_frame()
            if self.so_on_field(ly) and self.read_cd('echo', ly) > self.SO_ECHO_RISE:
                self.macro_mark(f'{tag}按住Q声骸冷却中')
            else:
                self.so_echo(ly, f'{tag}按住Q', cap=self.R5_LY_OPEN_Q_CAP, confirm=self.R5_LY_ECHO_CONFIRM,
                             send=lambda: self.si_tap_key(vk_q))
            keep = True
            return held_at
        finally:
            if not keep:
                self.release_attack()

    def ly5_finish_con(self, ly, tag):
        """终章出手后等协奏真满(光圈 + 环), R5_LY_FIN_CON 内没有就普攻补(上限 R5_LY_CON_CAP)。返回满没满。"""
        if self.con_ready(ly, self.R5_LY_FIN_CON, tag=f'{tag}协奏', strict=True):
            return True
        return self.wait_con_full('莉莉', self.R5_LY_CON_CAP, tag=f'{tag}补协奏', cur=ly, strict=True)

    def seg5_ly_open(self, ly, xn, tag):
        """5R 莉莉·启动段(普通入场): R -> 演出尾巴连按闪避出梦乡 -> 2A -> 按住普攻(带过 E、Q, R5_LY_OPEN_HOLD_EQ;
        梦香没满连段自己补): 织梦重击 -> 终章 -> 协奏满变奏心。返回心带不带变奏。"""
        t_in = getattr(ly, 'last_switch_in_time', 0.0) or time.time()
        self.wait_until_time(t_in + self.R5_LY_OPEN_R_LEAD)
        fired = self.ly5_r_direct(ly, tag, self.R5_LY_R_DIRECT) or             self.ensure_r(ly, tag, self.LY_R_WINDOW, self.R5_LY_OPEN_R_CAP,
                          lambda r: self.macro_spam(self.R_BUILD_SLICE, self.A_INTERVAL, tag=f'{tag}攒R:A#{r}'))
        if fired:
            self.ly5_slumber_out(ly, tag, time.time())
        else:
            self.logger.warning(f'{tag}: 莉莉开局 R 没放出去, 照常往下打(少了摇篮曲的攻击 buff)')
        start = time.time()
        taps = 0
        while time.time() - start < self.LY_OPEN_E_AT:
            self.tap()
            taps += 1
            left = self.LY_OPEN_E_AT - (time.time() - start)
            self.macro_wait(max(0.0, min(self.LY_A_EVERY - self.A_TAP, left)))
        self.macro_mark(f'{tag}2A(A x{taps})')
        held_at = None
        if self.R5_LY_OPEN_HOLD_EQ and self.XN_HOLD_SENDINPUT and self.game_foreground():
            held_at = self.ly5_hold_eq(ly, tag)
        if held_at is not None:
            self.ly_heavy(ly, tag, self.LY_TALE_HOLD_OPEN, entry=True, blind=self.R5_LY_HEAVY_BLIND_OPEN,
                          held_at=held_at)
        else:
            if self.press_res_until_cd(ly, self.LY_E_CAP, f'{tag}E', ready_grace=0.15):
                ly.record_resonance_use()
            self.echo_if_ready(ly, f'{tag}Q', self.R5_LY_OPEN_Q_CAP)
            self.ly_fill_perfume(ly, tag)
            self.ly_heavy(ly, tag, self.LY_TALE_HOLD_OPEN)
        full = self.ly5_finish_con(ly, tag)
        if not full:
            self.logger.warning(f'{tag}: 莉莉终章后协奏没满(con{self.con_last:.2f}) — 心普通入场, 转相没有同奏')
        self.need(self.switch_retry(xn, f'{tag}切心', assume_con=full, time_out=self.R5_LY_TO_XN_TIME_OUT),
                  'lily -> hsin')
        return full

    def seg5_ly_loop(self, ly, xn, tag, e_now=True):
        """5R 莉莉·循环开头(变奏·永眠花开, +130 梦香): E -> 点一下 A -> 切心(普通)。
        e_now=False(四星协奏武器的第一个循环): 不按 E 只点 A, E 留给第二次上场。"""
        t_in = getattr(ly, 'last_switch_in_time', 0.0) or time.time()
        self.wait_until_time(t_in + self.LY_LOOP_E_LEAD)
        self.ly_end_slumber(ly, tag)
        self.ly5_entry_e_ok = False
        if e_now:
            if self.press_res_until_cd(ly, self.LY_LOOP_E_CAP, f'{tag}E'):
                ly.record_resonance_use()
                self.ly5_entry_e_ok = True
            for i in range(self.R5_LY_LOOP_A):
                if i:
                    self.macro_wait(max(0.0, self.LY_A_EVERY - self.A_TAP))
                self.tap()
        else:
            # 四星武器圈: E 留到第二次上场, 这次点 A 攒梦香, 点到到场 +R5_LY_LOOP_NO_E_UNTIL(至少 R5_LY_LOOP_A_NO_E 下)
            until = t_in + self.R5_LY_LOOP_NO_E_UNTIL
            taps = 0
            while taps < self.R5_LY_LOOP_A_NO_E or time.time() < until:
                if taps:
                    self.macro_wait(max(0.0, self.LY_A_EVERY - self.A_TAP))
                self.tap()
                taps += 1
            self.macro_mark(f'{tag}E留到第二次上场(四星武器,A x{taps},到场{time.time() - t_in:.2f}s)')
        self.need(self.switch_retry(xn, f'{tag}切心', read_con=True), 'lily -> hsin')

    def seg5_so0(self, so, ly, tag):
        """5R 锁暝·第一次上场(普通入场): 点 A -> 到场 +R5_SO0_DODGE_AT 斜前闪 -> 点 A R5_SO0_A2_FOR -> 停 R5_SO0_Q_GAP -> Q -> 切莉莉(普通)。
        妄执攒到这儿, 莉莉变奏进来的碎霆 +200 正好补满。R5_SO0_BREAK=True: 能处决就处决顶替那一闪(处决后的 A 从演完起算)。"""
        sw = self.switch_press_at
        t_in = max(getattr(so, 'last_switch_in_time', 0.0) or sw, sw)
        self.wait_until_time(sw + self.LQ_SO_B_A_LEAD)
        dodge_at = t_in + self.R5_SO0_DODGE_AT
        taps = 0
        while time.time() < dodge_at:
            self.tap()
            taps += 1
            self.macro_wait(min(self.R5_SO0_A_EVERY - self.A_TAP, dodge_at - time.time()))
        self.macro_mark(f'{tag}2A(A x{taps})')
        broke = bool(self.R5_SO0_BREAK and self.try_break(tag))
        if not broke:
            self.dodge_dir(self.R5_SO0_DODGE_KEYS, tag)
        self.broke_this_lap = broke
        start = time.time()
        taps = 0
        while time.time() - start < self.R5_SO0_A2_FOR:
            self.tap()
            taps += 1
            left = self.R5_SO0_A2_FOR - (time.time() - start)
            self.macro_wait(max(0.0, min(self.R5_SO0_A_EVERY - self.A_TAP, left)))
        self.macro_mark(f'{tag}{"处决后" if broke else "闪后"}2A(A x{taps})')
        self.macro_wait(self.R5_SO0_Q_GAP)
        self.echo_if_ready(so, f'{tag}Q', self.R5_SO0_Q_CAP)
        self.need(self.switch_retry(ly, f'{tag}切莉', read_con=True), 'suoming -> lily')

    def seg5_ly_heavy(self, ly, so, tag, e_first=False):
        """5R 莉莉·主段(锁暝切回来, 普通入场): [四星武器第一个循环先 E] -> 到场就按住(R5_LY_HOLD_AT_ENTRY; 梦香没满连段自己补)
        织梦重击 -> 终章 -> Q -> 马上 R(没好边 A 边等) -> 演完 -> 协奏满变奏锁暝。返回锁暝带不带变奏。"""
        if e_first and self.press_res_until_cd(ly, self.LY_E_CAP, f'{tag}E'):
            ly.record_resonance_use()
        # PostMessage 的按下沿落在入场动作里会被丢掉、后面按着也不算数, 只有 SendInput 能到场就按
        if self.R5_LY_HOLD_AT_ENTRY and self.XN_HOLD_SENDINPUT and self.game_foreground():
            # 变奏入场 E 出了 = 梦香到场就满, 很快进引导; 四星圈(这里才 E)/入场 E 没出要连段补梦香, 给长的
            full_at_entry = not e_first and getattr(self, 'ly5_entry_e_ok', False)
            blind = self.R5_LY_HEAVY_BLIND_FULL if full_at_entry else self.R5_LY_HEAVY_BLIND
            self.ly_heavy(ly, tag, self.LY_TALE_HOLD, entry=True, blind=blind)
        else:
            self.ly_fill_perfume(ly, tag)
            self.ly_heavy(ly, tag, self.LY_TALE_HOLD)
        self.echo_if_ready(ly, f'{tag}Q', self.LY_ECHO_CAP, confirm=self.R5_LY_ECHO_CONFIRM)
        # 视频 Q 后 0.1s 就 R; 11-19 18:53 实机 Q 出了到 R 按下 0.42s(读协奏 settle + wait_r_lit + ensure_r 各读几帧)。
        # 协奏只读一帧(实机这里都是 0.95~1.00), 不到 LY_PRE_R_CON 才走原来的补协奏; R 不先读亮, 直接连按(R5_LY_R_DIRECT)
        try:
            con = float(self.task.get_current_con())
        except Exception:
            con = 0.0
        if con >= self.R5_LY_PRE_R_CON:
            self.macro_mark(f'{tag}R前协奏{con:.2f}(一帧)')
        else:
            self.ly_pre_r_con(ly, tag, need=self.R5_LY_PRE_R_CON)
        return self.ly_r_and_switch(ly, so, tag, direct=self.R5_LY_R_DIRECT)

    def seg5_so_b(self, so, xn, tag):
        """5R 锁暝·第二次上场(莉莉变奏进来 = 碎霆 +200): 妄执满(连 2 帧) -> E 无舍念 -> 按住刻心念 -> 协奏满变奏心。
        进场妄执没满(锁0 被打断/打空): 点 A 补到满; R5_SO_B_FILL_CAP 还不满不按 E(不满是破隙), 交给 so_ensure_intro。
        返回协奏满没满。"""
        start = time.time()
        arrived_at = None
        hits = misses = taps = 0
        next_tap = 0.0
        why = 'cap'
        while True:
            now = time.time()
            if arrived_at is None and now - start >= self.R5_SO_B_ARRIVE_CAP:
                why = '没到场'
                break
            if arrived_at is not None and now - arrived_at >= self.R5_SO_B_FILL_CAP:
                break
            self.task.next_frame()
            if self.hud_ok() and self.so_on_field(so):
                if arrived_at is None:
                    arrived_at = time.time()
                if self.so_forte_full(so):
                    hits += 1
                    misses = 0
                    if hits >= 2:
                        why = '满'
                        break
                else:
                    hits = 0
                    misses += 1
                    # 刚到场那几帧技能栏可能还是莉莉的: 连 3 帧不满才点 A
                    if misses >= 3 and time.time() >= next_tap:
                        self.tap()
                        taps += 1
                        next_tap = time.time() + max(0.0, self.SO_A_EVERY - self.A_TAP)
            self.macro_wait(0.01)
        el = time.time() - (arrived_at or start)
        self.macro_mark(f'{tag}妄执({why},{"进场就满" if why == "满" and not taps else f"A x{taps}"},{el:.2f}s)')
        full = False
        if why == '满':
            key, t_w = self.so_wuse(so, tag)
            self.so_kx_hold(so, key, t_w, tag)
            full = self.so_kx_wait_con(so, tag, self.LQ_SO_KX_FINISH_CAP, grace=self.R5_SO_KX_GLOW_GRACE,
                                       tap_from=self.R5_SO_KX_TAP_FROM, tap_every=self.R5_SO_B_A_EVERY)
        else:
            self.logger.warning(f'{tag}: 锁暝变奏进场妄执没满({why}), 接着普攻到妄执满再出刻心念')
        if not full:
            full = self.so_ensure_intro(so, tag, a_every=self.R5_SO_B_A_EVERY)
        self.need(self.switch_retry(xn, f'{tag}切心', assume_con=full, time_out=self.SO_TO_XN_TIME_OUT),
                  'suoming -> hsin')
        return full

    def seg5_so_r(self, so, xn, tag, brk=False):
        """5R 锁暝·R(心转相的同奏进来, 进念深): R [没好边 A 边按] -> 演完 -> [brk: 点 A x R5_SO_R_A_TAPS 让处决提示出来,
        能处决就处决] -> 带着 5s 同奏切心(变奏·照世相)。不 Q(声骸第一次上场放过了)。
        处决演出里倒计时停, 同奏只用掉收招那点。返回心带不带变奏。"""
        self.wait_until_time(self.switch_press_at + self.SO_R_LEAD)
        fired = self.so_r_asap(so, tag, self.LQ_SO_R_CAP, allow_e=not so.has_intro)
        if fired:
            self.so_tongzou_at = time.time()
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.SO_R_ANIM_MIN, time_out=self.SO_R_ANIM_CAP)
            r_taps = self.R5_SO_R_A_TAPS if brk else 0
            for i in range(r_taps):
                if i:
                    self.macro_wait(max(0.0, self.R5_SO0_A_EVERY - self.A_TAP))
                self.tap()
            if r_taps:
                self.macro_mark(f'{tag}R后A(x{r_taps})')
            if brk and self.try_break(tag, prompt_wait=self.R5_SO_R_BREAK_PROMPT_WAIT):
                self.broke_this_lap = True
            full = True
        else:
            self.logger.warning(f'{tag}: 锁暝 R 没放出去(能量不够 / 没进念深) — 心拿不到同奏')
            full = self.read_con(1.0, settle=0.15) >= 1.0
        self.need(self.switch_retry(xn, f'{tag}切心', assume_con=full), 'suoming -> hsin')
        return full

    def run_cycle_5r(self, so, ly, opener, tag):
        """5R 一圈。启动: 心E-锁Q-莉R(出梦乡)-2A-E-Q-重击 -> 心1; 循环: 莉E-A -> 心0 2A-E -> 锁0 2A-闪-2A-Q
        -> 莉 重击-Q-R -> 锁b 无舍念-刻心念 -> 心1; 之后: 锁R -> 心2 Q-统御众机-万阙垂天 -> 莉莉变奏入场、HUD 回来。"""
        marks = len(self.macro_marks)
        self.timeline_logged = False
        if opener:
            self.seg5_open_head(so, ly, tag=tag)
            self.seg5_ly_open(ly, self, tag=f'{tag}莉')
        else:
            late_e = self.R5_LY_4STAR and self.loop_count == 1
            self.seg5_ly_loop(ly, self, tag=f'{tag}莉', e_now=not late_e)
            self.seg_xn0(self, so, tag=f'{tag}心0', fill=False, a_until=self.R5_XN0_A_UNTIL)
            self.seg5_so0(so, ly, tag=f'{tag}锁0')
            self.seg5_ly_heavy(ly, so, tag=f'{tag}莉R', e_first=late_e)
            self.seg5_so_b(so, self, tag=f'{tag}锁b')
        self.seg_xn1(self, so, tag=f'{tag}心1', echo=False, e_after=False,
                     tap_from=self.R5_XN1_TAP_FROM, tap_every=self.R5_XN1_TAP_EVERY,
                     early_after=self.R5_XN1_EARLY_AFTER if opener else self.R5_XN1_EARLY_AFTER_DIR,
                     early_r=self.R5_XN1_EARLY_R, early_hold=self.R5_XN1_EARLY_HOLD,
                     dodge_keys=None if opener else self.R5_XN1_DODGE_KEYS,
                     repress_min=0.0 if opener else self.R5_XN1_REPRESS_MIN)
        intro = self.seg5_so_r(so, self, tag=f'{tag}锁R', brk=self.R5_SO_R_BREAK and not opener)
        full = self.seg_xn2(self, intro, tag=f'{tag}心2', echo_first=True,
                            con_e=self.R5_XN2_CON_E, con_cap=self.R5_XN2_CON_CAP)
        self.need(self.switch_to_ly(ly, f'{tag}切莉', full), 'hsin -> lily')
        self.logger.info(f'{tag} cycle timeline: {self.macro_timeline(marks)}')
        self.timeline_logged = True

    # ==================== 一圈 ====================

    def run_cycle(self, so, ly, opener, tag):
        """莉(启动: 2A-E / 循环: 变奏入场 E) -> 心 2A-E -> 莉 织梦重击-终章-Q-R -> 锁暝 R-A-Q -> 心 步红尘-Q-转相-E
        -> 锁暝 刻心念 -> 心 统御众机-万阙垂天 -> 莉莉变奏入场、HUD 回来。AXIS_KIND='5R' 走 run_cycle_5r。"""
        if self.AXIS_KIND == '5R':
            return self.run_cycle_5r(so, ly, opener, tag)
        marks = len(self.macro_marks)
        self.timeline_logged = False
        if opener:
            self.seg_ly_open(ly, self, tag=f'{tag}莉')
        else:
            self.seg_ly_loop(ly, self, tag=f'{tag}莉')
        self.seg_xn0(self, ly, tag=f'{tag}心0')
        self.seg_ly_heavy(ly, so, tag=f'{tag}莉R', opener=opener)
        self.seg_lq_so_a(so, self, tag=f'{tag}锁a')
        self.seg_xn1(self, so, tag=f'{tag}心1')
        intro = self.seg_so_b(so, self, tag=f'{tag}锁b')
        full = self.seg_xn2(self, intro, tag=f'{tag}心2')
        self.need(self.switch_to_ly(ly, f'{tag}切莉', full), 'hsin -> lily')
        self.logger.info(f'{tag} cycle timeline: {self.macro_timeline(marks)}')
        self.timeline_logged = True

    def macro_enter(self, entry_char):
        so = self.teammate('Suoming')
        ly = self.teammate('Lily')
        if so is None or ly is None:
            return None
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = entry_char or self.on_field_char() or self
        for char in (self, so, ly):
            char.check_f_on_switch = False
        self.macro_start()
        self.timeline_logged = False
        self.hud_seen_at = time.time()
        self.hud_watch = True
        return so, ly, previous

    def macro_exit(self, previous):
        # 长按跨了好几个方法, 中途抛异常的话鼠标会一直按着。这里是唯一保证松手的地方
        self.release_attack()
        # 中途被停(关自动战斗/切出窗口)也要把时间轴写下来
        if not self.timeline_logged and self.macro_marks:
            self.logger.info(f'xsl timeline (没跑完): {self.macro_timeline()}')
            self.timeline_logged = True
        self.hud_watch = False
        self.task.skip_combat_check = previous
        self.mark_busy(False)

    def perform_opener(self, entry_char=None):
        """启动轴: 切第一个人(5R 心 / 12W 莉莉) -> 一圈。返回时莉莉刚变奏入场、HUD 在场。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        so, ly, previous = got
        self.logger.info(f'xsl opener entry: {self.cur.name} (axis {self.AXIS_KIND})')
        try:
            self.loop_count = 0
            self.so_tongzou_at = -99.0
            first = self if self.AXIS_KIND == '5R' else ly
            if self.cur is not first:
                self.need(self.switch_retry(first, 'op', read_con=True), f'opener: switch to {first.name}')
            self.run_cycle(so, ly, opener=True, tag='op')
            self.logger.info(
                f'xsl opener total {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'xsl opener broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    def perform_loop(self, entry_char=None):
        """循环轴一圈。场上不是莉莉(宏散过)就先把场子交给她。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        so, ly, previous = got
        try:
            if self.cur is not ly:
                self.need(self.switch_to_ly(ly, 'L接莉'), 'loop: hand the field to lily')
            self.loop_count += 1
            n = self.loop_count
            self.run_cycle(so, ly, opener=False, tag=f'L{n}')
            self.logger.info(
                f'xsl loop #{n} {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'xsl loop broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    # ==================== 框架接口 ====================

    def drive(self, entry):
        """三个人的 do_perform 都走这里。entry = 框架这次调的是谁(= 场上是谁)。"""
        # 【HUD 不在就什么都不做】: 关卡结束/结算/过场时框架还会接着调进来(F 开始战斗那边撑着不脱战)
        if not self.wait_hud_up(cap=self.DRIVE_HUD_CAP, tag='drive'):
            if time.time() - self.drive_log_at > 10:
                self.drive_log_at = time.time()
                self.logger.info('xsl: 队伍 HUD 不在(结算/过场/窗口没了?), 宏不动')
            return None
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            return self.perform_opener(entry)
        self.logger.info(f'xsl: opener not pending ({blocked}), running the loop')
        return self.perform_loop(entry)

    def do_perform(self):
        if not self.is_in_xsl:
            return super().do_perform()
        self.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时谁都不让框架切(宏自己切莉莉)。"""
        if self.is_in_xsl and self.opener_pending():
            return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        return super().on_combat_end(chars)
