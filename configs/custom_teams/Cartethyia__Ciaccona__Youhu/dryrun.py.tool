"""卡夏双人轴的干跑: 假 task + 假游戏, 真跑一遍启动轴和几圈循环。

验证的是【调用链、判据分支和逐格墙钟】, 不是打得对不对。判据方法(形态/剑/决意/回路/R 亮)直接在角色实例上换成假游戏的读数。
用法: 放在 <working>/configs/custom_teams/Cartethyia__Ciaccona__Youhu/ 里, 用 ok-ww 自带 python 跑:
      <ok-ww 安装目录>/data/apps/ok-ww/python/python.exe dryrun.py.tool
环境变量:
  DRY_LOOPS=2            循环几圈
  DRY_OP_FULL=1          开局卡提能量满(1, 手打那局就是) / 不满(0, 走攒能量段)
  DRY_SET=名=值,...       改持宏类的常量再跑(例: DRY_SET=Y3_A_TO_E=2.6 看模型抓不抓得到)
  DRY_FAIL=a,b,...       故意失败(每圈各触发一次), 测保底分支:
                         ecd       小卡 E 一直在 CD(走跳下落)
                         form      芙 R 化身卡提第一下被吞
                         lib       芙一直攒不满决意(显化结束变回小卡, 没 R2)
                         cir       夏空 R 能量不够
                         ciforte   夏空回路一直不满(不按四拍重奏)
                         sw        切夏空第一下被拒 2.8s
                         e2        芙 E2 进 CD 读不到(到点硬切)
                         eeat      小卡 E 亮着但按下去被吞 0.3s(核对进 CD 失败 -> 补跳下落)
                         cicon     夏空协奏最多到 0.85(Y2 等协奏撞上限, 卡提 Y3 没变奏)
                         ctcon     卡提协奏最多到 0.90(夏空 Y2 没变奏, 回路不 +1)
                         late2     神权剑晚 4 下 A 才出(Y3 跳下落前等它)
                         brk       小卡变奏到场 1.2s 出处决提示(处决完连段从 A1 重来)
                         hover     X2 夏空进场是普通空中(芙离 Boss 太高): 1.45s 才落地, 之前按 E 打断下落
"""
import os
import sys
import time
import types
from pathlib import Path


def find_working():
    marker = Path('src') / 'char' / 'CustomCharLoader.py'
    cands = [Path(os.environ['OKWW_WORKING'])] if os.environ.get('OKWW_WORKING') else []
    here = Path(os.path.abspath(__file__))
    cands += [*here.parents, Path.cwd(), *Path.cwd().parents]
    for c in cands:
        if (c / marker).is_file():
            return str(c)
    sys.exit('找不到 ok-ww 的 working 目录, 设环境变量 OKWW_WORKING')


sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
WORKING = find_working()
sys.path.insert(0, WORKING)
os.chdir(WORKING)

DRY_LOOPS = int(os.environ.get('DRY_LOOPS', '2'))
FAILS = set(x for x in os.environ.get('DRY_FAIL', '').split(',') if x)
OP_FULL = os.environ.get('DRY_OP_FULL', '1') == '1'
LOG = []
T0 = [time.time()]
CT, CI, YH = 0, 1, 2
NAMES = ['卡', '夏', '釉']


def rec(what):
    LOG.append((time.time() - T0[0], what))


class FakeExecutor:
    trigger_tasks = []
    onetime_tasks = []
    interaction = None

    def check_enabled(self, check_pause=False):
        pass


