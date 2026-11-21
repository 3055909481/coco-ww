"""绯雪单人轴干跑: 假游戏模型 + 假 task, 把 Hiyuki.do_perform 真跑一遍。

用法(在 working 目录): ..\\python\\python.exe configs\\custom_teams\\Chixia__Hiyuki__Youhu\\dryrun.py.tool [场景]
场景: yq(已变身, 大世界) / normal(副本, 先变身) / all(默认)
看什么:
  - 每一刀居合: 闪避 -> 出刀(模型 0.50s) -> 下一次闪避 的间隔, 以及有没有"出刀前就闪避"(!!! 标出来)
  - boss 死后还发了几个输入、最后一个离死亡多久
模型的时序取自视频实测, 不是游戏真值; 用来查流程和保底分支, 不用来定常量。
"""
import os
import sys
import time
import importlib.util

sys.path.insert(0, os.getcwd())

from src.task.BaseCombatTask import NotInCombatException  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class Game:
    STANCE_TO_CAST = 0.50
    STANCE_ICON_DELAY = 0.09
    A_GAIN = 14
    E_GAIN = 75
    LIB_GAIN = 50
    LIB_ANIM = 4.0
    HOLD_TO_LIB = 3.0

    def __init__(self, form, death_at, start_cold=0, eat_dodges=(), no_buffer=False):
        self.t0 = time.time()
        self.eat_dodges = set(eat_dodges)   # 第几次闪避被吞(从 1 数)
        self.no_buffer = no_buffer          # 架势不缓存左键: 只认派生帧之后的点击
        self.dodge_n = 0
        self.form = form
        self.cold = start_cold
        self.death_at = death_at
        self.stance_at = None
        self.lmb_buffered = False
        self.cast_pending_at = None
        self.left_icon = False
        self.hud_off_until = 0
        self.lib_lit = False
        self.hold_start = None
        self.inputs = []
        self.casts = []
        self.bad = []

    def now(self):
        return time.time() - self.t0

    def log(self, what):
        self.inputs.append((self.now(), what))

    def tick(self):
        t = self.now()
        if self.stance_at is not None and self.cast_pending_at is None and self.lmb_buffered \
                and t >= self.stance_at + self.STANCE_TO_CAST:
            self.do_cast(self.stance_at + self.STANCE_TO_CAST)
        if self.hold_start is not None and t - self.hold_start >= self.HOLD_TO_LIB:
            self.lib_lit = True

    def do_cast(self, at):
        self.cold -= 100
        self.casts.append(at)
        self.stance_at = None
        self.lmb_buffered = False
        self.cast_pending_at = None
        self.last_cast = at
        if self.cold < 100:
            self.left_icon = False

    def crest(self):
        return self.cold >= 100

    # 输入
    def lmb(self):
        self.tick()
        t = self.now()
        self.log('A')
        if self.stance_at is not None:
            if t >= self.stance_at + self.STANCE_TO_CAST:
                self.do_cast(t)
            elif not self.no_buffer:
                self.lmb_buffered = True
        elif self.form == 'yq' or self.form == 'normal':
            self.cold = min(300, self.cold + self.A_GAIN) if self.form == 'yq' else self.cold

    def rmb(self):
        self.tick()
        t = self.now()
        self.dodge_n += 1
        if self.dodge_n in self.eat_dodges:
            self.log('D(eaten)')
            return
        self.log('D')
        if self.stance_at is not None:
            self.bad.append(f'{t:.2f} dodge inside stance (before cast) -> stance lost')
            self.stance_at = None
            self.lmb_buffered = False
            return
        if self.form == 'yq' and self.crest():
            self.stance_at = t
            self.left_icon = True

    def key(self, k):
        self.tick()
        self.log(k)
        if k == 'e' and self.form == 'yq':
            self.cold = min(300, self.cold + self.E_GAIN)
        if k == 'r' and self.form == 'normal' and self.lib_lit:
            self.form = 'yq'
            self.cold = self.LIB_GAIN
            self.hud_off_until = self.now() + self.LIB_ANIM
            self.lib_lit = False

    # 读屏
    def right_icon(self):
        self.tick()
        return self.form == 'yq' and self.crest() and self.stance_at is None and self.hud()

    def left(self):
        self.tick()
        t = self.now()
        return self.form == 'yq' and self.left_icon and self.hud() and (
            self.stance_at is None or t >= self.stance_at + self.STANCE_ICON_DELAY)

    def hud(self):
        return self.now() >= self.hud_off_until


class Box:
    def scale(self, *a, **k):
        return self


class Hit:
    pass


class FakeExecutor:
    def check_enabled(self, check_pause=True):
        pass