class Game:
    def __init__(self):
        self.on_field = CT
        self.pending = None
        self.hidden = []
        self.lock_until = 0.0
        self.refuse_until = 0.0
        self.cycle = 0
        self.fail_used = set()
        self.holding = False
        self.hold_at = 0.0
        self.taps_since = 0
        # 卡提
        self.form = 'small'
        self.form_cd = 0.0
        self.manifest_left = 0.0       # 显化剩余(小卡形态暂停)
        self.manifest_tick = time.time()
        self.resolve = 0.0
        self.ct_energy = 100.0 if OP_FULL else 30.0
        self.ct_e_cd = 0.0
        self.ct_e_at = -10.0
        self.eat_until = 0.0
        self.fl_e_cd = 0.0
        self.fl_e1_at = -10.0
        self.fl_e2_until = 0.0
        self.fl_e2_at = -10.0
        self.swords = {'sword1': False, 'sword2': False, 'sword3': False}
        self.air_until = 0.0
        self.heavy_lock = 0.0          # 重击按下后 1.15s 之内按 E 被收招吞(实机 X4)
        self.locked = False            # 锁怪(中键)
        self.brk_at = None             # 处决提示出现的时刻
        self.ci_land_due = None        # X2 夏空下落落地(回路涨)的时刻
        # 处决完连段从 A1 重来: 2.05s 起 A4, 再 45F 出神权剑; A4 起手前按 E 就把这一套打断了
        self.a4_at = None
        self.sword2_due = None
        # 夏空
        self.ci_energy = 100.0
        self.forte = 0
        self.ci_taps = 0
        # 协奏(0~1, 满了切人 = 下一个人变奏, 清零); 四拍重奏那 0.25 晚 0.3s 才到账
        self.con = [0.0, 0.0, 0.0]
        self.con_due = []

    def add_con(self, who, v):
        cap = 0.85 if (who == CI and 'cicon' in FAILS) else 0.90 if (who == CT and 'ctcon' in FAILS) else 1.0
        self.con[who] = min(cap, self.con[who] + v)

    def fail_once(self, name):
        if name not in FAILS or (name, self.cycle) in self.fail_used:
            return False
        self.fail_used.add((name, self.cycle))
        rec(f'!!故意失败:{name}')
        return True

    def hud(self):
        now = time.time()
        return not any(a <= now < b for a, b in self.hidden)

    def hide(self, dur):
        now = time.time()
        self.hidden.append((now, now + dur))
        self.lock_until = max(self.lock_until, now + dur)

    def tick(self):
        now = time.time()
        if self.form == 'fleur' and self.manifest_left > 0 and self.hud():
            self.manifest_left -= now - self.manifest_tick
            if self.manifest_left <= 0 and 'lib' in FAILS:
                self.form = 'small'
                rec('显化结束 -> 变回小卡')
        self.manifest_tick = now
        if self.ci_land_due is not None and now >= self.ci_land_due:
            self.ci_land_due = None
            if self.on_field == CI:
                self.forte = min(3, self.forte + 1)
                rec('夏空下落落地(回路+1)')
        if self.sword2_due is not None and now >= self.sword2_due:
            self.sword2_due = self.a4_at = None
            self.swords['sword2'] = True
            rec('神权剑(处决后那套 A4)')
        for due in [d for d in self.con_due if now >= d[0]]:
            self.con_due.remove(due)
            self.add_con(due[1], due[2])
        if self.pending and now >= self.pending[1]:
            dst, _, intro = self.pending
            self.pending = None
            self.on_field = dst
            self.taps_since = 0
            rec(f'到场{NAMES[dst]}{"(变奏)" if intro else ""}')
            if intro:
                self.add_con(dst, 0.10)
                if dst == CT and self.form == 'small' and self.fail_once('brk'):
                    self.brk_at = now + 1.2
            if dst == CI:
                # 芙 E2 往下劈那段(起手后 0.26~0.67s)切进来才是空中出场下落
                air = self.form == 'fleur' and 0.26 <= now - self.fl_e2_at <= 0.67
                if air and self.fail_once('hover'):
                    air = False
                if intro:
                    self.forte = min(3, self.forte + 1)
                self.ci_taps = 0
                if self.form == 'fleur':
                    # 落地后的打击才涨回路: 接上下落 0.75s; 悬空的 A1 -> A2-下落 1.45s
                    self.ci_land_due = now + (0.75 if air else 1.45)
                    rec(f'夏空空中进场下落(E2后{now - self.fl_e2_at:.2f}s)' if air
                        else f'!!夏空没接上下落(E2后{now - self.fl_e2_at:.2f}s)')
            if dst == CT and self.form == 'small' and intro:
                self.swords['sword1'] = True
        if self.holding and self.on_field == CI and self.forte >= 3 and now - self.hold_at >= 0.7:
            self.forte = 0
            self.ci_energy += 25
            self.con_due.append((now + 0.3, CI, 0.25))
            rec('四拍重奏出手(回路清空)')

    def press(self, key, down_time):
        self.tick()
        now = time.time()
        if key in ('1', '2', '3'):
            dst = int(key) - 1
            if dst == self.on_field or self.pending:
                return
            if now < self.lock_until or now < self.refuse_until:
                return
            if dst == CI and self.fail_once('sw'):
                self.refuse_until = now + 2.8
                return
            out = self.on_field
            intro = self.con[out] >= 1.0
            if intro:
                self.con[out] = 0.0
            self.pending = (dst, now + 0.08, intro)
            return
        if not self.hud() or now < self.lock_until:
            return
        who = self.on_field
        if key == 'f':
            if self.brk_at is not None and now >= self.brk_at:
                self.brk_at = None
                self.lock_until = now + 1.5
                self.taps_since = 0
                self.a4_at = now + 1.5 + 2.05
                self.sword2_due = self.a4_at + 0.75
                rec('处决(连段重来)')
            return
        if key == 'e':
            if who == CT and self.form == 'small':
                if now < self.heavy_lock:
                    rec('!!小卡E被重击收招吞')
                    return
                if now >= self.ct_e_cd and 'ecd' not in FAILS:
                    if now < self.eat_until or self.fail_once('eeat'):
                        self.eat_until = max(self.eat_until, now + 0.3)
                        rec('!!小卡E被吞')
                        return
                    if self.a4_at is not None and now < self.a4_at:
                        rec(f'!!A4 没起手就按了 E(差 {self.a4_at - now:.2f}s, 中间那把没了)')
                        self.a4_at = self.sword2_due = None
                    self.ct_e_cd = now + 11.0
                    self.ct_e_at = now
                    self.swords['sword3'] = True
                    self.air_until = now + 1.0
                    self.ct_energy += 10
                    self.add_con(CT, 0.15)
                    rec('小卡E(人权剑, 升空)')
            elif who == CT:
                if now < self.fl_e_cd:
                    pass
                elif now - self.fl_e1_at > 2.2:
                    self.fl_e1_at = now
                    self.resolve += 10
                    rec('芙E1(升空)')
                elif now - self.fl_e1_at >= 0.92:
                    self.fl_e_cd = now + 1.0
                    self.fl_e2_until = now + (0 if self.fail_once('e2') else 0.15)
                    self.fl_e2_at = now
                    self.resolve += 10
                    rec('芙E2')
            elif who == CI:
                if self.ci_land_due is not None:
                    self.ci_land_due = None
                    rec('!!夏空 E 打断了下落')
                self.ci_energy += 15
                self.add_con(CI, 0.15)
        elif key == 'q':
            rec(f'Q({NAMES[who]})')
        elif key == 'r':
            if who == CI:
                if self.ci_energy >= 100 and not self.fail_once('cir'):
                    self.ci_energy = 0
                    self.add_con(CI, 0.20)
                    self.hide(3.4)
                    rec('夏空R(演绎)')
                return
            if who != CT:
                return
            if self.form == 'small' and self.manifest_left <= 0:
                if self.ct_energy >= 100:
                    self.ct_energy = 0
                    self.form = 'fleur'
                    self.manifest_left = 12.0
                    self.resolve = 0
                    self.hide(3.38)
                    rec('R1 变身')
            elif now >= self.form_cd:
                if self.form == 'fleur' and self.resolve >= 120:
                    self.form = 'small'
                    self.manifest_left = 0
                    self.resolve = 0
                    self.swords = {k: False for k in self.swords}
                    self.hide(5.0)
                    rec('R2')
                elif self.form == 'fleur':
                    if self.fail_once('form'):
                        return
                    self.form = 'small'
                    self.form_cd = now + 1.5
                    rec('化身卡提')
                else:
                    self.form = 'fleur'
                    self.form_cd = now + 1.5
                    rec('化身芙')

    def tap(self):
        self.tick()
        if not self.hud() or time.time() < self.lock_until:
            return
        who = self.on_field
        self.taps_since += 1
        if who == CT:
            self.add_con(CT, 0.03)
            if self.form == 'small':
                self.ct_energy += 2
                if time.time() < self.air_until and any(self.swords.values()):
                    n = sum(self.swords.values())
                    self.swords = {k: False for k in self.swords}
                    self.air_until = 0
                    self.ct_energy += 15
                    self.add_con(CT, 0.10)
                    rec(f'下落吸{n}剑')
                elif self.a4_at is None and self.taps_since % (26 if 'late2' in FAILS else 22) == 0:
                    self.swords['sword2'] = True
            else:
                self.resolve += 0 if 'lib' in FAILS else 3.6
        elif who == CI:
            self.ci_energy += 3
            self.add_con(CI, 0.012)
            self.ci_taps += 1
            if self.ci_taps % 10 == 0 and 'ciforte' not in FAILS:
                self.forte = min(3, self.forte + 1)

    def heavy(self):
        if self.on_field == CT and self.form == 'small':
            self.heavy_lock = self.hold_at + 1.15
            self.swords['sword1'] = True
            self.ct_energy += 8
            self.add_con(CT, 0.08)
            rec('小卡重击(异权剑)')


G = Game()


class FakeTask:
    def __init__(self):
        self.executor = FakeExecutor()
        self.chars = []
        self.key_config = {'Echo Key': 'q', 'Liberation Key': 'r', 'Resonance Key': 'e',
                           'Jump Key': 'space', 'Dodge Key': 'lshift'}
        self.skip_combat_check = False
        self.use_liberation = True
        self.in_liberation = False
        self._f_last_press = -1
        self.frame = None
        self.enabled = True
        self.can_break = False

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        end = time.time() + max(down_time, 0.003)
        G.press(str(key), down_time)
        left = end - time.time()
        if left > 0:
            time.sleep(left)

    def send_key_down(self, key, after_sleep=0):
        pass

    def send_key_up(self, key, after_sleep=0):
        pass

    def jump(self, after_sleep=0.01):
        if G.hud() and G.on_field == CT:
            G.air_until = time.time() + 0.8
            rec('跳')

    def mouse_down(self, *a, **kw):
        if kw.get('key') == 'right':
            rec(f'闪避({NAMES[G.on_field]})')
            return
        G.tick()
        if not G.holding:
            G.holding = True
            G.hold_at = time.time()
        time.sleep(0.002)

    def mouse_up(self, *a, **kw):
        if kw.get('key') == 'right':
            return
        if G.holding:
            if time.time() - G.hold_at < 0.25:
                G.tap()
            else:
                G.heavy()
        G.tick()
        G.holding = False

    def next_frame(self):
        time.sleep(0.03)
        G.tick()

    def in_team(self):
        return (True, G.on_field, 3) if G.hud() else (False, -1, 0)

    def get_current_con(self):
        return round(G.con[G.on_field], 3) if G.hud() else 0.0

    def get_cd(self, name, index=None):
        return 0.0

    def find_one(self, *a, **kw):
        return None

    def add_freeze_duration(self, *a, **kw):
        pass

    def box_of_screen(self, *a, **kw):
        return None

    def middle_click(self, *a, **kw):
        if G.hud():
            G.locked = True
            rec('中键锁怪')

    def is_pick_f(self):
        return False