class FakeTask:
    def __init__(self, game):
        self.g = game
        self.executor = FakeExecutor()
        self.combat_start = 1.0
        self.chars = []
        self.in_liberation = False
        self.can_break = False
        self.skip_combat_check = False
        self._in_combat = True
        self.key_config = {}

    def check_combat(self):
        if self.g.now() >= self.g.death_at:
            raise NotInCombatException('boss dead')

    def next_frame(self):
        time.sleep(0.016)

    def find_one(self, name, **kw):
        if name == 'hiyuki_right':
            return Hit() if self.g.right_icon() else None
        if name == 'hiyuki_left':
            return Hit() if self.g.left() else None
        return None

    def get_target_names(self):
        return 'has_target', 'no_target'

    def get_box_by_name(self, name):
        b = Box()
        b.name = name
        return b

    def find_best_match_in_box(self, box, names, threshold=0):
        if not self.g.hud():
            return None
        want = 'target_box_long2' if self.g.form == 'yq' else 'box_target_enemy_long'
        return Hit() if box.name == want else None

    def in_team(self):
        return self.g.hud(), 0, 3

    def has_target(self):
        return self.g.now() < self.g.death_at

    def mouse_down(self, key='left', **kw):
        if key == 'right':
            self.g.rmb()
        else:
            self.g.hold_start = self.g.now()
            self.g.lmb()

    def mouse_up(self, key='left', **kw):
        if key != 'right':
            self.g.hold_start = None

    def send_key(self, k, down_time=0.02, **kw):
        self.g.key(k)
        time.sleep(down_time)

    def send_key_down(self, k):
        self.g.key(k)

    def send_key_up(self, k):
        pass

    def box_of_screen(self, *a, **k):
        return Box()

    def is_pick_f(self):
        return False

    def sleep(self, s):
        time.sleep(s)

    def add_freeze_duration(self, *a, **k):
        pass


def load_hiyuki():
    path = os.path.join(HERE, 'Hiyuki.py')
    spec = importlib.util.spec_from_file_location('hiyuki_dry', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Hiyuki


def run(form, death_at, start_cold=0, **knobs):
    Hiyuki = load_hiyuki()
    g = Game(form, death_at, start_cold, **knobs)
    task = FakeTask(g)
    char = Hiyuki(task, 0, char_name='char_hiyuki')
    char.is_current_char = True
    char.get_resonance_key = lambda: 'e'
    char.get_echo_key = lambda: 'q'
    char.get_liberation_key = lambda: 'r'
    char.liberation_available = lambda check_color=True: (g.tick(), g.lib_lit)[1]
    char.add_freeze_duration = lambda *a, **k: None
    import logging
    logging.basicConfig(level=logging.WARNING)
    char.logger = logging.getLogger('dry')
    char.logger.info = lambda m: print('   log:', m)
    try:
        char.do_perform()
    except NotInCombatException:
        pass
    print(f'--- form={form} death@{death_at:.1f}s {knobs}')
    dodges = [t for t, w in g.inputs if w == 'D']
    for i, c in enumerate(g.casts):
        before = [d for d in dodges if d <= c]
        after = [d for d in dodges if d > c]
        d0 = before[-1] if before else float('nan')
        d1 = after[0] if after else float('nan')
        print(f'   cast{i + 1} @{c:.2f}  dodge->cast {c - d0:.2f}  cast->next dodge {d1 - c:.2f}')
    late = [(t, w) for t, w in g.inputs if t >= death_at]
    last = [t for t, w in g.inputs if t < death_at]
    print(f'   last input before death: {death_at - last[-1]:.2f}s before' if last else '   no input')
    print(f'   inputs after death: {len(late)} {" ".join(f"{w}{t - death_at:+.2f}" for t, w in late)}')
    for b in g.bad:
        print('   !!!', b)
    return late, g.bad


def sweep(form, t_from, t_to, step):
    """boss 在各个时刻死, 统计死后还发出的输入。"""
    import contextlib
    import io
    worst = []
    bad_total = 0
    t = t_from
    while t <= t_to:
        with contextlib.redirect_stdout(io.StringIO()):
            late, bad = run(form, death_at=t)
        bad_total += len(bad)
        worst.append((len(late), t, ' '.join(f'{w}{x - t:+.2f}' for x, w in late)))
        t += step
    worst.sort(reverse=True)
    print(f'--- sweep form={form} deaths {t_from}..{t_to} step {step}: '
          f'max inputs after death {worst[0][0]}, dodge-inside-stance {bad_total}')
    for n, t, s in worst[:5]:
        print(f'   death@{t:.2f}: {n} {s}')


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if which in ('yq', 'all'):
        run('yq', death_at=9.0)
    if which in ('normal', 'all'):
        run('normal', death_at=16.0)
    if which in ('fail', 'all'):
        run('yq', death_at=9.0, eat_dodges={2})
        run('yq', death_at=9.0, no_buffer=True)
    if which in ('sweep', 'all'):
        sweep('yq', 0.5, 12.0, 0.41)
        sweep('normal', 1.0, 18.0, 0.53)