task = FakeTask()

from src.char.BaseChar import CharType  # noqa: E402
from src.char import CustomCharLoader as L  # noqa: E402
from src.char.Cartethyia import Cartethyia as NCart  # noqa: E402
from src.char.Ciaccona import Ciaccona as NCiac  # noqa: E402
from src.char.Youhu import Youhu as NYouhu  # noqa: E402

team = ('Cartethyia', 'Ciaccona', 'Youhu')
CCart = L.load_team_char_class(NCart, team)
CCiac = L.load_team_char_class(NCiac, team)
CYouhu = L.load_team_char_class(NYouhu, team)
assert CCart is not NCart and CCiac is not NCiac and CYouhu is not NYouhu, 'custom team code not loaded'


class Logger:
    def info(self, m):
        rec(f'[info] {m}')

    def warning(self, m):
        rec(f'[WARN] {m}')

    def debug(self, m):
        pass

    def error(self, m):
        rec(f'[ERR] {m}')


def build(cls, index, name, char_type):
    c = object.__new__(cls)
    c.task = task
    c.index = index
    c.char_name = name
    c.logger = Logger()
    c._char_type = char_type
    c.is_current_char = index == CT
    c.has_intro = False
    c.has_sub_dps_intro = False
    c.current_con = 0
    c.last_switch_in_time = 0
    c.last_switch_time = 0
    c.last_outro_time = 0
    c._buff_time = 0
    c.last_buff_time = 0
    c.check_f_on_switch = False
    c.last_res = c.last_liberation = c.last_echo = 0
    c._resonance_available = c._echo_available = c._liberation_available = False
    c.freeze_durations = []
    c.is_cartethyia = True
    if cls is not CCart:
        c.intro_motion_freeze_duration = 0.73
    c.add_freeze_duration = lambda *a, **kw: None
    c.time_elapsed_accounting_for_freeze = lambda start, *a, **kw: time.time() - start
    c.switch_out = types.MethodType(lambda self, con_full=False: setattr(self, 'is_current_char', False), c)
    c.record_liberation_use = lambda *a, **kw: None
    c.send_resonance_key = lambda *a, **kw: task.send_key('e', down_time=0.01)
    c.send_echo_key = lambda *a, **kw: task.send_key('q', down_time=kw.get('down_time', 0.01))
    c.send_liberation_key = lambda *a, **kw: task.send_key('r', down_time=0.01)
    if cls is CCart:
        c.__dict__.update(dict(opener_armed=True, last_opener=-1, macro_t0=0.0, macro_marks=[], cur=None,
                               holding_attack=False, switch_press_at=0.0, lib_press_at=0.0, loop_count=0,
                               hud_seen_at=0.0, hud_watch=False, drive_log_at=0.0, timeline_logged=True,
                               breaks=0, break_seen_at=None, form_pressed_at=0.0, ci_level_left=-1.0))
    return c


cart = build(CCart, CT, 'char_cartethyia', CharType.MAIN_DPS)
ciac = build(CCiac, CI, 'char_ciaccona', CharType.SUB_DPS)
youhu = build(CYouhu, YH, 'char_youhu', CharType.HEALER)
task.chars = [cart, ciac, youhu]

on = lambda who: G.hud() and G.on_field == who  # noqa: E731
cart.is_small = lambda: on(CT) and G.form == 'small'
cart.is_lib_big_available = lambda: on(CT) and G.form == 'fleur' and G.resolve >= 120
cart.get_sword_buffs = lambda: dict(G.swords) if on(CT) and G.form == 'small' else {k: False for k in G.swords}
cart.is_mid_air_attack_available = lambda: on(CT) and G.form == 'small' and any(G.swords.values())
# 小卡 E 按下后 0.7s 才出冷却数字, 这段时间技能栏还读成可用(实机 X4)
cart.resonance_available = lambda: on(CT) and 'ecd' not in FAILS and (
    time.time() >= G.fl_e_cd if G.form == 'fleur' else (time.time() >= G.ct_e_cd or time.time() < G.ct_e_at + 0.7))


def cart_has_cd(box):
    if not on(CT) or box != 'resonance':
        return False
    now = time.time()
    if G.form == 'fleur':
        return now < G.fl_e2_until
    return 'ecd' in FAILS or G.ct_e_at + 0.7 <= now < G.ct_e_cd


cart.has_cd = cart_has_cd
cart.target_locked = lambda: G.locked
cart.break_prompt = lambda: on(CT) and G.brk_at is not None and time.time() >= G.brk_at
cart.liberation_available = lambda *a, **kw: on(CT) and G.form == 'small' and G.manifest_left <= 0 \
    and G.ct_energy >= 100
ciac.is_mouse_forte_full = lambda: on(CI) and G.forte >= 3
ciac.forte_level = lambda: float(G.forte) if on(CI) else None
ciac.liberation_available = lambda *a, **kw: on(CI) and G.ci_energy >= 100

for kv in [x for x in os.environ.get('DRY_SET', '').split(',') if x]:
    k, v = kv.split('=')
    setattr(cart, k, type(getattr(cart, k))(v))
    rec(f'DRY_SET {k}={v}')

ok = True
try:
    rec('==== 启动轴 ====')
    cart.drive(cart)
    for n in range(DRY_LOOPS):
        G.cycle = n + 1
        rec(f'==== 循环 #{n + 1} ====')
        cur = next((c for c in task.chars if c.index == G.on_field), cart)
        cart.drive(cur)
except Exception:
    import traceback
    ok = False
    rec('!!EXCEPTION ' + traceback.format_exc())
for t, w in LOG:
    print(f'{t:7.2f}  {w}')
print('OK' if ok else 'FAILED')
