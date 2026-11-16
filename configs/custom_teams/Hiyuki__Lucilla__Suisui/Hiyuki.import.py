import base64
import ctypes
import threading
import time

import cv2
import numpy as np

from src.char.BaseChar import SwitchPriority
from src.char.Hiyuki import Hiyuki as NativeHiyuki

try:
    from ok.device.interaction_methods.keys import vk_key_dict
except Exception:
    vk_key_dict = {}

# ==================== 物理 F 键监视 ====================
# 跟 Hsin__Rover__Suisui/Hsin.py 里那份逐字相同。
F_VK = 0x46              # 'F'
F_POLL_INTERVAL = 0.05   # 一次按键按住的时间远大于这个, 不会漏采
F_LOG_DEBOUNCE = 1.0     # 按住不放时别刷日志
F_TASK_NAME = 'FStartCombatTask'


# 监视线程的实现在主程序 src/combat/AxisWatch.py (椰果版), 这里只导入
from src.combat.AxisWatch import f_settings, start_enable_watch, start_f_watch


# ==================== 自动战斗开关监视 ====================
# 【玩家手动开关自动战斗, 必须从启动轴重跑】。手动停任务不走 combat_end(), opener_armed 会跨局留在
# False, 而角色对象常被粘性复用、reset_state() 不跑。只有独立线程能观察到 enabled 的 True->False->True。
ENABLE_POLL_INTERVAL = 0.2


# ==================== SendInput(键盘 + 左右键) ====================
# 【整条轴的按键都走 SendInput】: 手打记录里左键是一路按着跨好几次切人的, 换上来的人接着打 ——
# PostMessage 的按住做不到(切人那一下的 WM_ACTIVATE 会把它打断, 新角色也不认旧消息里的按住)。
# 只在游戏窗口在前台时走; 不在前台退回 PostMessage(按住跨切人会失效, 日志里会警告)。
# 【每次现取 ctypes.windll.user32.SendInput】: 跑轴记录插件会把它换成带记录的版本, 缓存住就记不到。
_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008
_MOUSE_FLAGS = {'left': (0x0002, 0x0004), 'right': (0x0008, 0x0010), 'middle': (0x0020, 0x0040)}


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


def _send_input(inp):
    try:
        return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) == 1
    except Exception:
        return False


def si_mouse(button, up):
    """系统级鼠标键按下/抬起(button = 'left' / 'right' / 'middle')。返回是否发出去了。"""
    try:
        inp = _INPUT(type=_INPUT_MOUSE)
        inp.u.mi = _MOUSEINPUT(0, 0, 0, _MOUSE_FLAGS[button][1 if up else 0], 0, 0)
        return _send_input(inp)
    except Exception:
        return False


def si_key(vk, up):
    """系统级键盘按下/抬起(带扫描码)。返回是否发出去了。"""
    try:
        scan = int(ctypes.windll.user32.MapVirtualKeyW(vk, 0))
    except Exception:
        scan = 0
    try:
        inp = _INPUT(type=_INPUT_KEYBOARD)
        flags = (_KEYEVENTF_SCANCODE if scan else 0) | (_KEYEVENTF_KEYUP if up else 0)
        inp.u.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
        return _send_input(inp)
    except Exception:
        return False


# ==================== 居合徽记模板 ====================
# 预求身回路条剑身上那两颗徽记(左 = 第 1 颗, 右 = 第 2 颗)。44x44 灰度, 取自 2560x1440 DVR 原片里一颗满的徽记。
# 离线验过(2K 原图 / 缩到 1080): 有徽记 0.91~1.00(录屏压缩后 0.86~0.96), 没有 <= 0.41, 渐隐那一两帧 0.5~0.8。
IAI_TPL_B64 = (
    'm7HDuo1RU2NeW1taV1ZUVFRVVVZWVlZWVldYWlteYGBiV1VUUFRdYWlra2vBu6p1PDg/aVdaWVdVVFJPTlBPTk1NTU5PUFNVVlpcXGBeWlZJ'
    'UEtPWlZVXZd3dlRVZD9pVmRXVVxYZU4xOTpAQ0hGOTZAQlRnZWdqal5ZXUNqVU15aWppYGljQmdsNV9ZTU5OQjNASFaJkqGsrq2mo39xSDku'
    'Q19qW1lfOXhsPGxmZmdoalgwiH44XF1eTUJJUYLM5enq6uvr6/L1/eCzlVZKRFFUU1kuiZYmX11hYGFcTjWhkkBUVVIxWZzb7ty0joNrX1NZ'
    'Z26Mp+nk0JxfRlJTWi6KpSZVW15fZWFKTdaoNEgzOZfR8cKOVEU+PkBCQ0RGRi1DaHXU/emFOyxGPKnZQlZaXV9oYUdQ1rVGP1u57cmESEJI'
    'XWVlZmdoaWxsYFZBOUuBy/afPDowudxPVVZZXGtjSFLUzFAvq+CtXiJPU1NVVVVbXVteYmJnZ2hmOzNXqf+vOFPY3V9KVVdZbWRJVNPVXjrf'
    'vzcnXFJbXl5dXVxYXltdYWZlWlZyVzIppeQ8ZebdZklTVldxa0xT0umEQYNHUGRfUl1kU2FhXlRIUFZUW2BrbWlkaz08gzyG2dxpTFNXVnNs'
    'TlLP6sdQUzp/f0tSX11WT0xTWFdQU01NUF5mZ1iAcEM9R7XZ2mhOV1pac21PUM7f7YImQLeSRFVVVl1oSkpggFxKXmNcUkxdT4irWx+C393X'
    'Zk5dXV5+bUxQztDUv1ZUw45RVURPZZ5sR2ORYUZllX5NOkhDksF4Q6Pw3tdmTmBeYIlwTl3E4Nnll0nimj9LSEFwlG5FbJVhOWyPakpSVkKk'
    '9VCD4eblxWQ2W11de1ZTcqro1uK3ZN+0M216V2GPc0drlGQ8d45PS3l8N67kc6vg5uyneF9KXGBqQmyEbfPR19O+5tpJZZJsTIR9UWCQZ02N'
    'e0Vfl3RVzdrB39vX8HKDjTxebmFDg4tO4NfO1Nzk93Q7g3BGgINXXY9lVZhuSGygP37h29vb2enRUYeUVFZ5ZSvFlUDK4+TN0uDq7jxermFM'
    'j2FYlV9gjFpmlWM+2fjV4djX48E8kMkrZIBcTNaWKJLp2Nna3+vvnDOAh0h6cleLXm92TIFxMoHp9NXY2NnemTqT1kpZhU9h5pkmWufk3d3h'
    '7O/7SUGRWF2IWoJggltaj0RG2vbv3tjf29VeNpjtbU6HRVzipDpSv9ve3+Ht8fq8MG95T4BlkGJ0WYJ5Nav/7Ovj2eLrtkczm/V4R4hAXN6t'
    'NEF44N/g5Ovx+fuaF4dgYW9zdXJlhy1+/vrs5+Pc2+drSjSb9HFEh0Be3a00TFCm5Obk6O3u/tx6LXmAcm5zenI5YfP/8+nl4t7poVdJNJz0'
    'cUaGP17ZsDNKQnHY6+Pl6uzx9elYXnmDdm5yeV7q9P/t5+Tk5tpsUEg0m/RzRIJAXNmwMzxTVafs5eXm6Oz3/+BnUn1/dm1y7f/z7e3n4+Pf'
    'j1dfSzOj8GhQiUlJzeBHS25iX8Ta5OXr7fXz/9iMa4F9hPfy+PT07/He+LJRU1xOOsHcSmWUVkO4411CYGFYSsns6+7x9PP7/+mrfkhk//Py'
    '8/Tz4vHeclFQbzNp8586YYVfPoL0qDdLZ1xEadDn8/Px9Ov5/+3qZzC98+zx8d/xyGVNVmBlL5v2dTNof15DXdnHTUlnYW9JWtXz8vHv8O7x'
    '9PT2gGHs6u/x+N5lNVdUa1hIxtBWTXmQW1MloPN6K21zbW5SV8Xk8/Tz9Pj7+vj32t3x5e+/UDNlYmNeRHPimDRXYXVbXDRU4LhLO1dVXlI7'
    'Q6r09PHv6t/u8/j38+Dv3TIsUE1tXz5Kv+JrQVdhdVtXSDOb75M1SklYcH6TzPjU5v3519vm7fLr1+jZspVqTUw8MIr3qERQVWFzXkhWXUfR'
    '44BVjJXE6O3g5rtqufnz6+Lb5Nnn0+Tn58yzk2l31dFHUFFSYXJjS1hxf8rw6OTk49nT0dTe45tBY7bP3unp6eff2tra3eXr8/bw152AWVNj'
    'cWFcVVFRd5G1xtbb5Ovs6d/b544+RU58qbXQ1N7h4uHg4MqxoI5xUEpKV2d0Y11WSUExPUJPX2VudX99dG5lYm9zWlU7VmhtdXd0c25tWlBI'
    'NTVSXGtobXhoZWNfX2BLUDY8Ny4sNT5IT1peXWJ0aVtoWlZLQTc2NT1IS1BRWGFnbG9ydmlqamxtZFlQRk6JkmNFKixIVlJQT1RWVlVKPiRD'
    'TmqJeHBNT1xkX2Vpa29zaGlpamxsZFtQO06a1Ni7iGhBPDUxMjIyMjhpla3i1axlT09bamhjZ2psb3JpaGhoaGloY1huQj1kxuH+18q4loWG'
    'hoeH2Ovz66hpNUNGW2hraGVqbG5vcGppaWhoaGhlZmZnUTo2Voyl2d3j6e3u8fHdvXxbOElXVFtmamppaWxvb3BwbGtqaGJmY2NeYl1bUz4z'
    'MVBUWWRqbW1ubjo5OEVQWGFiYmFjamhqbnBvb29ubm5vbWhmZGJkZWVgalxOQ0pGOzk4Ojs7PElXXF5kZWhmaGlsa25wcnFxcA=='
)
IAI_TPL = np.frombuffer(base64.b64decode(IAI_TPL_B64), np.uint8).reshape(44, 44)
_yy, _xx = np.mgrid[-20:20, -20:20]
IAI_DISK = (_xx ** 2 + _yy ** 2) < 20 ** 2

# ==================== E 图标空中/地面模板 ====================
# 绯雪在空中时 E(霜罚)图标是「人+竖月牙」(落华), 在地上是「人+横飘带」(白玉切)。48x48 灰度, 取自 2560x1440 DVR 原片
# (10-31 00:39 / 10-30 23:36 两局各几帧平均); 中间冷却数字那一条和左上角计数遮掉不比。离线验过 2K 和缩到 1080:
# 图标亮着时对的那张 0.89~1.00、另一张 0.44~0.59; 枯霜阶段图标变暗对的那张 0.6~0.9; 白闪那一两帧两张都低, 判不出来。
EAIR_TPL_G_B64 = (
    'q6qpuqqoqKebg4KCgoODg4ODg4OEg4J7cnR1d3l7fYCAgYKCgoGAf39+fn5+f39/qrjq5quoqKmkiYKCg4OEhIWGhoaHiId3ZmNlZWZpb3Z6foCB'
    'gICAgH9/fn19fn5/qtLZ6ayoqKiokYGCg4SEhYWGhoeHh4Z2aGZmZmdmZWdtdHl9f4CAf39+fn19fX1+qarP6KmnqKiplYGDhYaGhoaGhoaHh4V1'
    'ZmVmZ2dmZWVlZ2x0e35/f39+fn59fX1+p6bR6Kinp6eok4KFhoaHhoaGhYSFhoV0ZGRmZmdnZmVkZGZobXV/gX9+fn5+fX19p6fQ6Kmmp6eojYWG'
    'h4aGhoaFl6mkkod0ZWRkZGVlZWRkY2RlZmdyf4B/f35+fX1+pqfO56elp6ehiIaHh4eGhofR6NfLuZd0YmRjZGVlZWVkY2NkY2JkcHyAfn19fX6A'
    'pqeytaSmp6eUhoeGh4eIlrT7/Pv8/fW9ZWVjZGRkZGRkZGJiYmFhZG55fXx9fX2AnqalpaSloJOHhIWGhoaDpuHw897e7fv7z3JkYmNkZWVmZWJh'
    'YWFgYWRte319fX1+g46UlZSSioaEg4ODhYWGxdrWpIaHi7Pm/eahbmNkZXWdmH9lYWFhYGFib318fn19fn1+foKEhIOEg4ODhIWn/P366YaGhoZ6'
    'rOz79uDN2cd1Y2JiYWFhYWBgZHN9fX1+fHx9gYODg4ODg4KCgoPP9/f97oWFhYZ1YnCPqbKqg2RhYWJhYWFhYWFhYWh3fn5/fHx/g4SEg4ODgoKB'
    'goC299rrxIOFhYV0Y2RjY2NiYWBgYGBgYWBhYWFhYWJufH6AfX2DhIODg4KCg4OCgYGH3NO5r6aTgoRzYWFgYGBgYF9fXl1eXl5gYGBgYGBneoCA'
    'foCDhIODg4KBgoKCgYKFlceVvvv228SnnJ+hi3tnYlxeXGFma3F1fISJkpufmIR/f4GDhISDjZuptb3Dw8O5tNKj8vv85szWyMTT0uPdrazFycW9'
    'ta2lnZWKf3Vxbnd9gI67y+Lt+Pz79t3IwL28utPW++3i++isnpqWkZKfm21eXF1dXVxaWVpbW1xcXm57gom8+vz8/fz0xLq5ua+nqK7f/P33+++Y'
    'mamnpqqopJeKeWRaXFtaWltbW15hZG16hYaFouf8/vzpt7uvkYOJhYSp3/j79tCkhI2Zn6aop6imp6SJcF1YW1xfYWJkZWl2hYaGho3L+fz5xbuY'
    'hoR7jJ6bt+n7/vz61oVfaHOWoJycoaenqJt4Y2NhYWFjZWdyhoeGhoaFnuX778KkhYTe2azf+Kzl/f38+feNjeDZu+Xwp5mqqaqqnnthYmJiY2Rt'
    'hoeGhoaGhYi28PPBkpPHdH9z9enC/f395vbjp8VpcoXFqmZ/nKmrq66fYGNiYmNqhoiHhoaGhYWGkMDszouEgYVj8PjE/fz8p/Lu2bRobXDQoWRl'
    'aoOoqay4xXBmZWdsh4iHhoaGhoaGhYSNwNilgXyG/N7c+/iel/vx682UhKPJjWpqbG15pq6uw9x5bGtth4eGhoaGhoaGhYWEhImrrpvf6Kb3/MWE'
    'eL3z6eSqn9nDkm5ubm5vdKeusPXLa2ltiIeGh4aGhYWFhISEhISFhIzB0tPhzrfvsoSr3uzRqMLSvWlvbm5vb3qtr+n7oGpuiYmJiYmHhoWFhISE'
    'g4OEhrOLpOvY8/v70ISEuNPj6KXPzXhxb29vb26Us/T93G1wioqKi4qJh4eGhoWFhYOCq4p+pPHj6bXJz5eQopLa+Nrh0odzc3R1dHGF2f39+Xlx'
    'iouMjIuJiIiHhoaGhpfAj4iBw92oibT99pCEt4mapc7x24V5d3V1dHi8+v39+4d0iYyMjIqKiYiHh4eHhpzv4tbr9sOChrT87oR+m8PJwNHd3M6A'
    'fHt6jdn9/P79+HxziYyLi4qKiYmIh4eHiIB2f39+dXWChn6QhYGFgn+KiYR9w/msf5bQ9v39/f781XZ2iIuLi4uKiomIiIiJioqJhoWFhISEg4eC'
    'hoeIiYaFhIOEhsjd2fn9/f79/f37o3Z4iIqKi4uLiomIiIiIiIiIh4aGhYSFhYWGhoeHh4aHhYqlyPD7/f7+/v7+/fzPfXl5iIiKi4uLiYmIiIeI'
    'h4eHh4aGhYWFhYWGhoeGjKG73fP7/P39/v7+/v3+/eOHfHt5iYmLi6Pg1MW2o5eOioiGhoaGhYWJkqO2xNLp8vr9/f39/f3+/v7+/vz+4I6Cfnt6'
    'i4qKi4vV/Pr8/vv18u3n5+fo6u/3/P7+/v7+/f3+/f39/v7+/v7+/PnQjYKCfnx6jIuKi4qb8/v9/f39/f39/f39/f3+/v7+/v79/f39/f39/f39'
    '/v7987eKhoOAfnx6jIuLi4qIsPr+/f39/f39/f39/f39/f39/f39/f39/f39/f39/vzZmoiGhYJ+fXx7i4mLi4qKjsf8/v39/f39/f39/f39/f39'
    '/f39/f39/f39/P3856+OioiHhoOAfn59jIqKioqKi43O/v39/f3+/v7+/fz8/f38/v7+/f39/f38/Oi0kYyLi4qJhoSCgH59jYuLi4mJi4qNxvv8'
    '/f7+/v7+/f39/v38/fz69vv8/PvgsZCJiomKi4qIhoSDgoB9jo2Mi4mIiYyKh7v1/f3+/f39/Pr28OPW1M/b8Pny0qaIh4eHh4iIiomHiImGg4OB'
    'jY2Ni4uKiYmLioqayMrIv7mzppyircro9/744beUiYiIhoeHh4eHiIiHioyIhYWDjY2Mi4uLiYiIiomIi4eVq77L2ev3/Pz32raXioiLiIiIh4iI'
    'h4aGhoeHiImHhoWFjIyMjIuLioiHiIeIiouQsNf4+e7Xw6eTiIeHiIiJiIiHh4eHh4WEhIWFhoaFhYWFi4uMjIyNjIuJh4aGiIqKiYuSkIqJiYmJ'
    'iIiIiImJiIiIiIaEg4OEhISFhYWFhYWFjIyLjIyNjYuKiIaGh4iJioqLioqKiomJiIeHh4iKiYmHhYSDg4SEhISEg4SEhYWFjIyMi4yLiomKiIeH'
    'h4eHhoaIhoSEhIWEg4KBgoSFhIWEhIOEhIWFhYSEg4SFhYSD'
)
EAIR_TPL_A_B64 = (
    'rK2wvautq6icj46OjYyMi4uJhoaHiIZ9dnV1dnh7fHx7eXl4eHh4eHd3d3d2dXJyrrzr6K+trKunlJCQj46Ni4qHhYWIi4l3Z2NjZGZpb3R4eXp6'
    'eXl4d3Z2dnZ2dHJyr9Lc6LCtra2sm5GQjoyLiYaEg4SGiYh2ZmNiYmFhZGdrcHV5enl4dnV0dHV2dHJyr6/T6bOvr6+vnpCOjIuJh4SChISEhoZ1'
    'ZGRkY2JiZWZmZ2t0eXl3dnV0c3R1dHJxr6/U6bKwsLGxnImJiIeFhIKBgoSEg4iBg5SamJF1cmVlZWpsbXR2dnV0c3N0dHJxsK7T6bKxsLKsjoKD'
    'hYaFhIKBgYqqzOz7/Pz89ryWeWVlZ2tqbXBydXV0c3N0dHJwr67R6LKwsa6dg4GBhoeFhYSEsuv8/Pj38vHflJdxZ2ZlaWtsbm9vcXV0dHNzcnFw'
    'sLC5wbCxraOLg4KChIeGhKfm+eS9qJ6Hc2+Ck25oZ2dnbWxvcG9ub3FzdHNycnFwqa+wsq+toI6FhISDg4SLxPLInIeKjIx7bIWXbGZpZmZrbW9w'
    'cG9vb29ydHRzcnFwk5ygpJ6RhYKDhYWFhIbR2JeGhoaIjI5/ipurysCCaGttbnFxcG9vb29vcXR0c3FwkJCRj4SCg4KDiYWGidO0h4iFhYWIi5Gg'
    'npLy+/vMsNKIcXJxcHBvbm5ub3F0c3FxkZKRiYKCg4SUiIWIx6OEhYeGhIWHi6ipcY7j2/TB4P3VdHNzcXFwb29ubm9ydHJykZKOhYKEhJGRhoal'
    'o4GEgoaHhYSHo7aGcJ3m0/rok/jzd3V0c3Jwb29ubW5wc3NykY+GgoKEi6WIhpKsh4SEgoKFhYWctJSvpaDquLaWkvv1fHV0c3JycXBvbm1ucnVz'
    'kIyCgoOFqZWGh56HhoWFhIOEhpyyj4/5/Pz9+q531fvQeHh2dHRzcnFwb29ucHV0j4eBgoSSsIaHh4qFhoaGhoSBmreThcP7/fz68Prd+92VeIqi'
    'eXV0c3JycHBvcHV1jYSCg4auooaIh4aFh4eGh4Wj6tfJ2fX9/Pv6sKry+ql6eH3dhnV0dHRycXBwcHR2ioGBgZC5jIaHh4aFhoeFhYu2wKS69/39'
    'zpGEeNfFxfXmtXi1r3R0dHRzcnJxcXN2iIGCgaWyh4aHhoaFhYWDhpe0qKbb/Pz9y21wovzdfYe22eTT3nZ0dHRzc3JycnJ2h4KChLWnhoaHhoWD'
    'iYqQ4ebk7/nz5+375nNxw+WdfXhve4nE0JNzdHNzc3NzcnN2h4SDkbmjhYaGhn+jxaKJ4O/46eLm+er86Xdy1PKmmaa9vqfAysKSdHNzc3NzcnJ3'
    'hoWEnbyghYSCi7fExaKeuPPxzeHp+ur7w3d20ON3c37eyHZ6nNWydHJycnJycnJ3h4aFprqhhoWCqJubxbCqfcD029a1166Ib3h4vvR9doHzxG10'
    'geCMc3FycXJycXJ3iYeGrLqjhISEf4Cbw6uNe7/f2PTAx6Fydnl5meqmdZfku5CImM6ucnJycXFxcXJ3ioiHr7mmg4ODhIKbwrCQkMrNy/nYyaJ2'
    'eHl6j6PWcrDEup2ZpsLOcnFycXFwcXF2ioiHsbqshIKBg4KawrChqMvNksji3qJ4d3d5j4iMpbHKqnyJnsnhe3JycXFwcHB1i4iIsru1h4KBgYGa'
    'w7C8rsKsfYzD976CeXZ3j4h1kKTKm3Z1jMbglHNycnFwb29zi4mJsLu6mYWCgYCaxbnCp6ajf4ib7NzhtHpzkZ6Ffp7Gm3R0gMLKvnVzcnFwb29z'
    'jIiKqrq8r4aDgn+ax77BnZ6ehISUxdD73ox6y+eqfI/RqW5wmsKi5nlzcXFwb29zj4iKnrO8uJOCgn6b0sCwi6qthHiVwbLa8oJ3yOXMmpu4x6mr'
    'v5x76ZB0cnJxcHB0kYmIk6m8u7GGgX99n6SHdnNxdndxbGyC4pB1eHtqb3Ftd4OEe4iS0LBycnJxcHF2koqGiZy5u72jgn+fs4p5eXl6eXl2dnV1'
    'oHx0dXNycnJzdHN1kbCRrtVycnJxcXN4k4yHh5Onuru5mYq2j3l4d3d3d3d2dXV2dHVzdHNzcnJyc3mcs5SHjvV7cnJyc3Z6lo+Ih4uTuLi4uKCP'
    'enp4d3V2dnZ2dnV1dHR0dHNzcnNzhaqxjZWvjO2YcnJzdXp8lZKLiIaCpLi4uLipinl5eHZ2dnV1dXV1dHR0c3NzdYOhs6CSpbKxlte3cnJyd3t8'
    'lJSOiIaDiLS4t7e2tKOKe3h3dnZ2dXV0c3NzdIGSpbGupaWxsbC0y8DNc3JyeXx8kpKPi4iFhJG4t7e2tLWzsqKRjIWCg4OGi4+eqrGysbCwr6+w'
    'sb3p9rHnc3J3fX19j5CPi4iGg4CkuLe1tbS0tLW0tLWysLKysrKxsbGxsbGxr7Cy0PX9+qP2enR7fH19jY6OjImGg4GGrL21tLOztLOztLSzsrKx'
    'sbGxsrGxsLKyr8Pp+/39+5/yi3l9fX19i4uMjIiGhIKBgaLQubOzs7Ozs7OzsrGxsrKysbKysa/E5fn9/f387Ivcm3x9fn19ioqKioiIhYF/fHuR'
    '0dG6sbSzs7K0srKysrKysrK91Ov7/f38/fvKf3LEoX1+fn19iImJiYiIhoKAfnp5eq/p59LFvrq3tre6ur/N2e36/f7+/v374JtzcnS3mHx9fXx8'
    'iIiIiYmIh4WCf3t4dnR8rd/5+vj2+Pf3+Pv9/v7+/f39+d6gdnBydXioin19fHx7h4eIiIiIiIaFgXx5dnV1dHWJsMzv+/3+/f38/f39++m4kXdz'
    'cXR2enqSgH19fHt7h4eHiIiIiIaGg357eHd2dXV1dXeFmrTM2uHYybiih3x3d3Zzdnh6e3t7e3x8fHt6hoeHiImJiYeGhYJ/e3l3dXR1dXV1dXR0'
    'dHRxcHBydnh4d3h7fHx7e3t7e3t7e3p5h4eHiImJiIeGhIOBgH16dnV0c3JzdXRzcnJycXJ1d3h7fICAfn18fX18fHt7e3p4h4eHh4aHhoWFhIOC'
    'gYB/fXl1c29vb25tbW1ubXB1e4CCgoJ+fX1+fn59fHt7enh3'
)
EAIR_TPL_G = np.frombuffer(base64.b64decode(EAIR_TPL_G_B64), np.uint8).reshape(48, 48)
EAIR_TPL_A = np.frombuffer(base64.b64decode(EAIR_TPL_A_B64), np.uint8).reshape(48, 48)
_ey, _ex = np.mgrid[0:48, 0:48]
EAIR_MASK = ((((_ex - 23.5) ** 2 + (_ey - 23.5) ** 2) < 22 ** 2) & ~((_ey >= 16) & (_ey <= 34))
             & ~((_ex < 14) & (_ey < 14))).astype(np.uint8)


class MacroBroken(Exception):
    """某一步切人没成功 / 大招没放出来 / HUD 长时间不在, 后面的按键会落在错的人身上 —— 整圈放弃, 交回框架。"""


class Hiyuki(NativeHiyuki):
    """绯雪。绯洛穗(绯雪/洛瑟菈/穗穗)手打宏轴里她是【整条轴的持宏者】, 三个人的 do_perform 都转到 drive()。

    ================= 轴的出处 =================
    群友 2026-10-30 用跑轴记录插件手打的一局(反馈包 20261030_204846_手打_Hiyuki__Lucilla__Suisui, 2560x1440,
    带 NVIDIA 录屏 + 键位浮层)。启动轴 + 一圈循环轴, 两遍手打的同一步误差基本在 ±0.03s 内, 宏表就是这两遍的按键时刻。
    作者编队 1 穗穗 / 2 洛瑟菈 / 3 绯雪; 代码【按角色切人】, 编队随意。闪避用右键。

    ================= 轴的形状 =================
    前段(左键一路按着, 换上来的人接着打; 启动轴从穗穗开始, 循环轴从 R2 演完的绯雪开始):
        启动: 切绯雪 -> 切穗穗 E -> 切绯雪 E -> 切穗穗 E -> 切绯雪 Q R1(见心, 变身预求身)
        循环: 切穗穗 -> 切绯雪 E -> 切穗穗 E -> 切绯雪 Q R1
    R1 演完: 绯雪 E -> 切穗穗 (启动轴 Q) R -> 穗穗大招演出里松左键
    穗穗演完: 切洛瑟菈 长按 E 0.92s(完美焦距, 手打 0.85 卡在变金起点上, 见 LU_HOLD) -> 追光到账后 Q R
    洛瑟菈演完: A A -> 切绯雪 A + 按住左键 + 跳 -> 按着左键切回洛瑟菈 = 追忆断舍离 -> 断舍离演出里松左键
    断舍离演出快完(HUD 消失后 1.34s, 演出里按)切绯雪(变奏) -> 主段(按表: 闪避进居合架势 -> 居合 -> 跳/E 取消,
        3 居合后长按枯霜, 有处决就处决) -> R2(归刃)
    R2 演完 = 一圈结束, 交回框架; 下一次框架调进来就是循环轴。
    循环轴有两版(LOOP_KIND): '6居' = 作者「4R24居循环6居」宏(R1 演完先点两下普攻, 变奏入场一口气 3 刀居合接枯霜,
        后面 居合 -> 处决 -> 居合 x2 -> R2 点按, 见 L6_ 那几张表; 前段照宏跑洛瑟菈没变奏, 用的是 5 居版前段); '5居' = 群友手打那版(LP_ 那几张表)。

    ================= 判据 =================
    几乎全按时间走(作者原话: 很多操作要完全按照宏来, 没什么判定的时间)。只在下面几处读屏:
    - 切人: 按数字键后读队伍 HUD 认上场的人, 后面第一下技能键等它认到再按(切人失败 1.5s 放弃整圈)。
    - 大招/演出: R 连按到 HUD 连续消失 = 出手; 演完 = HUD 回来, 下一段以 HUD 回来那一刻为锚。
    - 【预求身回路条上没有直接的"居合次数"】, 能读到的是三样:
      · 剑身两颗徽记 = 寒意够几次居合(每 100 一颗, 最多显示 2 颗)。寒意满 300 时居合扣 100 还剩 2 颗, 徽记不变 ——
        10-30 23:08 实机前 3 次居合都打出来了(画面上居合 1+4 段伤害), 徽记一直 2 颗。所以徽记只记日志, 不拿来判"居合没出"。
      · 徽记蓝 = 这一颗的居合带武霜(给淬寒), 淡 = 不带; 3 点武霜用完全变淡。
      · 【淬寒满 3 层(= 3 次带武霜的居合) = 回路条右边鼠标小图标换成长按枯霜那个(框架模板 hiyuki_lib_forte)】:
        手打录屏两遍, 第 3 次居合按下后 0.16 / 0.19s 亮(2K 0.82~0.91, 1080 0.81~0.87), 一直亮到枯霜放完, 其他时间 0。
    - 闪避门: 进居合架势的那下闪避, 闪避键图标要是披风(框架模板 hiyuki_right)。到点亮着就照表按; 不亮就补一下普攻
      (攻击动作之后才闪得进架势, 普攻后 ~0.1s 就亮), 亮了再按, 后面整段顺延。手打两遍每一下到点都亮着, 照手打的情况走不会多等。
      【手打的走法跟 Boss 冻没冻住有关】: 居合命中时目标霜冻效应 >=10 层才冻成冰块(霜结)。手打第 1 刀居合就把「虚幻珍宝」冻住,
      绯雪落在冰块上, 第 2 刀后的跳+E 是从地面起的白玉切; 没冻住(23:36 打「燎照之骑」、10-31 01:11/01:12 虚幻珍宝第 1 刀后
      破盾白闪+处决提示)绯雪从第 1 次跳起一直在空中, 跳+E 成了空中落华, 落华之后 1.3s 披风都不亮。
    - 启动轴主段 6 刀进架势后的居合: 比手打早 OP_IAI_LEAD 按(游戏缓存到能出手那帧才出); 第 1 刀的跳挪到闪避后 0.616s
      (手打 0.566s 那下跳会把还没出手的居合顶掉, 补上 3A 之后常常居合没出、Boss 没冻住)。
    - 启动轴的 R2 最好在穗穗的烟岚里放: 烟岚 = 洛瑟菈延奏(绯雪变奏第 30F)给绯雪, 持续 14s 游戏时间(全局时停里不走),
      没有图标。按 R2 时离切人 ~13.9s 游戏时间, 比手打慢就赶不上:
      绯雪晚到的那 0.05~0.07s 是真晚到(烟岚也跟着晚, 不吃烟岚), 照样顺延; 处决前刚看到过提示就到点直接按;
      但三次跳起来之后那下进架势的闪避都要等落地那一下打出来(认定落地后 0.09s)再闪: 闪早了少寒意, 居合 6 后面那下 A
      接不成第 7 刀居合(见 DL_HIT_GAP)。第 1 刀等架势、绯雪晚到顺延的, 在居 2、居 3 后那下跳追回来(手打那两下跳在居合出手后
      0.11~0.13s 才按, 有富余, 见 IAI_J_MIN), 不早于手打; R2 前那次读怪提前读掉, 到点直接按。
    - 空中/地面: E 图标在空中换成落华样式(模板见文件头)。手打每一下进架势的闪避、每一下跳都在地上, 所以
      闪避门之前人在空中就先等落地(跳起/落华后最多 0.8s; 没冻住时站在倒地的宝箱上也算空中, 超过就不等), 主段表上的跳碰上人在空中就不按(不然是二段跳, 越飘越高)。
    - 枯霜门: 到点时 hiyuki_lib_forte 亮着就照表按; 不亮再等一会儿, 还不亮(淬寒没满)且还有徽记才补一刀 闪避->居合, 补完亮了再按。
      6 居循环轴补过刀的, 枯霜按下之后的整段再顺延 L6_KUS_RESCUE_LAG(地面居合接枯霜起手晚, 见那里)。
    - 切回洛瑟菈后按着左键一直打到断舍离(协奏满)才照常演出里切绯雪变奏; 转火那圈前面两下 A 没算上, 会晚 1~2s, 6s 都不出才照切。
    - 切人有 ~1s 冷却(刚换下来的人 1s 内切不回来, 手打最紧的一次 1.03s): 按数字键前先等够, 免得按了被吞再重按。
    - 处决只在怪身上有 F 提示时按(F 也是拾取键)。提示跟着 Boss 满屏跑(冰块被打到屏幕边缘、镜头贴脸时在右上),
      读框用全宽; 主段里顺手每 0.5s 看一眼, 到点那一帧被挡住但 1.2s 内看到过也照按。
      循环轴那格到点读一次就定(不再多等), 没提示(转火刚换上的新怪偏谐是空的)就连点普攻 2.0s(BRK_SPAM), 比处决那段多出的时间后面整段顺延。
    - 转火(矩阵奇藏一轮一只, 打死了刷下一只): 有没有怪 = 锁定图标锁着 / Boss 破盾图标 / 血条 任一读到。宏里离下一个按键
      够远时每 0.3s 顺手读一次; 有怪、锁定图标明确是没锁就按中键锁上。只在三处判"没怪"并等新怪: R2 前(最多 5s, R2 留给
      下一只)、R2 演完(最多 10s, 不然穗穗变奏打空没协奏)、循环轴圈首。前段/主段中途死了不停(按着的键和增益都在走)。

    ================= 改这个文件之前先读 =================
    - 引擎原语(切人记账 / F 监视 / 开关监视 / HUD 看门狗)抄自 skill 自带 Hsin__Rover__Suisui/Hsin.py。
    - 【所有按键走 SendInput】: 按住左键跨切人只有系统级输入做得到; 不在前台退回 PostMessage 只能凑合。
    - 宏表时刻是"相对锚点的秒", 锚点 = 段开头那个可读的事件(按下左键 / R 演完 HUD 回来 / 切人那一下)。
      某一步因为等切人/补居合/处决晚了, 后面整段跟着顺延(self.shift), 保持手打的间隔。
    - 自定义 __init__ 里跟内置版同名的字段会被打回内置值, 要保住就在 reset_state() 里重申。
    """

    # ==================== 宏表 ====================
    # 行 = (相对锚点秒, 动作, 参数)。动作:
    #   A / D / J / E / Q  点按(参数 = 按住秒)。A 左键, D 右键闪避, J 跳
    #   Di                 进居合架势的那下闪避: 先过"闪避键是披风"的门(到点亮着就照表按)
    #   Dl                 跳之后的 Di: DL_CATCHUP 开着 = 前面顺延了就从手打那一刻起边读边等, 落地+披风亮了马上按, 顺延追回来
    #                      (见 land_catchup); 关着 = 跟 Dh 一样
    #   Dh                 跳起来、按着左键落地之后的 Di: 认定落地后至少 DL_HIT_GAP 才按(落地那一下给寒意), 顺延了追到那一刻,
    #                      落地晚了跟着晚(见 land_hit_wait)
    #   Jc                 居合后那下跳: 前面顺延了就往前追, 按点不早于手打那一刻、不早于进架势那下闪避 + IAI_J_MIN;
    #                      没顺延就照表(见 jump_catchup)
    #   Jc3                居 1 后那下跳: 一律按在进架势那下闪避 + IAI_J_MIN_3A, 比表晚就往后推、后面整段顺延(见 jump_floor)
    #   Ad / Au            左键按下 / 松开(按着跨切人)
    #   Ed / Eu            E 按下 / 松开(洛瑟菈长按 E)
    #   sw                 切到这个角色(参数 = 类名)
    #   iai                居合那一下 A(参数 = 按住秒), 记一笔徽记前后(只记日志)
    #   kus                枯霜那一下按住 A(参数 = 按住秒), 先过"枯霜就绪图标亮"的门
    #   F?                 有处决提示才按 F(参数 'seen' = 到点前刚看到过就直接按不再读; 'A' = 到点读一次就定, 没提示就连点普攻 BRK_SPAM 秒)

    # ---- 启动轴前段: 锚点 = 按下左键(穗穗在场) ----
    OP_PRE = (
        (0.000, 'Ad', None),
        (0.654, 'sw', 'Hiyuki'),
        (1.678, 'sw', 'Suisui'),
        (2.546, 'E', 0.06),
        (2.716, 'sw', 'Hiyuki'),
        (2.841, 'E', 0.06),
        (4.313, 'sw', 'Suisui'),
        (4.575, 'E', 0.06),
        (5.501, 'sw', 'Hiyuki'),
        (7.265, 'Q', 0.03),
    )
    OP_R1_AT = 7.327
    # ---- 循环轴前段: 锚点 = 上一圈 R2 演完 HUD 回来(绯雪在场) ----
    LP_PRE = (
        (0.398, 'Ad', None),
        (0.510, 'sw', 'Suisui'),
        (2.074, 'sw', 'Hiyuki'),
        (3.139, 'E', 0.06),
        (4.610, 'sw', 'Suisui'),
        (4.871, 'E', 0.06),
        (5.787, 'sw', 'Hiyuki'),
        (7.552, 'Q', 0.03),
    )
    LP_R1_AT = 7.614
    # 循环轴调进来时上一圈 R2 演完超过这么久(中间断过), 就从现在起算, 马上按下左键
    LP_ANCHOR_FRESH = 1.50

    # ---- R1 演完: 锚点 = HUD 回来。绯雪 E -> 切穗穗 (启动轴 Q) -> 穗穗 R ----
    OP_POST = (
        (0.000, 'E', 0.06),
        (0.061, 'sw', 'Suisui'),
        (0.326, 'Q', 0.06),
    )
    OP_SU_R_AT = 0.417
    LP_POST = (
        (0.000, 'E', 0.06),
        (0.092, 'sw', 'Suisui'),
    )
    LP_SU_R_AT = 0.199
    # 穗穗大招 HUD 消失后这么久松左键(手打两遍 3.30 / 3.33s, 演出 4.3s, 松在演出里)
    SU_LMB_UP = 3.30

    # ---- 洛瑟菈: 锚点 = 穗穗大招演完 HUD 回来 ----
    # 长按 E 到切上来 +1.51s 松手(两遍一样); 追光的印象/协奏松手后 ~0.9s 才到账, R 在那之后。
    # 启动轴手打是 R1 演完那下 E 一直按着没松(6.36s, 跨过穗穗开大), 洛瑟菈上场后 +1.52s 才松; SendInput 的按住没有
    # 键盘自动重复, 换上来的人不一定认, 所以两轮都写成上场后按下(循环轴手打 +0.664s 按、按住 0.85s)。
    # v0.1.8~v0.1.25 按住 1.00s(松手和后面的 Q/R 一起比手打晚 0.15s): 对焦只有松手落在环变金那段才是追光, 金色从 E 出手(冷却
    # 16.0 出现那帧)算 +0.77~0.88 起、+1.08~1.12 退。10-31 09:42 实机(DVR 09.43.52)按键一模一样(+0.664 按、按住
    # 0.85), 游戏里松手都在出手后 0.80~0.82s: 开场 0.82 追光(冷却 15.2)、循环 0.80 补光(冷却 15.3->7.2, R 没解锁);
    # 白色标记出现时刻两次差 2 帧(出手后 0.683 / 0.716s)。
    # 按住 0.92s(v0.1.25 及以前 1.00)。11-09 01:28~01:32 三局 DVR(01.33.51) 6 次长按逐帧量(都按 E 键时刻算, 画面时刻):
    #   E 出手(冷却 16.0 出现) +0.067~0.10 —— 比松手生效的 3 帧延迟多 1~3 帧, 是游戏把 E 缓存到变奏能被打断那一帧才放,
    #   再早按也不会更早出(切人后 +0.664 按已经是边);
    #   外圈三角滑进来先是蓝色, 跟白标记重合那帧变金 = 追光窗口开始: 肉眼第一帧淡金 +0.90~0.91(3 次逐帧), 其余 3 次自动读
    #   +0.93~0.97(读法比肉眼晚 1~3 帧); 窗口宽 ~0.24s(10-08 量的), 到 ~+1.14 才退;
    #   松开 E 到画面上环消失 +0.045~0.06。
    #   -> 按住 >= ~0.86 才落进窗口(手打 0.85 就卡在这条边上, 10-31 一半补光), 0.92 = 松手生效 +0.965~0.98, 比最晚的窗口
    #   起点(按 E 后 ~0.93)留 2 帧。再短就是赌: 补光只给 25 印象, R 不亮, 这一圈直接断。
    # R: 追光的 50 印象在松手生效后 0.91s(第 54F)到账, R 图标那一帧才亮(11-09 实机逐帧); 启动轴松手后 0.931s 按 R 一直第一下
    # 就放, 循环轴手打是 0.96s, 也改成 0.931。Q 跟 R 一起挪(Q -> R 隔 0.046 照手打)
    LU_HOLD = 0.92
    OP_LU = (
        (0.050, 'sw', 'Lucilla'),
        (0.714, 'Ed', None),
        (0.714 + LU_HOLD, 'Eu', None),
        (0.714 + LU_HOLD + 0.885, 'Q', 0.02),
    )
    OP_LU_R_AT = 0.714 + LU_HOLD + 0.931
    LP_LU = (
        (0.050, 'sw', 'Lucilla'),
        (0.714, 'Ed', None),
        (0.714 + LU_HOLD, 'Eu', None),
        (0.714 + LU_HOLD + 0.885, 'Q', 0.02),
    )
    LP_LU_R_AT = 0.714 + LU_HOLD + 0.931

    # ---- 洛瑟菈大招演完: 锚点 = HUD 回来 ----
    # A A -> 切绯雪 A + 按住 + 跳 -> 按着切回洛瑟菈(追忆里按住 = 断舍离)
    LUR = (
        (0.000, 'A', 0.06),
        (0.371, 'A', 0.06),
        (0.461, 'sw', 'Hiyuki'),
        (0.588, 'A', 0.105),
        (1.052, 'Ad', None),
        (1.408, 'J', 0.06),
        (1.578, 'sw', 'Lucilla'),
    )
    LUR_SW_BACK = 1.578
    # 切回洛瑟菈后按着左键一直等到断舍离(HUD 消失)才往下走, 这么久还没有才松手照切(手打 2.14 / 2.15s 消失)。
    # 转火那圈洛瑟菈 R 演完那两下 A 正赶上 Boss 死、换下一轮, 没算数: 切回来按住是从溯念留形第 1 段打起, 1->2->3 段打完相片
    # 才断舍离, 比正常圈晚 1~2s(断舍离那 +20 协奏才满, 没它绯雪没有变奏)。11-01 20:56 实机(205407 L2)旧值 3.2s 按住 3.15s
    # 就松手切了绯雪 -> 协奏没满、绯雪没变奏, 这一圈后面全乱
    DSL_GONE_CAP = 6.00
    DSL_LMB_UP = 1.18            # 断舍离 HUD 消失后这么久松左键(手打 1.17 / 1.20s, 演出 1.33~1.39s)
    # 断舍离 HUD 消失后这么久按切绯雪 = 主段锚点。手打两遍 1.335 / 1.350s, 比 HUD 回来早 ~0.05s(演出里按, 游戏缓存);
    # 10-30 实机等读到 HUD 回来才按是 1.386 / 1.396s, 主段每个键相对绯雪入场都晚 0.05s(图标亮起时刻对表也是这样)
    DSL_SW_AT = 1.34
    # 演出里按的切人游戏有时不收: 10-31 00:51/00:54 实机 HUD 回来(按键后 0.06s)场上还是洛瑟菈, 0.12s 后重按才切上,
    # 绯雪晚到 0.15s, 第一下 A 照表按, 离变奏只有 0.85s(手打/00:39 是 1.0s)被变奏吞了 -> 没有第 3 段冰环, 寒意少一截。
    # 主段改从绯雪认到上场那一刻往前推这么多起算(手打 0.04~0.09s, 00:39 实机 0.06s)
    # 11-01 实机读到上场是切人后 0.11~0.13s(每局 HUD 回来那帧场上还是洛瑟菈, +0.05s 又按了一下), 每局顺延 0.05~0.07。
    # v0.1.11 当成读屏晚、启动轴改 0.13 不顺延: 15:54/15:56 第一下 A 落在切人后 1.075s(跟手打一样)被变奏吞了, 没 3A -> 是真晚到。
    # 烟岚是变奏第 30F 给的, 跟着一起晚, 这截顺延不吃烟岚
    MAIN_ARRIVE_OFS = 0.06
    # 表上 'iaiw' 那刀居合: 离进架势的闪避不到这么久就等够再按, 晚了多少后面整段顺延(居合->跳 的间隔照手打)。
    # 架势第 30F(0.50s)才接得了居合(帧表 居合架势-顿帧 派生帧 30)。启动轴第 1 刀手打是闪避后 0.462s 按(靠缓存)、0.566s 跳;
    # 10-31~11-01 实机 15 局这三下分毫不差, 没 3A 的 2 局都冻住, 有 3A 的 12 局只冻住 2 局。没冻住的那刀居合根本没出
    # (剑身寒意不扣、没有 15 万那一刀、霜冻层数停在 13、枯霜晚一刀亮) = 跳把还没出手的居合顶掉了。同一张表里跳落在
    # 闪避后 0.616~0.646s 的居合 2/3/4 都正常(居 4 也在 3A 之后, 闪避后 0.529s 按)。v0.1.9~v0.1.14 用 0.60(跳在 ~0.70s)。
    # 11-01 16:38 录屏: 第 1 下和第 4 下进架势的闪避, 闪避键都在按下后 0.034s 换成跑人图标(进了架势), 一样快 -> 照居 4
    # 0.53 按, 省 0.07s 给 R2(跳见 IAI_J_MIN_3A)。11-01 17:26 录屏: 0.53 按和 16:38 0.60 按, 居合都在闪避后 0.60s 出手(架势
    # 没到那帧按早了只是缓存), 早按不费时间, 改成跟其他刀一样照表(手打 0.462 再早 OP_IAI_LEAD), 只在闪避晚了时兜底
    IAI_DA_MIN = 0.42
    # 表上 'Jc'/'Jc3'(启动轴居 1/2/3 后那下跳): 居合第 1F 扣寒意、伤害脱手, 出手后两帧就能跳。11-01 16:38 录屏剑身寒意亮头
    # (录屏比游戏晚 ~1 帧): 落地后进架势的居 2/3/6 在闪避后 0.517~0.534s 出手, 3A 后进架势的居 4 在 0.584s 出手(0.529 就按了也得等;
    # 手打教学录像一样是居 4 比居 6 晚 0.08), 居 4 出手后 0.032s 跳一直正常。手打居 1 是 0.566s 跳 = 还没出手就跳, 我们这样按
    # 12 局只成 2 局, 所以居 1 照居 4 的 0.616。手打居 2/3 是闪避后 0.646s 跳 = 出手后 0.11~0.13s, 多出来的这截拿来追前面顺延的
    # (第 1 刀等架势、绯雪晚到), 不早于手打。17:04(v0.1.15) 追之前 R2 比手打晚 0.082s
    IAI_J_MIN = 0.58             # 落地后进架势那刀(居 2/3): 出手后 >=0.046s 才跳
    IAI_J_MIN_3A = 0.616         # 3A 后进架势那刀(居 1): 跟居 4 一样
    # v0.1.16 居 1 先等到 0.53 才按, 有顺延, 跳才挪得到 0.616; v0.1.17 居 1 照表不顺延, 跳落回手打 0.566 -> 20:32/20:33 两局居 1
    # 都没出, 枯霜门补一刀, 枯霜晚 1.6s。所以居 1 的跳改成一律不早于闪避 + 0.616('Jc3', jump_floor)
    # 启动轴主段闪避进架势后那 6 刀居合比手打早这么多按(循环轴不动)。11-01 视频作者看录屏觉得架势里等久了, 让早点按。
    # 17:26 我们除居 1(0.53 vs 0.463)外跟手打同一时刻按; 实测出手那帧由架势定(落地后进的 0.52~0.55s、3A 后的 0.60s),
    # 按早了游戏缓存。手打居 1 在出手前 0.14s 按都接得上, 这里早 0.04 还在这个范围里
    OP_IAI_LEAD = 0.04
    # 居 6 后那下 A(寒意还 >=100 时接成连续居合 = 第 7 刀)。关掉就不按, 拿来对照 R2 吃没吃到烟岚
    OP_IAI7 = True
    OP_IAI7_AT = 17.620

    # ---- 主段: 锚点 = 断舍离演完按下切绯雪那一刻(变奏入场) ----
    # 启动轴: 居合 x3 -> 枯霜 -> 居合 x2 -> 处决 -> 居合 x1 -> R2 点按
    OP_MAIN = (
        (1.075, 'A', 0.15),
        (1.583, 'A', 0.07),
        (1.924, 'Di', 0.06),
        (2.386, 'iaiw', 0.67),
        (2.490, 'Jc3', 0.06),
        (3.295, 'Dl', 0.06),
        (3.762, 'iai', 0.09),
        (3.941, 'Jc', 0.06),
        (4.033, 'E', 0.06),
        (4.562, 'Di', 0.06),
        (5.029, 'iai', 0.09),
        (5.208, 'Jc', 0.06),
        (5.301, 'E', 0.06),
        (5.861, 'kus', 1.45),
        (5.949, 'J', 0.06),
        (8.707, 'A', 0.08),
        (9.049, 'A', 0.09),
        (9.516, 'A', 0.07),
        (9.949, 'Di', 0.09),
        (10.478, 'iai', 0.66),
        (10.565, 'J', 0.06),
        (11.375, 'Dh', 0.09),
        (11.870, 'iai', 0.07),
        (12.052, 'F?', 'seen'),
        (13.727, 'A', 0.07),
        (14.071, 'D', 0.06),
        (14.533, 'A', 0.09),
        (14.808, 'A', 0.09),
        (15.008, 'A', 0.06),
        (15.453, 'A', 0.97),
        (15.853, 'J', 0.06),
        (16.629, 'Dh', 0.06),
        (17.078, 'iai', 0.07),
        (17.620, 'A', 0.09),
    )
    OP_R2_AT = 17.864
    OP_R2_HOLD = 0.06
    # 循环轴: 居合 x2 -> 处决 -> 居合 x1 -> 枯霜 -> 居合 x1 -> R2 一直按住到游戏自己放(见 fire_r2)
    LP_MAIN = (
        (1.075, 'A', 0.15),
        (1.616, 'A', 0.97),
        (2.013, 'J', 0.06),
        (2.821, 'Di', 0.06),
        (3.266, 'iai', 0.66),
        (3.357, 'J', 0.06),
        (4.167, 'Di', 0.07),
        (4.649, 'iai', 0.10),
        (4.782, 'J', 0.06),
        (4.817, 'D', 0.09),
        (5.300, 'A', 0.07),
        (5.609, 'A', 0.09),
        (6.070, 'A', 0.09),
        (6.471, 'F?', 'A'),
        (8.208, 'A', 0.06),
        (8.563, 'Di', 0.06),
        (9.012, 'iai', 0.09),
        (9.193, 'J', 0.06),
        (9.287, 'E', 0.06),
        (9.800, 'kus', 1.46),
        (9.890, 'J', 0.06),
        (12.716, 'A', 0.09),
        (13.074, 'A', 0.07),
        (13.539, 'A', 0.09),
        (14.016, 'Di', 0.06),
        (14.466, 'iai', 0.09),
        (15.087, 'A', 0.09),
    )
    LP_R2_AT = 15.439
    LP_R2_HOLD = None
    FIN_R2_HOLD = 1.00           # 上一圈断在预求身(hy_finish_form)放 R2 只为退出形态, 照旧按住 1s

    # ==================== 6 居版循环轴(LOOP_KIND = '6居') ====================
    # 出处: 作者的「4R24居循环6居」G HUB 宏(录制延时, 11-09 截图)。编队 1 绯雪 / 2 洛瑟菈 / 3 穗穗, 代码按角色切人。
    # 跟 5 居版(上面 LP_, 群友手打, v0.1.22 实机过)的区别:
    #   前段: 结构一样, 宏的间隔短一点(R1 早 0.31s)。照宏跑不行, 用的是 5 居版的 LP_PRE / LP_R1_AT(见下)
    #   R1 演完: 先松左键, 点两下普攻(预求身攒寒意)再绯雪 E -> 切穗穗 R(5 居版是按着左键, HUD 回来马上 E)
    #   主段: 变奏入场 A A -> 闪避进架势 -> 居合 + 两下连续居合 -> 跳 E -> 枯霜(5 居版居合 2 刀 + 处决 + 1 刀后才枯霜)
    #         -> A A 按住A A A -> 闪避 居合 -> 处决 -> 按住A A A -> 闪避 居合 + 连续居合 -> R2 点按(5 居版按住到游戏自己放)
    # 洛瑟菈 / 断舍离跟 5 居版共用(宏里那几段跟手打差 <0.1s; 长按 E 用 LU_HOLD 0.92s, 宏的 0.86 是 10-31 实机一半补光的那个值)。
    # 没写 L6_ 的表回落到 LP_ 那份。
    LOOP_KIND = '6居'            # '6居' / '5居'
    # 前段不写 L6_PRE / L6_R1_AT, 回落到 5 居版 LP_PRE / LP_R1_AT。
    # 宏的前段(按下左键对到 0.398: 切穗穗 0.598 / 切绯雪 2.048 / E 2.948 / 切穗穗 4.398 / E 4.648 / 切绯雪 5.548 / Q 7.298 /
    # R1 7.308)穗穗第一次只站 1.45s(5 居版 1.56s)。11-09 00:18 / 00:21 两局实机照宏跑, 穗穗开完大切洛瑟菈时
    # 洛瑟菈 HUD 相片条是空的(5 居版 11-01 21:31 那局上场 0.7s 就有 2 张 = 变奏 +100), R 一直不亮, 两局都卡死在这。
    # 穗穗第一次上场那下重击收尾的爆发离切走只剩 0.1~0.2s(作者视频是 0.4s), 协奏差这一下就没变奏
    # R1 演完: 锚点 = R1 HUD 回来。宏里 R 松开后 4.1s 松左键, 按 R1 出手 -> HUD 回来 ~4.10s 折算(按下普攻 0.75 / 1.24)。
    # 左键是按着跨过 R1 的, HUD 回来那刻游戏接成预求重击(长按 24F -> 拔刀, +0.57s 那刀, 21 寒意), 派生帧在 HUD 回来
    # +0.73~0.77s(帧表 预求重击-伤害 派生 20F)。11-09 00:52 实机(DVR 00.54.07, 录屏 = 记录 +364.64, 作者视频 = 录屏 -412.0)
    # 照宏 +0.75 按的第一下普攻卡在派生帧边上被吞(绯雪站了 0.5s 不动; 作者那下 ~+0.80 按、+1.0 出刀), 第二下成了 A1,
    # 少 A1+A2 一截寒意。录屏刀鞘寒意条: 主段变奏入场 我们 ~266 / 作者 ~287, 居 1 前 我们 ~281 / 作者顶到 300;
    # 居 1/居 2 各扣 100 后我们剩 ~81(徽记 0、刀身全粉), 居 3 按下去没出(绯雪站着), 作者居 2 后还有 1 颗徽记、居 3 照出。
    # 居 3 没出 -> 淬寒 2 层 -> 枯霜门补刀, 后面整段晚 1.6s。整段往后挪 0.10s, 两下普攻都落在派生帧之后
    L6_POST = (
        (0.050, 'Au', None),
        (0.850, 'A', 0.07),
        (1.340, 'A', 0.07),
        (1.610, 'E', 0.05),
        (1.710, 'sw', 'Suisui'),
    )
    L6_SU_R_AT = 1.770
    # 主段: 锚点 = 断舍离演完按下切绯雪那一刻(同 LP_MAIN)
    L6_MAIN = (
        (1.050, 'A', 0.15),
        (1.570, 'A', 0.10),
        (2.030, 'Di', 0.05),
        (2.460, 'iai', 0.07),
        (3.080, 'iaicA', 0.07),        # 居合后接着按 A = 连续居合; 徽记灭着先等亮回来, 回不来就连点普攻补寒意(见 IAIC_RESCUE_CAP)
        (3.730, 'iaicA', 0.07),
        (3.900, 'Jk', 0.05),           # 等第 3 刀出手再跳(见 IAIK_RETAP)
        (3.950, 'E', 0.05),
        (4.440, 'kus', 1.43),
        (4.520, 'J', 0.05),
        (7.270, 'A', 0.07),
        (7.640, 'A', 0.07),
        (8.130, 'Ah', 0.75),           # 等 A2 收招再按住(见 HOLDK_OFS); 宏按住 0.64, 先出 A3 再转重击余量太小, 拉到 0.75
        (9.470, 'A', 0.07),
        (9.900, 'A', 0.07),
        (10.290, 'Di', 0.05),
        (10.720, 'iai', 0.05),
        (10.840, 'F?', 'A'),
        (12.590, 'A', 0.64),
        (13.930, 'A', 0.07),
        (14.360, 'A', 0.07),
        (14.750, 'Di', 0.05),
        (15.180, 'iai', 0.07),
        (15.800, 'iaic', 0.07),        # 连续居合 = 第 6 刀: 徽记灭着先等它亮回来再按(见 IAIC_CAP)
    )
    L6_R2_AT = 15.970            # None = 6 居表没写完, 照 5 居版跑
    # 主段整张表(含 R2)再往后挪这么多。宏的第一下普攻在切人后 1.05s(5 居版/启动轴 1.075), 我们从认到绯雪上场 -0.06 起算,
    # 等于绯雪上场后 0.99s 按 -> 11-09 01:28~01:32 三局循环这一下全被变奏吞(录屏: 绯雪变奏完站着不动 ~0.9s, 第二下普攻
    # 才出刀), 第二下成了 A1, 少了带冰环的预求 A3(32 寒意) -> 居 2 后徽记 0、居 3 没出、枯霜门补一刀, 后面整段晚 1.6s。
    # 同一局启动轴那下在上场后 1.013s 按就出了; 帧表 预求QTE-伤害 派生帧 60F = 1.00s, 正好卡在两者中间。
    # 挪 0.05 = 上场后 1.04s(认人只会比真上场晚, 不会早)。只挪整张, 不单挪第一下: 两下普攻隔 0.52(A2 派生 32F)、
    # A3 到闪避 0.46(A3 第 4 段判定在 28F) 都是贴着帧表的, 压短会少伤害/寒意
    L6_MAIN_OFS = 0.05
    LP_MAIN_OFS = 0.0
    # 宏是点按 0.10s, 照宏(用户 11-09: 不蓄力, 蓄力就打断不了最后一刀居合的动作)。点按只有攒满 3 点【锻雪·归刃】才全耗,
    # 不满 3 点一点不耗(倍率 基础 198.81%+795.24%, 每点 +795.24%), 所以循环前两圈 R2 是基础倍率、第 3 圈吃满 3 点。
    # 11-09 03:15 实机: 启动 R2 大跳 2,041,470(满 3 点) vs 循环 L1 同一只虚幻珍宝 600,432, 比值 3.400 正好是点数差,
    # 烟岚在(绯雪入场->R2 冻结倒计时 启动 14.15s / L1 13.04s)
    L6_R2_HOLD = 0.10
    # R2 等最后一刀(居 6, 连续居合)扣了寒意再按, 不早于表(见 r2_wait_cut)。居合扣寒意那一下就算出伤, 动作被 R2 盖住没关系。
    # 11-09 02:48 实机(记录 024841, DVR 02.50.32 = 记录 +370.07) 循环 L1: 居 6 那下 A 后 0.17s 按 R(宏的间隔), R2 演出
    # 0.31s 后盖住 HUD, 这期间寒意条一直没动 = 居 6 被 R2 顶掉。同一局启动轴第 7 刀: A 后 0.231s 寒意条扣掉(剑格右边刀身变暗),
    # R 在 0.244s(手打的间隔)才按, 正好赶上。连续居合要等上一刀出手后 ~0.8s 才出手(居 5 是 A A 后闪避进的架势, 出手比落地后进的晚)
    L6_R2_CUT = True
    LP_R2_CUT = False
    # 寒意条剑格右边那一截刀身(4K hcenter, x1,y1,x2,y2): 有寒意是白的, 扣到底变成刀身的暗蓝。2K / 缩到 1080 读数一样:
    # 扣之前 白 0.79~0.88 暗 0; 扣掉那帧 白 0 暗 0.62; HUD 被演出盖住 白 0 暗 0.01
    CUT_BOX = (1773, 2004, 1818, 2019)
    CUT_WHITE = 0.50             # 按下最后一刀时白占比到这个才判得出"扣掉"
    CUT_DARK = 0.35              # 暗蓝占比到这个、白不超过 CUT_GONE_WHITE = 扣掉了
    CUT_GONE_WHITE = 0.20
    R2CUT_CAP = 0.60             # 最后一刀按下后最多等这么久, 还没扣就照按
    # ---- 6 居循环最后一刀(表上 'iaic'): 徽记亮着才按 ----
    # 11-09 03:01 实机(记录 030118, DVR 03.03.03 = 记录 +375.44) 循环 L1: 居 5 按下 +0.38 扣寒意、徽记 1->0, 居 6 那下 A
    # 照宏在 +0.62 按(徽记灭着), 游戏不收也不缓存, 绯雪站到 R2(v0.1.29 等扣寒意干等了 0.6s)。徽记在居 5 按下 +0.79 才亮回来
    # (居 5 命中后寒意回到 100 以上; 同一圈居 4 后也是 +0.80 亮回来, 02:48 那局 +0.85)。启动轴第 7 刀能出, 是因为居 6
    # 扣完还剩 1 颗徽记(2->1), 那下 A 按的时候徽记亮着。
    # 所以最后一刀: 到点上一刀已经扣过、徽记还亮着就照表按; 灭着就等亮回来马上按; 上一刀还没看到扣就先等它扣
    # (IAIC_DROP_WAIT 内没扣也照按)。等到上一刀按下 + IAIC_CAP 还不亮 = 寒意不够出不了, 这一刀不按、R2 直接按。
    # 按下后 R2 等徽记少一颗(扣了寒意)再按; IAIC_RETAP 还没扣补按一下普攻
    # 变奏入场那串的居 2 / 居 3 也走这个(11-09 03:15 实机 记录 031524): L1(同一只虚幻珍宝) 居 1 按下 +0.72 命中回寒意、
    # 徽记 1->2, 居 2 扣完还剩 1 颗, 居 3 照表出; L2/L3 转火打燎照之骑, 寒意条从绯雪第二次上场起就比 L1 少 ~18
    # (按键时刻完全一样), 居 1 回完没过 200, 居 2 扣完徽记 0, 居 3 照表(居 2 后 0.65)按下去不收, 跳又因为没徽记直接跳,
    # 枯霜门补刀晚 1.6s。居合命中回寒意在按下后 ~0.72~0.85s 到(居 4/居 5 后也是), 等它亮回来再按第 3 刀
    IAIC_CAP = 1.10
    # 变奏入场那串的居 2/居 3(表上 'iaicA'): 上一刀扣过、徽记没亮(转火后有几下没打到新怪, 寒意差一点)就每 BRK_SPAM_EVERY
    # 点一下普攻补寒意(居合动作里点的不算, 收招就出普攻, 命中回寒意), 徽记亮了再补出这一刀居合, 然后照表接跳 E -> 枯霜。
    # 用户 11-09: 补刀要接上正常流程, 不能拿 E 给的寒意去补(v0.1.34 补刀没出, 跳 E 后枯霜门又补一刀 = 用掉 E 的寒意)。
    #  - 离上一刀按下 IAIC_TAP_WINDOW 内亮: 按 A 就是连续居合(03:38 实机 L2 居 4 后 +0.98 亮、+1.115 按 A 接上了);
    #    按下 IAIC_TAP_CONFIRM 内没扣寒意、徽记也还亮着 = 没接上(成了普攻), 改走闪避。
    #  - 亮得更晚: 按 A 只是普攻(04:33 实机 L2 居 2 后 +1.56 亮, 按 A 和补按都打成普攻: 冻伤层数 13 没扣、徽记一直 1、
    #    枯霜不亮), 直接闪避进架势(同枯霜门补刀: dodge_gate -> 闪避 -> KUS_D_TO_A -> 居合)。
    #  - 上一刀按下 + IAIC_RESCUE_CAP 还不亮就不补(跳 E 后枯霜门补刀); 居 2 没补上居 3 直接不按。
    IAIC_RESCUE_CAP = 2.00
    IAIC_TAP_WINDOW = 1.20
    IAIC_TAP_CONFIRM = 0.65         # 扣寒意认定在按下后 0.26~0.49s(实机几刀), 留余量
    IAIC_STANCE_CONFIRM = 0.60
    IAIC_DROP_WAIT = 0.70
    IAIC_RETAP = 0.30
    R2CUT_FALLBACK = 0.30        # 按下时那截不是白的(判不出来): 最后一刀按下后这么久再按
    # 处决到表上下一下 A 隔 1.75s(5 居版 1.74s), 没处决时连点普攻超出这截才顺延。
    # 【这一格紧跟在居 4 后面 0.12s】(5 居版在三下普攻后面): 居合后 120F 内按普攻会接成连续居合(帧表 居合-地面),
    # 所以没处决时连点的普攻多半先多出一刀居合、提前用掉 100 寒意, 后面居 6 可能不出(总刀数应该不变)。没处理, 等实机看
    L6_BRK_SPAM_FIT = 1.67
    # 6 居循环没处决提示(转火换上的新怪偏斜值不够)就不补救(用户 11-09: 容错时间很少, 少一个居合就少一个, 直接 R2):
    # 不连点; 等居 4 命中回寒意、徽记亮回来(居 4 按下 + IAIC_CAP 内)按一下 A 接连续居合, 扣了寒意就放 R2, 主段后面的不打了;
    # 徽记不亮直接 R2。R 不亮(能量不够)才照旧连点。
    # 11-09 03:38 实机(记录 033820) L2: 没提示 -> 连点 2.0s(顺延 0.39), 其中一下在居 4 徽记亮回来(按下 +0.98)后接成连续居合,
    # 之后闪避门没徽记、居 5 没出、居 6 等徽记 0.49s 不按, R2 比有处决的圈晚 0.5s。那一刻 R 是亮的。
    L6_BRK_TO_R2 = True
    LP_BRK_TO_R2 = False
    # 枯霜门补过刀时, 枯霜按下之后的整段(跳 / 松左键 / 后面的普攻)再顺延这么久。表上枯霜接在 跳 E(落华)落地上, 按下就起手;
    # 补刀那条路是从地面居合接(补居合后 0.83s 按), 居合动作没收完枯霜起不了手。11-09 00:52 实机: 枯霜大特写起手
    # 按下 +2.21s(作者 +1.86s), 晚 0.35s; 表上紧跟枯霜按下 +0.08s 的那下跳落在起手之前、人跳起来了, 枯霜收尾那下普攻
    # 按在收招里被吞, A A 重击 整串错一格。5 居版 / 启动轴不顺延
    L6_KUS_RESCUE_LAG = 0.35
    LP_KUS_RESCUE_LAG = 0.0

    # ==================== 引擎节奏 ====================
    KEY_TAP = 0.06               # 补按/连按时每一下按住多久(手打 0.06)
    SW_TAP = 0.03                # 数字键按住多久
    READ_MIN_GAP = 0.045         # 离下一个按键还有这么久才读一帧(读一帧 + 认 HUD 要 10~30ms)
    POLL_MIN_INTERVAL = 0.012    # 两次读屏至少隔这么久(next_frame 不等新帧, 不限的话空转)
    LATE_WARN = 0.05             # 按键比表晚这么多就记进时间轴

    # ---- 切人 ----
    # 切人冷却: 刚被换下来的人这么久之内切不回来(从认到换人那一刻算)。手打最紧的一次是换下 1.03s 后切回;
    # 10-30 23:08 实机启动轴先把入口的绯雪换成穗穗, 前段头 3 次切人全撞冷却, 每次重按晚 0.36s
    SW_COOLDOWN = 0.95
    SW_RESEND = 0.12             # 按了数字键这么久还没认到就再按一次(重按没有副作用)
    SW_CAP = 1.50                # 这么久都没切过去 = 被游戏拒绝(硬直/演出里), 整圈放弃
    SW_BLOCK_AFTER = 0.50        # 切人没认到时, 这么久之后的任何按键都先等它(别打在上一个人身上)
    UNTRUSTED_CONFIRM = 3

    # ---- 大招 / 演出 ----
    LIB_GONE_CONFIRM = 3         # HUD 连续这么多帧不在 = 演出真的开始了
    HUD_LOST_ABORT = 9.00        # 队伍 HUD 连续这么久没见过 = 关卡结束/过场/窗口没了(最长的 R2 演出 5.8s)
    DRIVE_HUD_CAP = 1.50         # 框架调进来时 HUD 这么久都不在就什么都不做(结算画面上框架还会一直调进来)
    HUD_UP_CAP = 1.00
    # 绯雪 R1(见心): 一直按到 HUD 消失。没变身整圈都不成立, 放不出来就放弃这一圈
    R1_EVERY = 0.30
    R1_CAP = 2.50
    R1_MIN_GONE = 2.50           # R1 演出 4.0~4.05s; 这之前 HUD 闪回来不认
    R1_ANIM_CAP = 7.00
    # 穗穗 R / 洛瑟菈 R: 切上来认到人之后连按到 HUD 消失
    SU_R_EVERY = 0.10
    SU_R_CAP = 2.00
    SU_R_MIN_GONE = 2.50         # 穗穗大招演出 4.30~4.32s
    SU_R_ANIM_CAP = 7.00
    LU_R_EVERY = 0.10
    LU_R_CAP = 2.00
    LU_R_MIN_GONE = 3.00         # 洛瑟菈大招演出 4.44~4.47s
    LU_R_ANIM_CAP = 7.00
    # 绯雪 R2(归刃): 点按版按完 0.16s 进演出; 按住版松手后 ~0.9s 才进演出。没进就再来一次(不在按住途中重按)
    # 循环轴一直按住(hold=None): 帧表按住 60/114/168F 各扣 1 点归刃, 扣到 0 点就在派生帧(96/150/210F)自己放 ->
    # 有几点吃几点, 1 点时 ~1.6s 进演出(跟原来按 1s 松手一样快)。按着看到 HUD 消失才松, R2_AUTO_CAP 还没放就松手照旧等
    R2_TRIES = 2
    R2_TAP_WINDOW = 0.80
    R2_PRECHECK = 0.08           # 启动轴: 按 R2 前这么久先读一次有没有怪, 到点不用再现读
    R2_HOLD_WINDOW = 1.60
    R2_AUTO_CAP = 4.00           # 3 点 210F = 3.5s 自己放, 多给 0.5s
    R2_MIN_GONE = 3.50           # R2 演出 5.6~5.8s
    R2_ANIM_CAP = 9.00

    # ---- 处决 ----
    F_HOLD = 0.06
    F_LATE = 0.50                # 到点没提示就再看这么久, 还没有就不按(晚按的那一截整段顺延; 不按不顺延, 后面隔着 1.6s+)
    BREAK_CENTER_MIN = 0.90
    # 提示挂在 Boss 身上, Boss 在哪提示在哪。10-31 00:39 实机: 提示从主段 5s 起一直在(镜头贴脸时在右上 y≈0.15),
    # 到点那 0.3s 冰块被打到屏幕右边缘(x≈0.97), 出了框架那个 x0.2~0.75/y0.2~0.8 的框, 读数 0.55 -> 跳过处决, 后面整段错位。
    # 读框放到全宽(避开顶上 Boss 条、底下按键提示字母); 没提示时全屏最高 0.79
    BREAK_BOX = (0.0, 0.08, 1.0, 0.92)
    BREAK_PEEK_GAP = 0.50        # 绯雪主段里顺手看提示的间隔(全宽读一次 ~50ms)
    BREAK_PEEK_BUDGET = 0.16     # 离下一个按键至少还有这么久才顺手看
    BREAK_RECENT = 1.20          # 到点这一帧没读到(特效挡住/飞出画面), 但这么久之内看到过 -> 照按
    # 循环轴('F?' 'A' 那格)用这个: 处决提示从主段开头就在(L1 居 4 前 ~9s 起一直读得到), 只是被居合/闪避特效挡得一闪一闪。
    # 11-09 04:10 / 04:31 两局 L1 到点前最后一次读到是 1.20 / 1.70s 前(超了 BREAK_RECENT)就当没提示、没处决。
    # 中间转火没怪过(换了只怪)就不算
    BREAK_RECENT_A = 3.00
    # 表上写了 'seen' 的处决(启动轴): 到点前 BREAK_RECENT 内看到过就直接按, 不先读这一帧。11-01 12:45/12:51 实机两局都是
    # 0.4s 前看到过, 到点先全宽读一次再按 = 晚 0.08/0.09s, 整段顺延, 烟岚撑不到 R2
    BRK_SEEN_QUICK = True
    # 表上写了 'A' 的处决(循环轴): 转火刚换上的新怪偏谐是空的, 等也等不出提示(11-01 12:12 实机: Boss 在居合2 那下死了,
    # 0.4s 后刷出来的新怪出不了处决)。到点读一次就定(LP_F_LATE), 没有就把处决那 1.6s 改成连点普攻, 表上后面照原时刻接着打。
    # 11-01 20:57 实机 L2 转火没提示: 到点再看 0.5s + 空着到下一下 A = 绯雪愣了 1.7s。
    # v0.1.10 的「A3 后空等 1.15s 补一下 A4」12:45/12:51 实机看着也是愣在原地, 已删
    # 11-01 21:2x 实机(212610 L2) 连点 1.57s(13 下)居 4 后徽记 1->0(有处决的圈 1->1), 表上最后那下 A 没成第 5 刀居合 -> 改 2.0s
    LP_F_LATE = 0.0
    BRK_SPAM = 2.00              # 连点多久
    LP_BRK_SPAM_FIT = 1.66       # 处决到表上下一下 A 隔 1.74s: 连点到这之内不顺延, 超出的那截后面整段顺延(R2 跟着晚)
    BRK_SPAM_EVERY = 0.12        # 连点间隔(每下按住 KEY_TAP)

    # ---- 居合徽记(4K 坐标, hcenter): 只记日志, 不拿来判居合出没出(寒意满时居合不扣徽记) ----
    IAI_SLOTS = ((1687, 1998), (1905, 2004))   # 左 / 右两颗徽记中心
    IAI_R = 33                   # 徽记半径(4K)
    IAI_S = 12                   # 中心上下左右的搜索余量(4K)
    IAI_PRESENT = 0.75           # 模板分数到这个算有(有 0.86~1.00 / 没有 <= 0.41 / 渐隐 0.5~0.8)
    IAI_BLUE = 0.30              # 徽记圆内高饱和度占比到这个算蓝(带武霜); 只在"有"的时候有意义
    IAI_CONFIRM = 2              # 连读这么多帧一样才认颗数变了
    IAI_WATCH = 0.75             # 居合按下后这么久记一笔徽记前后(手打 0.23~0.35s 变)
    # ---- 枯霜就绪(淬寒 3 层) = 框架模板 hiyuki_lib_forte ----
    KUS_TPL = 'hiyuki_lib_forte'
    KUS_LIT = 0.70               # 框架原版也是 0.7; 亮 0.81~0.91(2K/1080), 不亮 0.00
    KUS_CONFIRM = 2              # 连读这么多帧才认亮/灭
    # ---- 闪避门: 闪避键图标是披风(框架模板 hiyuki_right) = 这下闪避能进居合架势 ----
    # 披风只在 普攻/白玉切/落华/居合/变奏 之后一小段时间亮(寒意 >=100)。手打 10 次进架势的闪避到点时全亮着(0.94~0.97),
    # 不进架势的两下普通闪避都不亮。10-30 23:36 实机第 3 刀: 绯雪一直没落地, 跳+E 成了空中落华, 下落期间不亮,
    # 表上那下闪避成了普通闪避、居合变普攻, 淬寒只到 2 层, 枯霜门补了 2 刀(+2.9s)
    # 10-31 01:11/01:12 实机(v0.1.5)两局一样: 第 1 刀没冻住, 第 2 刀后 J+E(32.35) 是空中落华, 披风到 34.26 都没亮,
    # 等满 0.8s 照按 = 普通闪避 + 空中普攻(用户看到的"空中 A 了没居合"); 那下普攻(34.15)按下 0.11s 披风就亮了, 一直亮到 1s 后。
    # 白玉切(地面 E)之后是 0.1s 就亮(手打)。
    CAPE_TPL = 'hiyuki_right'
    CAPE_LIT = 0.75              # 亮 0.87~0.98(偶尔一帧掉到 0.67), 不亮 <0.5; 单帧过线就算
    DGATE_FRESH = 0.12           # 到点时最近一次读数这么新、而且亮着, 就不再读直接按
    DGATE_CAP = 1.50             # 不亮(含补普攻)最多等这么久, 还不亮照按。落华在空中 ~1.5s, 补的普攻在下落里可能被吞
    DGATE_A = True               # 不亮就补普攻(闪避前得有攻击动作); False = 只等不补
    DGATE_A_AFTER = 0.10         # 到点不亮再等这么久(读数晚一帧)还不亮才补
    DGATE_A_EVERY = 0.30         # 补完这么久还不亮(被吞了)再补一下
    DGATE_A_HOLD = 0.06
    # ---- 空中/地面: E 图标样式(4K hcenter 坐标, 模板见文件头) ----
    # 手打录像每一下进架势的闪避、每一下跳都在地上, 闪避多在落地后 0~0.1s。10-31 v0.1.5/0.1.6 三局 Boss 第 1 刀没冻住,
    # 居1后那一跳没冰块可落, 闪避时还在空中 -> 空中架势 -> 到 35s 都没落地; 表上的跳成了二段跳越飘越高, 第 2 刀后的 E
    # 成了高空落华(1.3s 披风不亮)。00:39 那局居1冻住了, 跳起 0.74s 落到冰块上, 闪避在落地后 0.06s。
    EAIR_CENTER = (3247.5, 1932.0)
    EAIR_HALF = 84               # 读框半宽(4K), 缩到 56x56 里找 48x48 的模板(图标 ±72)
    EAIR_MIN = 0.55
    EAIR_MARGIN = 0.12           # 一张 >= EAIR_MIN 且比另一张高这么多才算, 不然当没读到
    EAIR_CONFIRM = 2
    EAIR_FRESH = 0.25            # 认定的空中/地面这么新才拿来用
    GROUND_GATE = True           # 进架势的闪避前人在空中就等落地
    GROUND_CAP = 0.80            # 最多等这么久, 还没落地照按(后面的跳靠 JUMP_AIR_SKIP 不再往上飘)
    # 认定空中之后这么久还没落地就不等了: 正常跳起到落地 0.3~0.6s(手打/00:39/09:42 冻住那局); 10-31 09:41~09:45
    # 没冻住的 4 局第 1 刀破盾(白闪), 绯雪跳上倒地的宝箱站着, E 图标一直是空中样式 2.8s, 等落地 4 次都等满 0.8s
    AIR_LAND_MAX = 0.80
    JUMP_AIR_SKIP = True         # 主段表上的跳: 人在空中就不按
    # 表上 'Dl'(启动轴第 1 次跳之后那下进架势的闪避): 手打是跳后 0.80s 按, 实机认定落地是跳后 0.70~0.72s。
    # 开着 = 前面顺延了就落地+披风亮马上按, 顺延追回来(不早于手打那一刻), 追回 0.09~0.11s, R2 赶得上烟岚; 代价是打断落地那一下,
    # 少 22 寒意, 一直欠到居 6, 少第 7 刀: 11-01 16:38(v0.1.14) 录屏剑身寒意条, 第 1 次落地好局(12:10)涨 ~50px、这局不涨,
    # 到居 5 前还差 ~30px(亮头 x 1356 vs 1327, 2K), 居 6 后徽记 1->0(v0.1.14 以为居 3 前徽记都回到 2 就补平了, 不对)。
    # 关着 = 跟 'Dh' 一样等落地出伤。R2 伤害(同一只虚幻珍宝, 第二跳): 16:02 比手打早 0.005s 204 万, 16:38 晚 0.10s 174 万(-15%),
    # 12:10 晚 0.25s 152 万 -> 晚 0.1s 就丢了烟岚。所以关着, 时间改在居 2/3 后那下跳追(见 IAI_J_MIN), 落地那一下照打
    DL_CATCHUP = False
    # 表上 'Dh'(第 2、3 次跳之后那下进架势的闪避): 跳前按着的左键在落地那一下出伤给寒意, 闪避早了把它打断, 后面接不上第 7 刀居合。
    # 11-01 跑轴记录(从认定落地那一刻算): v0.1.10 六局 0.067~0.109s 才闪, 第 7 刀都在; 16:02/16:16 第 2 下 0.000/0.058s、
    # 第 3 下 0.053/0.060s 就闪(第 2 下是 'Dl' 追的; 16:16 第 3 下是照表按的, 那次落地比平时晚 0.04s), 处决后寒意回到 100
    # 晚 0.6s、到不了 200, 居 6 后徽记 1->0, 少第 7 刀。所以这两下(DL_CATCHUP 关着时第 1 下也是)按 认定落地 + DL_HIT_GAP:
    # 前面顺延了照样追到那一刻, 落地晚了比表晚也等
    DL_HIT_GAP = 0.09            # 手打/v0.1.10 平均; 0.06 以下就打断了
    DL_HIT_WAIT = 0.25           # 读到过空中, 到点后最多再等这么久认定落地; 还没认到就照表走(后面 dgate 还会等落地/披风)
    DL_HIT_JUMP = 1.5            # 这么久之内按过跳才算这一跳(表上的跳碰上人在空中不按, jump_at 还是上一跳的)
    # ---- 枯霜门: 到点不亮先等, 还不亮才补居合 ----
    KUS_GRACE = 0.30             # 到点没亮再等这么久(第 3 次居合按下 0.16~0.19s 亮, 表上到点时早该亮了)
    KUS_GRACE_READS = 3          # 等的这段至少要这么多次有效读数(绯雪在场、HUD 在)都不亮, 才算"明确没满"
    KUS_RESCUE = True            # 明确没满且还有徽记(寒意够) -> 补 闪避->居合
    KUS_RESCUE_MAX = 2
    KUS_D_HOLD = 0.06
    KUS_D_TO_A = 0.46            # 手打 闪避->居合 0.45~0.50s
    KUS_A_HOLD = 0.09
    KUS_A_TO_NEXT = 0.83         # 手打 居合->枯霜 0.83s; 补完亮了就提前按
    # ---- 6 居循环轴: 变奏入场那串第 3 刀(连续居合)后面那下跳(表上 'Jk') ----
    # 11-09 01:53/01:57 实机四圈循环第 3 刀全没出(徽记还剩 1 颗、寒意够), 枯霜门都补了一刀(后面整段晚 1.6s)。录屏:
    # 连续居合一刀接一刀要隔 ~0.8s 才起得了手(居 1->居 2 我们和作者都是 0.80s), 表上第 3 刀在第 2 刀出手后 0.30s 就按、
    # 跳只隔 0.17s, 第 3 刀能起手之前跳先出了, 人跳起来、第 3 刀没了(启动轴居 1 也是这样: 还没出手就跳 = 居合被顶掉)。
    # 作者视频是第 3 刀出手(刀身变紫、最后一颗徽记扣掉)之后 ~0.1s 才跳。所以这下跳等枯霜就绪亮(= 第 3 刀出了)再按,
    # 不早于表, 晚了后面整段照实顺延。第 3 刀按下 IAIK_RETAP 还没亮补按一下普攻(按太早游戏可能不收), 过 IAIK_CAP 还不亮
    # 或者没徽记就照按(后面枯霜门补刀)
    IAIK_RETAP = 0.25
    IAIK_CAP = 1.00
    # ---- 6 居循环轴: 枯霜后 A A 按住A(重击) 那下按住(表上 'Ah') ----
    # 按住A 要落在 A2 收招(派生帧)之后: 先出 A3(冰环, 32 寒意), 按着再转重击。落在 A2 派生帧之前, 游戏直接接重击, A3 没了。
    # 表上这下离枯霜按下 3.69s, 前提是枯霜按下 ~2.51s 后就绪图标灭(启动轴三局 2.51~2.54s)。11-09 02:22 实机循环 L1
    # (DVR 02.23.53, 录屏 = 记录 +369.2): 落华还在空中就按了枯霜, 枯霜晚收 0.18s(按下->灭 2.69s), 按住A 落在灭 +1.00s,
    # 重击 24F 蓄力在按下 +0.40s 就起(直接接的重击), 比作者视频(同样以枯霜大特写起手对齐)早 0.23~0.30s, 寒意条重击前后
    # 比作者少 ~12; 前面 A1/A2 两下是排队等收招的, 动画时刻跟作者差 <0.1s。
    # 所以这下按在 max(表, 枯霜灭 + HOLDK_OFS), 晚了后面整段顺延(作者后面闪避/居 4 也比我们晚 ~0.3s)。
    # 灭没读到(还亮着)最多等 HOLDK_CAP; 这一下枯霜之后根本没灭过就按 表 + HOLDK_FALLBACK
    HOLDK_OFS = 1.25
    HOLDK_CAP = 1.00
    HOLDK_FALLBACK = 0.25

    # ---- 转火: 这一只死了、下一轮的还没刷出来/没锁上 ----
    # 有没有怪 = 锁定图标锁着 / Boss 破盾图标 / 血条, 任一读到就算有。矩阵奇藏手打不锁怪, 只看锁定图标会一直判没怪;
    # 虚幻珍宝的血条颜色对不上, 认的是破盾图标。读丢的情况: R2 演完 HUD 回来第 1 帧(下一帧就有)、手打整段单帧丢 1 次、
    # 10-30 23:36 实机洛瑟菈大招起手的边框特效把 Boss 条盖了 1.0s(队伍 HUD 还在) —— 所以只在 R2 前/一圈收尾/圈首判没怪
    TF_ON = True
    TF_READ_GAP = 0.30           # 宏里顺手读"有没有怪"的间隔(一次 5~15ms)
    TF_BUDGET = 0.09             # 离下一个按键至少还有这么久才顺手读
    TF_GONE_CONFIRM = 3          # 连这么多次读不到、
    TF_GONE_SPAN = 0.25          # 而且头尾隔了这么久, 才算这一只没了
    TF_CONFIRM_GAP = 0.06        # 判没怪/等新怪时的读数间隔
    TF_CONFIRM_CAP = 0.60        # 判没怪最多读这么久(HUD 不在/读不了就不等)
    TF_SEEN_CONFIRM = 3          # 等新怪时锁不上的话, 连这么多次读到破盾图标/血条也算来了
    TF_KEEP_LOCK = True          # 有怪但锁定图标明确是没锁 -> 按中键锁(框架在两圈之间也这么做)
    TF_LOCK_CONFIRM = 2          # 连这么多次"有怪没锁"才按(中键是开关, 读错按下去会把锁解掉)
    TF_LOCK_GAP = 0.80           # 按完中键至少隔这么久再按
    TF_LOCK_GIVEUP = 3           # 连按这么多次都没锁上(锁不了的阶段), 之后每 TF_LOCK_SLOW 秒才试一次
    TF_LOCK_SLOW = 3.0
    TF_WAVE_CLICK = 0.30         # 等新怪时按中键的间隔(10-08 旧版实测新怪 0.9~1.5s 后才锁得上)
    # R2 演完场上没怪: 等新怪再开下一圈(穗穗变奏打空就没协奏, 整圈错位)。等的时候宏占着, 框架不判脱战 ——
    # 交回去框架只等 3s, 超了就结束战斗, 下次进战斗离上次启动轴超过 60s 会重跑启动轴
    TF_ROUND_WAIT = 10.0
    TF_R2_WAIT = 5.0             # 到 R2 时场上没怪: 最多等这么久再放(预求身没时限, 放 R2 才退出)

    # ==================== 防重入 ====================
    OPENER_MIN_GAP = 60.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opener_armed = True
        self.last_opener = -1
        self.macro_t0 = 0.0
        self.macro_marks = []
        self.cur = None
        self.use_si = True
        self.held = {}                 # 按着的键 -> 走的哪条路('si' / 'pm'), 松开要走同一条
        self.vk_cache = {}
        self.shift = 0.0               # 本段因为等切人/补居合/处决累计顺延了多少秒
        self.worst_late = 0.0
        self.sw_target = None          # 按了数字键还没认到的那个人
        self.sw_source = None
        self.sw_at = 0.0
        self.sw_last = 0.0
        self.sw_n = 0
        self.sw_untrusted = 0
        self.sw_out_at = {}            # 类名 -> 认到被换下来的时刻(切人冷却用)
        self.poll_at = 0.0
        self.hud_seen_at = 0.0         # 最后一次读到队伍 HUD 在场的时刻(HUD_LOST_ABORT 用)
        self.hud_watch = False
        self.drive_log_at = 0.0
        self.timeline_logged = True
        self.loop_count = 0
        self.hy_form = None            # 'base' = 没变身, 'yq' = 预求身(R1 之后、R2 之前); None = 不知道
        self.r2_back_at = -99.0
        self.iai_box = None
        self.iai_box_size = None
        self.iai_reset()
        self.tf_reset()
        self.warmed = None             # 预热过模板的画面尺寸
        # reset_state() 只在 load_chars() 时跑, 而角色对象会被粘性复用 —— 这里也要启一次
        try:
            start_enable_watch(self.task, self.logger)
        except Exception:
            pass

    def reset_state(self):
        super().reset_state()
        self.opener_armed = True
        # 内置非治疗角色默认 True, 换类时会被打回, 所以在这里重申(这条轴自己按处决)
        self.check_f_on_switch = False
        start_f_watch(self.task, self.logger)
        start_enable_watch(self.task, self.logger)

    # ==================== 队伍判定 ====================

    @property
    def is_in_hls(self) -> bool:
        """本队 = 绯雪 + 洛瑟菈 + 穗穗。按类名判。"""
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Lucilla', 'Suisui'}:
                members += 1
        return members == 2

    def teammate(self, name):
        if name == self.name:
            return self
        for char in self.task.chars:
            if char is not None and char.name == name:
                return char
        return None

    def opener_pending(self):
        return self.opener_blocked_by() is None

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。"""
        if not self.is_in_hls:
            return 'team is not hiyuki/lucilla/suisui'
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

    # ==================== 时间轴 / 看门狗 ====================

    def macro_wait(self, sec):
        """精确等待, 【不读屏】。切成 0.1 秒一片, 片间检查任务是否被停用。"""
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

    def macro_start(self):
        self.macro_t0 = time.time()
        self.macro_marks = []

    def macro_mark(self, name):
        self.macro_marks.append(f'{name}@{time.time() - self.macro_t0:.2f}')
        if self.hud_watch and time.time() - self.hud_seen_at > self.HUD_LOST_ABORT:
            self.hud_watch = False
            raise MacroBroken(f'队伍 HUD {time.time() - self.hud_seen_at:.1f}s 没出现(关卡结束/过场/窗口没了?)')

    def macro_timeline(self, since=0):
        return ' '.join(self.macro_marks[since:])

    def axis_mark(self, name, value=None, box=None):
        """给跑轴记录插件打点(没装插件就什么都不做)。"""
        mark = getattr(self.task, 'axis_mark', None)
        if mark:
            try:
                mark(name, value, box)
            except Exception:
                pass

    def mark_busy(self, busy):
        """F 键监视线程靠它区分"战斗中手动处决"和"重开副本"。"""
        self.task._ww_macro_busy = busy
        self.task._three_fire_macro_busy = busy

    def game_foreground(self):
        try:
            return bool(self.task.executor.interaction.hwnd_window.is_foreground())
        except Exception:
            return False

    # ==================== 按键 ====================

    def key_name(self, k):
        if k == 'E':
            return self.get_resonance_key()
        if k == 'Q':
            return self.get_echo_key()
        if k == 'R':
            return self.get_liberation_key()
        if k == 'J':
            try:
                return self.task.key_config.get('Jump Key', 'space') or 'space'
            except Exception:
                return 'space'
        return str(k)

    def vk_of(self, key):
        key = str(key)
        vk = self.vk_cache.get(key)
        if vk is not None:
            return vk
        vk = 0
        try:
            vk = int(self.task.executor.interaction.get_key_by_str(key)) & 0xff
        except Exception:
            code = vk_key_dict.get(key.upper())
            if code:
                vk = int(code)
            elif len(key) == 1:
                try:
                    vk = int(ctypes.windll.user32.VkKeyScanW(ord(key))) & 0xff
                except Exception:
                    vk = 0
        self.vk_cache[key] = vk
        return vk

    def press(self, k, up):
        """k = 'LMB' / 'RMB' / 'E' / 'Q' / 'R' / 'J' / 数字键。按下记进 held, 松开走按下时那条路。"""
        if up:
            way = self.held.pop(k, None)
            if way is None:
                return
        else:
            way = 'si' if self.use_si else 'pm'
        if k in ('LMB', 'RMB'):
            button = 'left' if k == 'LMB' else 'right'
            sent = way == 'si' and si_mouse(button, up)
            if not sent:
                if way == 'si' and up:
                    self.logger.warning(f'SendInput {k} 松开没发出去 — 如果鼠标卡住, 手动点一下')
                way = 'pm'
                if up:
                    self.task.mouse_up(key=button)
                else:
                    self.task.mouse_down(key=button)
        else:
            key = self.key_name(k)
            vk = self.vk_of(key)
            sent = way == 'si' and vk and si_key(vk, up)
            if not sent:
                way = 'pm'
                if up:
                    self.task.send_key_up(key)
                else:
                    self.task.send_key_down(key)
        if not up:
            self.held[k] = way

    def tap_now(self, k, hold):
        """点一下, 【真的按住 hold 秒】(不读屏)。"""
        self.press(k, False)
        try:
            self.macro_wait(hold)
        finally:
            self.press(k, True)

    def release_all(self):
        for k in list(self.held):
            try:
                self.press(k, True)
            except Exception:
                self.held.pop(k, None)

    def click(self, *args, **kwargs):
        """本队里 self.click() 改成按住 KEY_TAP(BaseChar.click 强制 10ms, 等于没点)。不在本队走原版。"""
        if not self.is_in_hls:
            return super().click(*args, **kwargs)
        self.tap_now('LMB', self.KEY_TAP)

    # ==================== 读屏 ====================

    def poll(self, budget=None, tf_gap=None):
        """读一帧: 认 HUD(看门狗)、没认到的切人、居合徽记。返回队伍 HUD 在不在。
        有没有怪(转火): 给了 tf_gap 就按这个间隔读; 只给 budget(离下一个按键还有几秒)时够远才顺手读; 都没给不读。"""
        t_in = time.time()
        gap = self.poll_at + self.POLL_MIN_INTERVAL - time.time()
        if gap > 0:
            time.sleep(gap)
        self.poll_at = time.time()
        self.task.next_frame()
        try:
            in_team, index, count = self.task.in_team()
        except Exception:
            in_team, index, count = False, -1, 0
        now = time.time()
        if in_team:
            self.hud_seen_at = now
        elif self.hud_watch and now - self.hud_seen_at > self.HUD_LOST_ABORT:
            self.hud_watch = False
            raise MacroBroken(f'队伍 HUD {now - self.hud_seen_at:.1f}s 没出现(关卡结束/过场/窗口没了?)')
        if self.sw_target is not None:
            self.sw_check(in_team, index, count, now)
        if self.iai_on:
            self.iai_update(bool(in_team) and index == self.index, now)
        if in_team and self.tf_on and self.TF_ON:
            now = time.time()
            if tf_gap is not None:
                due = now - self.tf_read_at >= tf_gap
            else:
                due = (budget is not None and now - self.tf_read_at >= self.TF_READ_GAP
                       and budget - (now - t_in) >= self.TF_BUDGET)
            if due:
                self.tf_update(now)
        # 绯雪主段: 离下一个按键够远就顺手看处决提示, 到点那一帧被挡住时靠它
        if (in_team and self.iai_on and budget is not None
                and time.time() - self.brk_peek_at >= self.BREAK_PEEK_GAP
                and budget - (time.time() - t_in) >= self.BREAK_PEEK_BUDGET):
            self.brk_peek_at = time.time()
            if self.break_prompt():
                self.brk_seen_at = time.time()
        return bool(in_team)

    def idle_until(self, due):
        """等到 due。离下一个按键还早就顺手读屏, 贴近了就只睡。"""
        while True:
            left = due - time.time()
            if left <= 0:
                return
            if left >= self.READ_MIN_GAP:
                self.poll(budget=left)
                continue
            time.sleep(left)
            return

    def wait_hud_up(self, cap=None, tag='HUD'):
        """等队伍 HUD 【现在】在场。"""
        cap = self.HUD_UP_CAP if cap is None else cap
        start = time.time()
        while time.time() - start < cap:
            if self.poll():
                return True
        self.macro_mark(f'{tag}一直不在({cap:.1f}s)')
        return False

    def wait_hud_gone(self, tag, until):
        """等 HUD 连续消失(演出开始)。返回第一帧消失的时刻, 到 until 都没消失返回 None。"""
        gone = 0
        gone_at = None
        while time.time() < until:
            if not self.poll():
                gone += 1
                if gone_at is None:
                    gone_at = time.time()
                if gone >= self.LIB_GONE_CONFIRM:
                    self.macro_mark(f'{tag}(进演出)')
                    return gone_at
            else:
                gone = 0
                gone_at = None
        self.macro_mark(f'{tag}(没进演出)')
        return None

    def wait_hud_back(self, tag, gone_at, cap, min_gone, confirm=1):
        """演出(从 gone_at 开始)结束、HUD 回来。返回第一帧回来的时刻。min_gone 之前的"回来"不认(演出中途会闪)。"""
        start = time.time()
        hits = 0
        first = None
        while time.time() - start < cap:
            on = self.poll()
            now = time.time()
            if not on or now - gone_at < min_gone:
                hits = 0
                first = None
                continue
            hits += 1
            if first is None:
                first = now
            if hits >= confirm:
                self.add_freeze_duration(gone_at, first - gone_at)
                self.macro_mark(f'{tag}({first - gone_at:.2f}s)')
                return first
        self.macro_mark(f'{tag}超时({cap:.1f}s)')
        return time.time()

    def break_prompt(self):
        """怪身上有处决提示(排除拾取 F)。读当前帧。不看顶部的谐度满: 那个比提示早好几秒。"""
        task = self.task
        try:
            hit = task.find_one('f_break', box=task.box_of_screen(*self.BREAK_BOX),
                                target_height=720, threshold=self.BREAK_CENTER_MIN)
            return bool(hit) and not task.is_pick_f()
        except Exception as e:
            self.logger.info(f'处决提示读失败 {e}')
            return False

    # ==================== 切人 ====================

    def sw_press(self, name, tag=''):
        """按数字键切到 name, 不等; 认人在 poll() 里。"""
        self.sw_blind = False
        target = self.teammate(name)
        if target is None:
            raise MacroBroken(f'{tag}: 队伍里没有 {name}')
        if self.sw_target is None and self.cur is target:
            self.macro_mark(f'{tag}>{name[:3]}(已在场)')
            return
        if self.sw_target is not None and self.sw_target is not target:
            self.need_field(tag)
            if self.cur is target:
                return
        self.sw_source = self.cur if self.cur is not None else self
        self.sw_target = target
        self.sw_untrusted = 0
        now = time.time()
        cool = self.SW_COOLDOWN - (now - self.sw_out_at.get(name, -99.0))
        if cool > 0:
            # 冷却里按了会被吞: 先不按, 冷却一过由 sw_check 按第一下
            self.sw_at = now + cool
            self.sw_last = self.sw_at - self.SW_RESEND
            self.sw_n = 0
            self.macro_mark(f'{tag}>{name[:3]}等冷却{cool:.2f}')
            return
        self.sw_at = self.sw_last = now
        self.sw_n = 1
        self.tap_now(str(target.index + 1), self.SW_TAP)

    def sw_check(self, in_team, index, count, now):
        target = self.sw_target
        team_size = len([c for c in self.task.chars if c is not None])
        hit = in_team and index == target.index
        trusted = in_team and count >= team_size
        self.sw_untrusted = self.sw_untrusted + 1 if (hit and not trusted) else 0
        if hit and (trusted or self.sw_untrusted >= self.UNTRUSTED_CONFIRM):
            self.sw_target = None
            self.handoff(self.sw_source, target)
            self.macro_mark(f'>{target.name[:3]}({now - self.sw_at:.2f}{f",x{self.sw_n}" if self.sw_n > 1 else ""})')
            return
        if now - self.sw_at > self.SW_CAP:
            self.sw_target = None
            raise MacroBroken(f'切 {target.name} {self.SW_CAP:.1f}s 没切过去(按了 {self.sw_n} 次)')
        if self.sw_blind and in_team:
            # 演出里按的那下没被收(HUD 回来场上还是原来的人): 马上重按, 不等 SW_RESEND
            self.sw_blind = False
            self.sw_last = now
            self.sw_n += 1
            self.tap_now(str(target.index + 1), self.SW_TAP)
            return
        if now - self.sw_last >= self.SW_RESEND:
            self.sw_last = now
            self.sw_n += 1
            self.tap_now(str(target.index + 1), self.SW_TAP)

    def need_field(self, tag=''):
        """按了数字键还没认到人就一直读到认到(或 SW_CAP 放弃)。"""
        while self.sw_target is not None:
            self.poll()

    def handoff(self, from_char, to_char):
        """复刻 switch_next_char() 尾部那套交接记账。不补的话 get_current_char() 返回 None, 主循环当场崩。"""
        self.task.in_liberation = False
        if from_char is not None and from_char is not to_char:
            from_char.switch_out(con_full=False)
            self.sw_out_at[from_char.name] = time.time()
        to_char.is_current_char = True
        to_char.has_intro = False
        to_char.has_sub_dps_intro = False
        to_char.last_switch_in_time = time.time()
        self.cur = to_char

    def on_field_char(self, time_out=1.5):
        """现在场上是谁 —— 只在拿不到入口角色时才用(开局 HUD 渐入时 in_team() 会说谎, 要 count 达标)。"""
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
                return None
            self.macro_wait(0.03)

    # ==================== 宏表回放 ====================

    def op_main_rows(self):
        """启动轴主段宏表: 架势里的居合比手打早 OP_IAI_LEAD 按; OP_IAI7 关掉就不按第 7 刀那下 A。"""
        rows = []
        for t, op, arg in self.OP_MAIN:
            if op in ('iai', 'iaiw'):
                t = round(t - self.OP_IAI_LEAD, 3)
            elif op == 'A' and t == self.OP_IAI7_AT and not self.OP_IAI7:
                continue
            rows.append((t, op, arg))
        return tuple(rows)

    def expand(self, rows):
        """宏表 -> 按时间排好的 (t, 次序, 事件, 参数)。点按拆成按下/松开两个事件, 跟后面的事件可以交叠。"""
        ev = []
        for t, op, arg in rows:
            if op in ('A', 'Ah', 'D', 'Di', 'Dl', 'Dh', 'J', 'Jc', 'Jc3', 'Jk', 'E', 'Q', 'iai', 'iaiw', 'iaic', 'iaicA',
                      'kus'):
                key = {'A': 'LMB', 'Ah': 'LMB', 'iai': 'LMB', 'iaiw': 'LMB', 'iaic': 'LMB', 'iaicA': 'LMB', 'kus': 'LMB',
                       'D': 'RMB',
                       'Di': 'RMB', 'Dl': 'RMB', 'Dh': 'RMB', 'Jc': 'J', 'Jc3': 'J', 'Jk': 'J'}.get(op, op)
                if op == 'Ah':
                    ev.append((t, 0, 'ahold', None))
                elif op in ('iaic', 'iaicA'):
                    ev.append((t, 0, 'iaicw', op == 'iaicA'))
                    ev.append((t, 0, 'iai', None))
                elif op == 'iaiw':
                    ev.append((t, 0, 'iaigate', None))
                    ev.append((t, 0, 'iai', None))
                elif op in ('iai', 'kus'):
                    ev.append((t, 0, op, None))
                    if op == 'kus':
                        ev.append((t, 1.5, 'kuslag', None))   # 枯霜按下之后、后面一切之前
                elif op == 'Jc':
                    ev.append((t, 0, 'jcatch', self.IAI_J_MIN))
                elif op == 'Jc3':
                    ev.append((t, 0, 'jfloor', self.IAI_J_MIN_3A))
                elif op == 'Jk':
                    ev.append((t, 0, 'jkus', None))
                elif op in ('Di', 'Dl', 'Dh'):
                    if op == 'Dl':
                        ev.append((t, 0, 'dland', None))
                    elif op == 'Dh':
                        ev.append((t, 0, 'dhit', None))
                    ev.append((t, 0, 'dgate', None))
                ev.append((t, 1, 'down', key))
                ev.append((t + arg, 2, 'up', key))
            elif op == 'Ad':
                ev.append((t, 1, 'down', 'LMB'))
            elif op == 'Au':
                ev.append((t, 1, 'up', 'LMB'))
            elif op == 'Ed':
                ev.append((t, 1, 'down', 'E'))
            elif op == 'Eu':
                ev.append((t, 1, 'up', 'E'))
            elif op == 'sw':
                ev.append((t, 1, 'sw', arg))
            elif op == 'F?':
                ev.append((t, 1, 'brk', arg))
            else:
                raise ValueError(f'unknown macro op {op}')
        ev.sort(key=lambda e: (e[0], e[1]))
        return ev

    def play(self, rows, anchor, tag):
        """按 anchor + t + shift 的时刻回放一张宏表。没处决直接 R2(brk_to_r2 设 main_abort)就停在那儿。"""
        skip_t = None
        self.main_abort = False
        for t, _, kind, arg in self.expand(rows):
            if skip_t is not None and t >= skip_t - 1e-6 and kind in ('iai', 'down', 'up') and (
                    kind == 'iai' or arg == 'LMB') and t <= skip_t + 0.5:
                continue
            due = anchor + t + self.shift
            if kind == 'iaicw':
                self.shift, ok = self.iai_cont_wait(anchor + t, due, tag, arg)
                if not ok:
                    skip_t = t
                continue
            if kind == 'dland':
                if not self.DL_CATCHUP:
                    self.shift = self.land_hit_wait(anchor + t, due, tag)
                elif self.shift > 0.0:
                    self.shift = self.land_catchup(anchor + t, due, tag)
                continue
            if kind == 'dhit':
                self.shift = self.land_hit_wait(anchor + t, due, tag)
                continue
            if kind == 'jcatch':
                if self.shift > 0.0:
                    self.shift = self.jump_catchup(anchor + t, due, arg, tag)
                continue
            if kind == 'jfloor':
                self.shift = self.jump_floor(anchor + t, due, arg, tag)
                continue
            if kind == 'jkus':
                self.shift = self.jump_after_iai(anchor + t, due, tag)
                continue
            if kind == 'ahold':
                self.shift = self.hold_after_kus(anchor + t, due, tag)
                continue
            if kind == 'kuslag':
                if self.kus_lag > 0.0:
                    self.shift += self.kus_lag
                    self.macro_mark(f'{tag}补刀后枯霜顺延{self.kus_lag:.2f}')
                    self.kus_lag = 0.0
                continue
            self.idle_until(due)
            if kind != 'up' and self.sw_target is not None and (
                    (kind == 'down' and arg in ('E', 'Q', 'R')) or time.time() - self.sw_at >= self.SW_BLOCK_AFTER):
                # 技能键 / 切人拖久了: 先等认到人, 晚了多少后面整段顺延多少
                self.need_field(tag)
                self.shift += max(0.0, time.time() - due)
                due = anchor + t + self.shift
            late = time.time() - due
            if kind in ('down', 'sw', 'brk') and late > self.LATE_WARN:
                self.worst_late = max(self.worst_late, late)
                self.macro_mark(f'{tag}{arg or kind}晚{late:.2f}')
            if kind == 'down':
                if arg == 'J' and self.JUMP_AIR_SKIP and self.iai_on and self.in_air():
                    self.j_skip = True
                    self.macro_mark(f'{tag}空中不跳')
                    self.axis_mark('空中不跳')
                    continue
                if arg in ('E', 'J') and self.iai_on:
                    # 跳 / 空中 E(落华往下砸)之后多久落地从这一下重新算
                    self.air_since = time.time()
                    if arg == 'J':
                        self.jump_at = self.air_since
                self.press(arg, False)
                if arg == 'RMB':
                    self.dodge_at = time.time()
                elif arg == 'LMB':
                    self.lmb_at = time.time()
            elif kind == 'up':
                if arg == 'J' and self.j_skip:
                    self.j_skip = False
                    continue
                self.press(arg, True)
            elif kind == 'sw':
                self.sw_press(arg, tag)
            elif kind == 'dgate':
                self.shift += self.dodge_gate(tag)
            elif kind == 'iaigate':
                self.shift += self.iai_da_gate(tag)
            elif kind == 'iai':
                self.iai_begin(tag)
            elif kind == 'kus':
                self.shift += self.kus_gate(tag)
                self.kus_press_at = time.time()
            elif kind == 'brk':
                self.shift += self.brk(due, tag, arg)
                if self.main_abort:
                    self.release_all()
                    break

    # ==================== 居合徽记 ====================

    def iai_reset(self):
        self.iai_on = False
        self.iai_c = -1                # 认定的徽记颗数(-1 = 还没读到过)
        self.iai_blue = False
        self.iai_last_raw = -1
        self.iai_hits = 0
        self.iai_watch = None
        self.iai_k = 0                 # 本段按了几次居合
        self.iai_log = []
        self.iai_kus = []
        self.kus_lit = False           # 枯霜就绪图标(淬寒 3 层)认定亮着
        self.kus_raw = None
        self.kus_hits = 0
        self.kus_reads = 0             # 有效读数次数(绯雪在场才读)
        self.kus_lit_by = 0            # 亮起时最近按的是第几次居合
        self.kus_lit_at = 0.0          # 认定亮起的时刻
        self.kus_off_at = 0.0          # 认定灭掉的时刻(枯霜放出去后清淬寒)
        self.kus_press_at = 0.0        # 宏表上枯霜那下按下的时刻
        self.iai_drop_at = 0.0         # 认定徽记少了一颗(扣寒意)的最近时刻
        self.iaic_skip = False         # 6 居最后一刀徽记没亮回来, 没按
        self.kus_lag = 0.0             # 枯霜门补过刀: 枯霜按下后整段再顺延这么久(play 的 kuslag 用掉)
        self.brk_seen_at = -99.0       # 主段里顺手看到处决提示的最近时刻
        self.brk_peek_at = 0.0
        self.cape_lit = False          # 闪避键是不是披风(最近一次读数)
        self.cape_at = 0.0
        self.air_state = None          # 认定的 'A' 空中 / 'G' 地面 / None 还没认出来
        self.air_raw = None
        self.air_hits = 0
        self.air_at = 0.0              # 最近一次读出空中/地面的时刻
        self.air_since = 0.0           # 认定空中的时刻
        self.air_up_at = 0.0           # 读数认定变成空中的时刻(按跳/E 不算)
        self.jump_at = 0.0             # 主段最近一次按跳
        self.land_at = 0.0             # 读数认定落地的时刻
        self.j_skip = False
        self.dodge_at = 0.0            # 表上最近一下闪避按下的时刻
        self.lmb_at = 0.0              # 表上最近一下左键按下的时刻

    def iai_read(self):
        """读两颗徽记。返回 (颗数, 最右边那颗蓝不蓝, 两颗的分数); 读不了返回 None。"""
        frame = self.task.frame
        if frame is None:
            return None
        h, w = frame.shape[:2]
        if h <= 0 or w <= 0:
            return None
        scale = h / 2160.0
        pad = self.IAI_R + self.IAI_S
        interp = cv2.INTER_AREA if scale > 0.67 else cv2.INTER_LINEAR
        scores = []
        blues = []
        for cx, cy in self.IAI_SLOTS:
            x1 = int(w / 2 + (cx - pad - 1920) * scale)
            y1 = int((cy - pad) * scale)
            x2 = int(w / 2 + (cx + pad - 1920) * scale)
            y2 = int((cy + pad) * scale)
            if x1 < 0 or y1 < 0 or x2 > w or y2 > h or x2 - x1 < 8:
                return None
            roi = np.ascontiguousarray(frame[y1:y2, x1:x2, :3])
            roi = cv2.resize(roi, (60, 60), interpolation=interp)
            res = cv2.matchTemplate(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), IAI_TPL, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            disk = roi[loc[1] + 2:loc[1] + 42, loc[0] + 2:loc[0] + 42]
            sat = cv2.cvtColor(disk, cv2.COLOR_BGR2HSV)[..., 1][IAI_DISK]
            scores.append(float(mx))
            blues.append(float((sat > 120).mean()))
        present = [s >= self.IAI_PRESENT for s in scores]
        count = int(sum(present))
        right = 1 if present[1] else 0
        blue = bool(count) and blues[right] >= self.IAI_BLUE
        return count, blue, scores

    def cut_read(self):
        """寒意条剑格右边那截刀身: 返回 (白占比, 暗蓝占比); 读不了返回 None。"""
        frame = self.task.frame
        if frame is None:
            return None
        h, w = frame.shape[:2]
        if h <= 0 or w <= 0:
            return None
        s = h / 2160.0
        bx1, by1, bx2, by2 = self.CUT_BOX
        x1 = int(w / 2 + (bx1 - 1920) * s)
        x2 = int(w / 2 + (bx2 - 1920) * s)
        y1 = int(by1 * s)
        y2 = int(by2 * s)
        if x1 < 0 or y1 < 0 or x2 > w or y2 > h or x2 - x1 < 4 or y2 - y1 < 2:
            return None
        roi = np.ascontiguousarray(frame[y1:y2, x1:x2, :3])
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        white = float(((hsv[..., 1] < 70) & (hsv[..., 2] > 200)).mean())
        dark = float(((roi[..., 0].astype(np.int16) - roi[..., 2].astype(np.int16) > 50) & (hsv[..., 2] < 190)).mean())
        return white, dark

    def iai_cont_wait(self, early, due, tag, rescue=False):
        """表上 'iaic'/'iaicA'(6 居循环的连续居合 居 2/居 3/居 6): 徽记灭着按 A 游戏不收, 等上一刀扣过寒意、徽记亮着再按,
        不早于表。上一刀按下 + IAIC_CAP 还不亮 = 寒意不够, 这一刀不按。
        rescue('iaicA'): 上一刀扣过、徽记没亮就连点普攻补寒意, 亮了自己补出这一刀(早亮按 A 接连续居合, 晚亮或没接上就
        闪避进架势再居合), 等扣了寒意才回去接表(跳 E)。返回 (新的顺延, 表上那下按不按)。"""
        if rescue and self.iaic_skip:
            self.macro_mark(f'{tag}居{self.iai_k + 1}等徽记:上一刀没补上, 不按')
            self.axis_mark('连续居合等徽记', None)
            return time.time() - early, False
        self.iaic_skip = False
        self.idle_until(due)
        w = self.iai_watch
        prev = w['t'] if w else time.time()
        cap = prev + (self.IAIC_RESCUE_CAP if rescue else self.IAIC_CAP)
        taps = 0
        next_tap = 0.0
        while time.time() < cap:
            now = time.time()
            settled = self.iai_drop_at > prev or now >= prev + self.IAIC_DROP_WAIT
            if self.iai_c >= 1 and settled:
                break
            if rescue and settled and now >= next_tap and 'LMB' not in self.held:
                self.tap_now('LMB', self.KEY_TAP)
                self.lmb_at = now
                taps += 1
                next_tap = now + self.BRK_SPAM_EVERY
                continue
            self.poll()
        now = time.time()
        ok = self.iai_c >= 1
        if ok and taps:
            return self.iai_rescue(early, tag, prev, taps), False
        at = max(due, now)
        if ok:
            how = '亮着' if at - due < 0.02 else f'亮回来(上一刀+{at - prev:.2f})'
        else:
            how = f'没亮(上一刀+{at - prev:.2f}{f",补A {taps}下" if taps else ""}), 不按'
            self.iaic_skip = True
        self.macro_mark(f'{tag}居{self.iai_k + 1}等徽记:{how}')
        self.axis_mark('连续居合等徽记', round(at - due, 2) if ok else None)
        return at - early, ok

    def iai_wait_cut(self, pressed, span):
        """居合按下后等扣寒意(徽记少一颗)最多 span 秒。"""
        while time.time() < pressed + span:
            if self.iai_drop_at > pressed:
                return True
            self.poll()
        return self.iai_drop_at > pressed

    def iai_rescue(self, early, tag, prev, taps):
        """连点普攻把徽记补亮了: 补出这一刀居合(见 IAIC_TAP_WINDOW), 扣了寒意(或等满确认时长)就返回新的顺延。"""
        lit = time.time() - prev
        pre = self.iai_c
        how = ''
        pressed = None
        if lit <= self.IAIC_TAP_WINDOW:
            at = time.time()
            self.tap_now('LMB', self.KEY_TAP)
            self.lmb_at = at
            if self.iai_wait_cut(at, self.IAIC_TAP_CONFIRM) or self.iai_c < 1:
                self.iai_begin(tag, at=at, pre=pre)
                pressed = at
                how = '按A接上'
            else:
                how = '按A没接上,'
        if pressed is None:
            self.dodge_gate(f'{tag}补')
            d_at = time.time()
            self.tap_now('RMB', self.KUS_D_HOLD)
            self.dodge_at = d_at
            self.idle_until(d_at + self.KUS_D_TO_A)
            self.iai_begin(tag)
            pressed = time.time()
            self.tap_now('LMB', self.KUS_A_HOLD)
            self.lmb_at = pressed
            cut = self.iai_wait_cut(pressed, self.IAIC_STANCE_CONFIRM)
            how += f'闪避进架势补居合({"扣了" if cut else "没看到扣"})'
        self.macro_mark(f'{tag}居{self.iai_k}等徽记:补A{taps}下亮了(上一刀+{lit:.2f}),{how}')
        self.axis_mark('连续居合补A', round(lit, 2))
        return pressed - early

    def r2_wait_cut(self, due, tag):
        """6 居循环: 最后一刀(连续居合)按下后等扣寒意(徽记少一颗, 或剑格右边那截刀身由白变暗)再按 R2, 不早于表。
        IAIC_RETAP 还没扣补按一下普攻; 等到 R2CUT_CAP 还没扣就照按。最后一刀没按(徽记没亮回来)就直接按。返回 R2 的按点。"""
        if self.iaic_skip:
            at = max(due, time.time())
            self.macro_mark(f'{tag}R2等扣寒意:最后一刀没按, 直接放')
            self.axis_mark('R2等扣寒意', None)
            return at
        w = self.iai_watch
        a = w['t'] if w else self.lmb_at
        self.poll()
        r = self.cut_read()
        use_cut = r is not None and r[0] >= self.CUT_WHITE
        cut_at = None
        how = ''
        retap = False
        while time.time() < a + self.R2CUT_CAP:
            if self.iai_drop_at > a:
                cut_at, how = self.iai_drop_at, '徽记'
                break
            if use_cut:
                r = self.cut_read()
                if r is not None and r[1] >= self.CUT_DARK and r[0] <= self.CUT_GONE_WHITE:
                    cut_at, how = time.time(), '刀身'
                    break
            if not retap and time.time() >= a + self.IAIC_RETAP and 'LMB' not in self.held:
                self.tap_now('LMB', self.KEY_TAP)
                retap = True
            self.poll()
        at = max(due, cut_at if cut_at is not None else time.time())
        note = (f'扣了{how}(A+{cut_at - a:.2f})' if cut_at is not None else f'没扣(A+{at - a:.2f}照按)') + \
            (',补A' if retap else '')
        self.macro_mark(f'{tag}R2等扣寒意:{note}')
        self.axis_mark('R2等扣寒意', round(at - a, 2))
        return at

    def iai_mark_box(self):
        """跑轴记录用的框(两颗徽记一起), 跟着分辨率缓存。"""
        frame = self.task.frame
        size = None if frame is None else frame.shape[:2]
        if self.iai_box is None or size != self.iai_box_size:
            pad = self.IAI_R + self.IAI_S
            (lx, ly), (rx, ry) = self.IAI_SLOTS
            try:
                self.iai_box = self.task.box_of_screen_scaled(3840, 2160, lx - pad, min(ly, ry) - pad,
                                                              rx + pad, max(ly, ry) + pad, hcenter=True)
            except Exception:
                self.iai_box = None
            self.iai_box_size = size
        return self.iai_box

    def air_read(self):
        """E 图标是空中样式还是地面样式。返回 'A' / 'G'; 判不出来或读不了返回 None。"""
        frame = self.task.frame
        if frame is None:
            return None
        h, w = frame.shape[:2]
        if h <= 0 or w <= 0:
            return None
        s = h / 2160.0
        (cx, cy), half = self.EAIR_CENTER, self.EAIR_HALF
        x1 = int(w / 2 + (cx - half - 1920) * s)
        x2 = int(w / 2 + (cx + half - 1920) * s)
        y1 = int((cy - half) * s)
        y2 = int((cy + half) * s)
        if x1 < 0 or y1 < 0 or x2 > w or y2 > h or x2 - x1 < 8:
            return None
        roi = cv2.cvtColor(np.ascontiguousarray(frame[y1:y2, x1:x2, :3]), cv2.COLOR_BGR2GRAY)
        roi = cv2.resize(roi, (56, 56), interpolation=cv2.INTER_AREA)
        sc = []
        for tpl in (EAIR_TPL_G, EAIR_TPL_A):
            res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED, mask=EAIR_MASK)
            sc.append(float(np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0).max()))
        g, a = sc
        if a >= self.EAIR_MIN and a - g >= self.EAIR_MARGIN:
            return 'A'
        if g >= self.EAIR_MIN and g - a >= self.EAIR_MARGIN:
            return 'G'
        return None

    def eair_box(self):
        """跑轴记录用的框(E 图标)。"""
        (cx, cy), half = self.EAIR_CENTER, self.EAIR_HALF
        try:
            return self.task.box_of_screen_scaled(3840, 2160, cx - half, cy - half, cx + half, cy + half, hcenter=True)
        except Exception:
            return None

    def in_air(self):
        return self.air_state == 'A' and time.time() - self.air_at <= self.EAIR_FRESH

    def tpl_score(self, name):
        """框架模板在当前帧的分数(框是模板自带的)。读不了返回 None。"""
        try:
            hit = self.task.find_one(name, threshold=0.3)
        except Exception:
            return None
        return float(hit.confidence) if hit else 0.0

    def iai_update(self, on_field, now):
        """绯雪在场的每一帧: 读闪避键披风 + 枯霜就绪图标(判据) + 两颗徽记(日志)。"""
        if not on_field:
            return
        cape = self.tpl_score(self.CAPE_TPL)
        if cape is not None:
            self.cape_lit = cape >= self.CAPE_LIT
            self.cape_at = now
        score = self.tpl_score(self.KUS_TPL)
        if score is not None:
            lit = score >= self.KUS_LIT
            self.kus_reads += 1
            if lit == self.kus_raw:
                self.kus_hits += 1
            else:
                self.kus_raw = lit
                self.kus_hits = 1
            if self.kus_hits >= self.KUS_CONFIRM and lit != self.kus_lit:
                self.kus_lit = lit
                if lit:
                    self.kus_lit_by = self.iai_k
                    self.kus_lit_at = now
                    since = now - self.iai_watch['t'] if self.iai_watch else -1
                    note = f'枯霜亮(居{self.iai_k}后{since:.2f},{score:.2f})'
                else:
                    self.kus_off_at = now
                    note = '枯霜灭'
                self.macro_marks.append(f'{note}@{now - self.macro_t0:.2f}')
                self.axis_mark('枯霜就绪', round(score, 2))
        air = self.air_read()
        if air is not None:
            if air == self.air_raw:
                self.air_hits += 1
            else:
                self.air_raw = air
                self.air_hits = 1
            self.air_at = now
            if self.air_hits >= self.EAIR_CONFIRM and air != self.air_state:
                self.air_state = air
                if air == 'A':
                    self.air_since = now
                    self.air_up_at = now
                else:
                    self.land_at = now
                self.axis_mark('空中' if air == 'A' else '地面', 1, self.eair_box())
        rd = self.iai_read()
        if rd is None:
            return
        c, blue, _ = rd
        if c == self.iai_last_raw:
            self.iai_hits += 1
        else:
            self.iai_last_raw = c
            self.iai_hits = 1
        if self.iai_hits >= self.IAI_CONFIRM:
            self.iai_blue = blue
            if c != self.iai_c:
                if 0 <= c < self.iai_c:
                    self.iai_drop_at = now
                self.iai_c = c
                self.axis_mark('居合徽记', c, self.iai_mark_box())
        w = self.iai_watch
        if w is not None and not w.get('done') and now - w['t'] >= self.IAI_WATCH:
            self.iai_note(w)

    def iai_begin(self, tag, at=None, pre=None):
        w = self.iai_watch
        if w is not None and not w.get('done'):
            self.iai_note(w)
        self.iai_k += 1
        self.iai_watch = {'k': self.iai_k, 't': time.time() if at is None else at,
                          'pre': self.iai_c if pre is None else pre, 'blue': self.iai_blue, 'tag': tag}
        self.axis_mark('居合', self.iai_k, self.iai_mark_box())

    def iai_da_gate(self, tag):
        """表上 'iaiw' 那刀居合: 离进架势的闪避不到 IAI_DA_MIN 就等够再按。返回等了多久(整段顺延)。"""
        wait = self.dodge_at + self.IAI_DA_MIN - time.time()
        if wait <= 0.0:
            return 0.0
        self.idle_until(time.time() + wait)
        self.macro_mark(f'{tag}居合等架势+{wait:.2f}')
        self.axis_mark('居合等架势', round(wait, 2))
        return wait

    def iai_note(self, w):
        """记一笔: 第几次居合, 按下前后徽记几颗(蓝/淡)。不判出没出。"""
        w['done'] = True
        note = f'居{w["k"]}徽{w["pre"]}{"" if w["blue"] else "淡"}->{self.iai_c}'
        self.iai_log.append(note)
        self.macro_marks.append(f'{note}@{w["t"] - self.macro_t0:.2f}')

    def dodge_gate(self, tag):
        """进架势的那下闪避: 闪避键是披风才进得了架势。到点时最近的读数亮着就照表按(不多等);
        不亮就边读边等, 过 DGATE_A_AFTER 还不亮补一下普攻(落华下落里被吞就隔 DGATE_A_EVERY 再补), 亮了再按,
        最多 DGATE_CAP。没徽记(寒意不到 100)披风亮不了, 不等。人在空中先等落地(ground_gate)。返回等了多久(整段顺延)。"""
        landed = self.ground_gate(tag)
        now = time.time()
        if self.cape_lit and now - self.cape_at <= self.DGATE_FRESH:
            return landed
        if self.iai_c == 0:
            self.macro_mark(f'{tag}闪避门:没徽记')
            return landed
        start = now
        lit = False
        taps = 0
        next_tap = start + self.DGATE_A_AFTER
        while time.time() - start < self.DGATE_CAP:
            self.poll()
            if self.cape_lit and self.cape_at >= start:
                lit = True
                break
            # 左键被表按着(居合/枯霜按住中)时不补, 免得松掉
            if self.DGATE_A and time.time() >= next_tap and 'LMB' not in self.held:
                self.tap_now('LMB', self.DGATE_A_HOLD)
                taps += 1
                next_tap = time.time() + self.DGATE_A_EVERY
        waited = time.time() - start
        self.macro_mark(f'{tag}闪避门:{"亮" if lit else "没亮"}{f",补A{taps}" if taps else ""}+{waited:.2f}')
        self.axis_mark('闪避门', round(waited, 2))
        return landed + waited

    def ground_gate(self, tag):
        """人在空中(E 图标是落华样式)就等落地再闪, 最多 GROUND_CAP; 在地上或读不出来不等。返回等了多久。
        落地只要一帧地面读数就放行(不等连 2 帧): 00:39 第 2 刀后那下闪避(32.87)就贴着落地(32.9)按的。"""
        if not (self.GROUND_GATE and self.in_air()):
            return 0.0
        start = time.time()
        # 在空中已经超过正常跳跃的时长 = 站在怪身上(没冻住的宝箱), 等不到落地
        until = min(start + self.GROUND_CAP, self.air_since + self.AIR_LAND_MAX)
        if until <= start:
            self.macro_mark(f'{tag}等落地:空中{start - self.air_since:.2f}s不等')
            return 0.0
        landed = False
        while time.time() < until:
            self.poll()
            if self.air_raw == 'G' and self.air_at >= start:
                landed = True
                break
        waited = time.time() - start
        self.macro_mark(f'{tag}等落地:{"落了" if landed else "没落"}+{waited:.2f}')
        self.axis_mark('等落地', round(waited, 2))
        return waited

    def land_catchup(self, early, due, tag):
        """表上 'Dl': 前面顺延了, 从手打那一刻(early)起边读边等, 跳起来后读到过空中、现在认定落地、闪避键是披风 -> 马上按,
        顺延追回来; 到 due 还没齐就照原样走(后面的 dgate 等落地/披风)。返回新的 shift。"""
        self.idle_until(early)
        while True:
            now = time.time()
            if now >= due:
                return due - early
            if (self.air_up_at > self.jump_at > 0.0 and self.air_state == 'G' and now - self.air_at <= self.EAIR_FRESH
                    and self.cape_lit and now - self.cape_at <= self.DGATE_FRESH):
                back = due - now
                self.macro_mark(f'{tag}落地追回{back:.2f}')
                self.axis_mark('落地追回', round(back, 2))
                return now - early
            self.poll()

    def land_hit_wait(self, early, due, tag):
        """表上 'Dh': 跳起来、按着左键落地之后那下进架势的闪避, 等落地那一下打出来(给寒意)再按。
        按点 = 认定落地 + DL_HIT_GAP, 不早于手打那一刻(early): 比 due 早 = 前面的顺延追回来, 比 due 晚(落地晚了)也等。
        这一跳没按(跳被跳过)、到点还没读到空中, 或读到空中后到 due + DL_HIT_WAIT 还没认定落地, 就照表走。
        等到按点才返回(后面的 dgate 在按点看披风)。返回新的 shift。"""
        jump = self.jump_at
        if not (jump > 0.0 and jump >= early - self.DL_HIT_JUMP):
            return due - early
        while True:
            now = time.time()
            up = self.air_up_at > jump
            if up and self.land_at > self.air_up_at:
                at = max(self.land_at + self.DL_HIT_GAP, early)
                d = at - due
                self.macro_mark(f'{tag}落地后闪({"追回" if d < 0 else "多等"}{abs(d):.2f})')
                self.axis_mark('落地后闪', round(d, 2))
                self.idle_until(at)
                return max(at, time.time()) - early
            if now >= (due + self.DL_HIT_WAIT if up else due):
                self.macro_mark(f'{tag}落地后闪:{"没认到落地" if up else "没读到空中"}')
                return now - early
            self.poll()

    def jump_floor(self, early, due, gap, tag):
        """表上 'Jc3'(居 1 后那下跳): 按点 = max(手打那一刻, 进架势那下闪避 + gap), 比表晚就往后推(还没出手就跳会把居合顶掉),
        比表早就追回。返回新的顺延。"""
        at = max(early, self.dodge_at + gap)
        if at > due + 0.005:
            self.macro_mark(f'{tag}跳等出手+{at - due:.2f}')
            self.axis_mark('跳等出手', round(at - due, 2))
        elif at < due - 0.005:
            self.macro_mark(f'{tag}跳追回{due - at:.2f}')
            self.axis_mark('跳追回', round(due - at, 2))
        return at - early

    def jump_after_iai(self, early, due, tag):
        """表上 'Jk'(6 居循环 变奏入场第 3 刀连续居合后那下跳): 等枯霜就绪亮(= 第 3 刀出手)再跳, 不早于表。
        第 3 刀按下 IAIK_RETAP 还没亮补按一下普攻; 过 IAIK_CAP 还不亮、或者没徽记(寒意不够出不了)就照按。返回新的顺延。"""
        if self.kus_lit or self.iai_c == 0:
            return due - early
        w = self.iai_watch
        pressed = w['t'] if w else time.time()
        retap = False
        while not self.kus_lit and time.time() < pressed + self.IAIK_CAP:
            if not retap and time.time() >= pressed + self.IAIK_RETAP and 'LMB' not in self.held:
                self.tap_now('LMB', self.KEY_TAP)
                retap = True
            self.poll()
        lit = self.kus_lit
        at = max(due, self.kus_lit_at if lit else time.time())
        note = f'{"出了" if lit else "没出"}{",补A" if retap else ""}'
        self.macro_mark(f'{tag}跳等居合:{note}+{max(0.0, at - due):.2f}')
        self.axis_mark('跳等居合', round(at - due, 2))
        return at - early

    def hold_after_kus(self, early, due, tag):
        """表上 'Ah'(6 居循环 枯霜后 A A 按住A 那下按住): 按在 max(表, 枯霜灭 + HOLDK_OFS), 让 A2 收完先出 A3 再转重击。
        到点还亮着最多等 HOLDK_CAP 灭; 枯霜按下后一直没灭过就按 表 + HOLDK_FALLBACK。返回新的顺延。"""
        pressed = self.kus_press_at
        if pressed > 0.0 and self.kus_off_at <= pressed:
            while self.kus_lit and time.time() < due + self.HOLDK_CAP:
                self.poll()
        if pressed > 0.0 and self.kus_off_at > pressed:
            at = max(due, self.kus_off_at + self.HOLDK_OFS)
            how = f'灭+{at - self.kus_off_at:.2f}'
        else:
            at = max(due + self.HOLDK_FALLBACK, time.time())
            how = '没等到灭'
        self.macro_mark(f'{tag}重击等A2收:+{at - due:.2f}({how})')
        self.axis_mark('重击等A2收', round(at - due, 2))
        return at - early

    def jump_catchup(self, early, due, gap, tag):
        """表上 'Jc'(居合后那下跳): 前面顺延了就往前追, 按点 = max(手打那一刻, 进架势那下闪避 + gap)。返回新的顺延。"""
        at = max(early, self.dodge_at + gap)
        if at >= due:
            return due - early
        self.macro_mark(f'{tag}跳追回{due - at:.2f}')
        self.axis_mark('跳追回', round(due - at, 2))
        return at - early

    def kus_gate(self, tag):
        """枯霜之前淬寒要满 3 层(枯霜就绪图标亮)。到点亮着就按; 不亮再等 KUS_GRACE;
        还不亮、而且这段读得到(明确没满)、还有徽记, 才补 闪避->居合。返回花掉的时间(整段顺延)。"""
        start = time.time()
        reads0 = self.kus_reads
        while not self.kus_lit and time.time() - start < self.KUS_GRACE:
            self.poll()
        rescued = 0
        while (not self.kus_lit and self.KUS_RESCUE and rescued < self.KUS_RESCUE_MAX
               and self.kus_reads - reads0 >= self.KUS_GRACE_READS):
            if self.iai_c == 0:
                self.macro_mark(f'{tag}补居合:没徽记(寒意不够)')
                break
            self.dodge_gate(f'{tag}补')
            self.tap_now('RMB', self.KUS_D_HOLD)
            self.idle_until(time.time() + self.KUS_D_TO_A - self.KUS_D_HOLD)
            self.iai_begin(f'{tag}补')
            a_at = time.time()
            self.tap_now('LMB', self.KUS_A_HOLD)
            rescued += 1
            reads0 = self.kus_reads
            while not self.kus_lit and time.time() < a_at + self.KUS_A_TO_NEXT:
                self.poll()
            # 补完亮了: 跟手打一样隔 0.83s 再按枯霜(居合出手动作没收完就按会被吞)
            self.idle_until(a_at + self.KUS_A_TO_NEXT)
        wait = time.time() - start
        if self.kus_lit:
            state = f'亮(居{self.kus_lit_by})'
        elif self.kus_reads == reads0:
            state = '读不到'
        else:
            state = '没亮'
        self.macro_mark(f'{tag}枯霜门:{state}{f",补{rescued}" if rescued else ""}(+{wait:.2f})')
        if not self.kus_lit:
            self.logger.info(f'{tag}: 枯霜前就绪图标{state}(补了 {rescued} 刀), 照按 — {" ".join(self.iai_log)}')
        self.iai_kus.append(f'{state}{f"补{rescued}" if rescued else ""}')
        if rescued and not getattr(self, 'round_opener', True):
            self.kus_lag = self.lp('KUS_RESCUE_LAG')
        return wait

    def brk(self, due, tag, mode=None):
        """到点有处决提示就按 F; 这一帧没读到但 BREAK_RECENT 内看到过也按; 都没有就再看 F_LATE 秒。
        表上写了 'seen' 的那格到点前刚看到过就直接按, 不先读这一帧; 写了 'A' 的那格到点读一次就定, 没有就连点普攻(brk_spam)。
        返回晚按了多少(整段顺延)。"""
        end = due + (self.LP_F_LATE if mode == 'A' else self.F_LATE)
        quick = mode == 'seen' and self.BRK_SEEN_QUICK
        recent = self.BREAK_RECENT
        if mode == 'A' and self.tf_gone_at < self.brk_seen_at:
            recent = self.BREAK_RECENT_A
        while True:
            if quick and time.time() - self.brk_seen_at <= self.BREAK_RECENT:
                seen = False
            else:
                self.poll()
                seen = self.break_prompt()
            quick = False
            now = time.time()
            if seen or now - self.brk_seen_at <= recent:
                late = max(0.0, now - due)
                self.task.send_key('f', down_time=self.F_HOLD)
                self.task.can_break = False
                how = '' if seen else f',{now - self.brk_seen_at:.2f}s前看到过'
                self.brk_seen_at = -99.0
                self.macro_mark(f'{tag}处决(+{late:.2f}{how})')
                self.axis_mark('处决', round(late, 2))
                return late
            if now >= end:
                break
        self.macro_mark(f'{tag}处决跳过(没提示)')
        if mode == 'A' and self.lp('BRK_TO_R2'):
            return self.brk_to_r2(due, tag)
        if mode == 'A' and self.BRK_SPAM > 0:
            return self.brk_spam(due, tag)
        return 0.0

    def r2_ready(self):
        try:
            return bool(self.liberation_available())
        except Exception:
            return False

    def brk_to_r2(self, due, tag):
        """6 居循环没处决: 不补救。徽记亮着(或等上一刀命中回寒意亮回来)补按一下 A 接连续居合, 然后停掉主段直接 R2
        (run_round 里照 r2_wait_cut 等扣寒意); 不亮就不补直接 R2。R 不亮才照旧连点(brk_spam)。返回顺延(这里都是 0)。"""
        self.poll()
        if not self.r2_ready():
            self.macro_mark(f'{tag}没处决:R不亮,照旧连点')
            return self.brk_spam(due, tag)
        w = self.iai_watch
        prev = w['t'] if w else time.time()
        while time.time() < prev + self.IAIC_CAP:
            dropped = self.iai_drop_at > prev
            if self.iai_c >= 1 and (dropped or time.time() >= prev + self.IAIC_DROP_WAIT):
                break
            self.poll()
        self.main_abort = True
        if self.iai_c >= 1:
            self.iaic_skip = False
            self.iai_begin(tag)
            at = time.time()
            self.tap_now('LMB', self.KEY_TAP)
            self.lmb_at = at
            how = f'补连续居合(上一刀+{at - prev:.2f})'
        else:
            self.iaic_skip = True
            how = f'徽记没亮(上一刀+{time.time() - prev:.2f}),不补'
        self.macro_mark(f'{tag}没处决直接R2:{how}')
        self.axis_mark('没处决直接R2', 0 if self.iaic_skip else 1)
        return 0.0

    def brk_spam(self, due, tag):
        """处决跳过: 处决那段时间连点普攻 BRK_SPAM 秒, 不站着。返回超出 BRK_SPAM_FIT(循环轴 5 居/6 居各一份)的那截(整段顺延)。"""
        start = time.time()
        end = start + self.BRK_SPAM
        n = 0
        while time.time() + self.KEY_TAP <= end:
            at = time.time()
            self.tap_now('LMB', self.KEY_TAP)
            self.lmb_at = at
            n += 1
            self.idle_until(min(at + self.BRK_SPAM_EVERY, end))
        over = max(0.0, time.time() - due - self.lp('BRK_SPAM_FIT'))
        self.macro_mark(f'{tag}处决跳过连点A(x{n},{time.time() - start:.2f}s,顺延{over:.2f})')
        self.axis_mark('处决跳过连点A', n)
        return over

    # ==================== 转火 ====================

    def tf_reset(self):
        self.tf_on = False
        self.tf_read_at = 0.0
        self.tf_reads = 0
        self.tf_absent = 0             # 连续读不到怪的次数
        self.tf_absent_at = 0.0        # 这一串读不到的第一次
        self.tf_gone_at = -99.0        # 最近一次认定没怪了(转火)的时刻
        self.tf_seen = 0               # 连续读到怪的次数
        self.tf_locked = None          # 最近一次锁定图标: True 锁着 / False 没锁 / None 两个图标都没读到
        self.tf_unlocked = 0           # 连续"有怪没锁"的次数
        self.tf_mmb_at = -99.0
        self.tf_mmb_fail = 0           # 按了中键还没锁上的次数
        self.tf_waiting = False        # 等新怪时中键归 tf_gate 管

    def target_locked(self):
        """锁定图标: True 锁着 / False 没锁 / None 都没读到。照 CombatCheck.has_target() 的前三个读框,
        去掉它最后那个兜底分支(会 sleep(1) 再连按两次 ESC, 不能在宏里调)。"""
        t = self.task
        try:
            has_name, no_name = t.get_target_names()
            for box in (t.get_box_by_name(has_name).scale(1.1), t.get_box_by_name('box_target_enemy_long'),
                        t.get_box_by_name('target_box_long2')):
                best = t.find_best_match_in_box(box, [has_name, no_name], threshold=0.6)
                if best:
                    return best.name == has_name
        except Exception:
            return None
        return None

    def enemy_seen(self):
        """Boss 破盾图标 / 血条。读不了当有怪(宁可不等)。"""
        t = self.task
        try:
            if t.find_one('boss_break_shield') or t.find_one('boss_break_lock'):
                return True
            return bool(t.has_health_bar())
        except Exception:
            return True

    def warm_templates(self):
        """框架模板第一次用到才从磁盘加载(每个 ~70ms, 10-30 23:36 启动轴中途就卡过一次)。进宏先把这条轴要读的都过一遍,
        免得卡在按键上。换分辨率框架会清空, 跟着重来。"""
        try:
            size = tuple(self.task.frame.shape[:2])
        except Exception:
            return
        if self.warmed == size:
            return
        self.warmed = size
        t0 = time.time()
        self.target_locked()
        self.enemy_seen()
        for name in (self.CAPE_TPL, self.KUS_TPL, 'f_break'):
            self.tpl_score(name)
        self.logger.info(f'hls: 模板预热 {(time.time() - t0) * 1000:.0f}ms')

    def tf_gone(self):
        return self.tf_absent >= self.TF_GONE_CONFIRM and self.tf_read_at - self.tf_absent_at >= self.TF_GONE_SPAN

    def tf_update(self, now):
        """读一次有没有怪(只在队伍 HUD 在场时调)。有怪、锁定图标明确是没锁 -> 按中键锁上。"""
        locked = self.target_locked()
        present = locked is True or self.enemy_seen()
        gone_before = self.tf_gone()
        self.tf_read_at = now
        self.tf_reads += 1
        self.tf_locked = locked
        if present:
            if gone_before:
                self.macro_mark(f'有怪了(空了{now - self.tf_absent_at:.1f}s)')
                self.axis_mark('转火', '有怪')
            self.tf_absent = 0
            self.tf_seen += 1
        else:
            if self.tf_absent == 0:
                self.tf_absent_at = now
            self.tf_absent += 1
            self.tf_seen = 0
            if not gone_before and self.tf_gone():
                self.tf_gone_at = now
                self.macro_mark('没怪了')
                self.axis_mark('转火', '没怪')
        if locked is True:
            if self.tf_mmb_fail:
                self.macro_mark(f'锁上了(中键x{self.tf_mmb_fail})')
            self.tf_unlocked = 0
            self.tf_mmb_fail = 0
        elif present and locked is False and not self.tf_waiting:
            self.tf_unlocked += 1
            gap = self.TF_LOCK_SLOW if self.tf_mmb_fail >= self.TF_LOCK_GIVEUP else self.TF_LOCK_GAP
            if self.TF_KEEP_LOCK and self.tf_unlocked >= self.TF_LOCK_CONFIRM and now - self.tf_mmb_at >= gap:
                self.click_middle()
                self.tf_mmb_fail += 1
                self.tf_unlocked = 0
                if self.tf_mmb_fail <= self.TF_LOCK_GIVEUP:
                    self.macro_mark('中键锁怪')
        else:
            self.tf_unlocked = 0
        return present

    def click_middle(self):
        """中键 = 锁定目标。走 SendInput: PostMessage 版会投 WM_ACTIVATE, 把按着的左键打断。"""
        if self.use_si and si_mouse('middle', False):
            time.sleep(0.03)
            if not si_mouse('middle', True):
                self.logger.warning('SendInput 中键松开没发出去')
        else:
            try:
                self.task.middle_click()
            except Exception:
                pass
        self.tf_mmb_at = time.time()

    def tf_gate(self, tag, cap):
        """场上没怪就等新怪(按中键找), 最多 cap 秒。真等了才返回等了多久; 有怪/HUD 不在/读不了返回 0。"""
        if not (self.TF_ON and self.tf_on):
            return 0.0
        start = time.time()
        if self.tf_absent == 0 and self.tf_seen > 0 and start - self.tf_read_at <= self.TF_READ_GAP + 0.1:
            return 0.0
        # 先确认: 读到怪就走, 连读不到够数够久才等
        while not self.tf_gone():
            if time.time() - start > self.TF_CONFIRM_CAP:
                return 0.0
            n = self.tf_reads
            self.poll(tf_gap=self.TF_CONFIRM_GAP)
            if self.tf_reads != n and self.tf_absent == 0:
                return 0.0
        self.macro_mark(f'{tag}没怪,等新怪')
        self.tf_waiting = True
        clicks = 0
        click_at = -99.0
        how = None
        try:
            while time.time() - start < cap:
                now = time.time()
                # 只在刚读过、锁定图标明确是没锁时按(图标读不到时按下去可能把锁解掉)
                if (self.tf_locked is False and now - self.tf_read_at <= 0.2
                        and self.tf_read_at - click_at >= 0.15 and now - click_at >= self.TF_WAVE_CLICK):
                    self.click_middle()
                    clicks += 1
                    click_at = time.time()
                self.poll(tf_gap=self.TF_CONFIRM_GAP)
                if self.tf_locked is True:
                    how = '锁上'
                    break
                if self.tf_seen >= self.TF_SEEN_CONFIRM:
                    how = '看到'
                    break
        finally:
            self.tf_waiting = False
        waited = time.time() - start
        self.macro_mark(f'{tag}等新怪:{how or "没来"}+{waited:.2f}(中键x{clicks})')
        self.axis_mark('等新怪', round(waited, 2))
        return waited

    # ==================== 大招 ====================

    def fire_lib(self, char, tag, cap, every, hold=None):
        """char 在场时连按 R 到 HUD 连续消失。返回第一帧消失的时刻, 放不出来返回 None。"""
        hold = self.KEY_TAP if hold is None else hold
        self.need_field(tag)
        if self.cur is not char:
            raise MacroBroken(f'{tag}: 场上是 {getattr(self.cur, "name", None)} 不是 {char.name}')
        start = time.time()
        n = gone = 0
        gone_at = None
        next_press = start
        while time.time() - start < cap:
            if gone == 0 and time.time() >= next_press:
                self.tap_now('R', hold)
                n += 1
                next_press = time.time() + max(0.0, every - hold)
            if not self.poll():
                gone += 1
                if gone_at is None:
                    gone_at = time.time()
                if gone >= self.LIB_GONE_CONFIRM:
                    self.task.skip_combat_check = True
                    try:
                        char.record_liberation_use()
                    except Exception:
                        pass
                    self.macro_mark(f'{tag}(出手,x{n},{gone_at - start:.2f}s)')
                    return gone_at
            else:
                gone = 0
                gone_at = None
        try:
            lit = bool(char.liberation_available())
        except Exception:
            lit = 'err'
        self.macro_mark(f'{tag}没出(x{n})')
        self.logger.warning(f'{tag}: {char.name} 的 R 按了 {n} 次没进演出 (图标亮={lit})')
        return None

    def fire_r2(self, tag, hold):
        """绯雪 R2。点按版(hold 短)按完 R2_TAP_WINDOW 内看 HUD 消失; 按住版【整段按住不重发】, 松手后 R2_HOLD_WINDOW 内看。
        hold=None: 一直按住到游戏自己放(HUD 连续消失才松, 最多 R2_AUTO_CAP)。
        没进演出就整个再来一次, 最多 R2_TRIES 次。返回第一帧消失的时刻或 None。"""
        self.need_field(tag)
        if self.cur is not self:
            raise MacroBroken(f'{tag}: 场上是 {getattr(self.cur, "name", None)} 不是绯雪')
        auto = hold is None
        window = self.R2_TAP_WINDOW if not auto and hold < 0.3 else self.R2_HOLD_WINDOW
        for i in range(self.R2_TRIES):
            self.press('R', False)
            t_down = time.time()
            gone = 0
            gone_at = None
            try:
                end = t_down + (self.R2_AUTO_CAP if auto else hold)
                while time.time() < end:
                    left = end - time.time()
                    if left >= self.READ_MIN_GAP:
                        if not self.poll():
                            gone += 1
                            gone_at = gone_at or time.time()
                            if auto and gone >= self.LIB_GONE_CONFIRM:
                                break
                        else:
                            gone = 0
                            gone_at = None
                    else:
                        time.sleep(max(0.0, left))
            finally:
                self.press('R', True)
            if auto:
                self.macro_mark(f'{tag}R2按住{time.time() - t_down:.2f}s{"自己放了" if gone >= self.LIB_GONE_CONFIRM else "没放,松手"}')
            if gone < self.LIB_GONE_CONFIRM:
                gone_at = self.wait_hud_gone(f'{tag}R2{"" if i == 0 else f"重按#{i}"}', time.time() + window)
            if gone_at is not None:
                self.task.skip_combat_check = True
                try:
                    self.record_liberation_use()
                except Exception:
                    pass
                return gone_at
        self.logger.warning(f'{tag}: R2 按了 {self.R2_TRIES} 次没进演出')
        return None

    def hy_finish_form(self, tag):
        """上一圈断在 R1 之后(还在预求身): 先把 R2 放掉再开新一圈, 免得前段的 R1 按成 R2。"""
        self.macro_mark(f'{tag}还在预求身,先放R2')
        g = self.fire_r2(f'{tag}收尾', self.FIN_R2_HOLD)
        if g is not None:
            self.wait_hud_back(f'{tag}收尾R2演出', g, self.R2_ANIM_CAP, self.R2_MIN_GONE, confirm=2)
        else:
            self.logger.warning(f'{tag}: 预求身收尾的 R2 没放出来, 当作已经变回去了')
        self.hy_form = 'base'

    # ==================== 一圈 ====================

    def lp(self, name):
        """循环轴的表/时刻: LOOP_KIND='6居' 且 6 居表写完了, 有 L6_ 那份就用 L6_ 的, 没有回落到 LP_。"""
        if self.LOOP_KIND == '6居' and self.L6_R2_AT is not None and hasattr(self, 'L6_' + name):
            return getattr(self, 'L6_' + name)
        return getattr(self, 'LP_' + name)

    def run_round(self, opener, anchor, tag):
        """前段 -> R1 -> 穗穗 R -> 洛瑟菈 E Q R -> 断舍离 -> 绯雪主段 -> R2 演完。"""
        marks = len(self.macro_marks)
        self.round_opener = opener
        self.timeline_logged = False
        self.worst_late = 0.0
        su = self.teammate('Suisui')
        luc = self.teammate('Lucilla')

        # ---- 前段(左键一路按着) ----
        self.shift = 0.0
        self.play(self.OP_PRE if opener else self.lp('PRE'), anchor, f'{tag}前')
        self.idle_until(anchor + (self.OP_R1_AT if opener else self.lp('R1_AT')) + self.shift)
        if self.hy_form == 'yq':
            raise MacroBroken('绯雪还在预求身, 这一下 R 会放成 R2')
        g = self.fire_lib(self, f'{tag}R1', self.R1_CAP, self.R1_EVERY)
        if g is None:
            raise MacroBroken('R1(见心)没放出来')
        self.hy_form = 'yq'
        h1 = self.wait_hud_back(f'{tag}R1演出', g, self.R1_ANIM_CAP, self.R1_MIN_GONE)

        # ---- R1 演完: 绯雪 E -> 穗穗 (Q) R ----
        self.shift = 0.0
        self.play(self.OP_POST if opener else self.lp('POST'), h1, f'{tag}穗')
        self.idle_until(h1 + (self.OP_SU_R_AT if opener else self.lp('SU_R_AT')) + self.shift)
        g = self.fire_lib(su, f'{tag}穗R', self.SU_R_CAP, self.SU_R_EVERY)
        if g is None:
            self.press('LMB', True)
            h2 = time.time()
        else:
            self.idle_until(g + self.SU_LMB_UP)
            self.press('LMB', True)
            h2 = self.wait_hud_back(f'{tag}穗R演出', g, self.SU_R_ANIM_CAP, self.SU_R_MIN_GONE)

        # ---- 洛瑟菈: 长按 E -> Q R ----
        self.shift = 0.0
        self.play(self.OP_LU if opener else self.LP_LU, h2, f'{tag}洛')
        self.idle_until(h2 + (self.OP_LU_R_AT if opener else self.LP_LU_R_AT) + self.shift)
        g = self.fire_lib(luc, f'{tag}洛R', self.LU_R_CAP, self.LU_R_EVERY)
        if g is None:
            lur = time.time()
        else:
            lur = self.wait_hud_back(f'{tag}洛R演出', g, self.LU_R_ANIM_CAP, self.LU_R_MIN_GONE)

        # ---- 洛瑟菈演完: A A -> 绯雪 A 按住 跳 -> 按着切回洛瑟菈 = 断舍离 ----
        self.shift = 0.0
        self.play(self.LUR, lur, f'{tag}断')
        g = self.wait_hud_gone(f'{tag}断舍离', lur + self.LUR_SW_BACK + self.shift + self.DSL_GONE_CAP)
        if g is None:
            self.press('LMB', True)
            t_main = time.time()
        else:
            self.idle_until(g + self.DSL_LMB_UP)
            self.press('LMB', True)
            # 演出还没完就按切绯雪(游戏缓存到演完才切), 主段从这一下起算; 等读到 HUD 回来再按会整段晚 0.05s
            t_main = g + self.DSL_SW_AT
            self.idle_until(t_main)
            self.add_freeze_duration(g, self.DSL_SW_AT)

        # ---- 绯雪主段(变奏入场) ----
        self.release_all()
        self.iai_reset()
        self.iai_on = True
        self.sw_press('Hiyuki', f'{tag}绯')
        if g is not None:
            self.sw_blind = True
        # 主段按绯雪真正上场那一刻起算: 变奏后那下 A 要落在变奏的接招窗口里才是预求身第 2 段, 下一下才是第 3 段(冰环)
        self.need_field(f'{tag}绯')
        late = time.time() - self.MAIN_ARRIVE_OFS - t_main
        if late > 0.0:
            t_main += late
            self.macro_mark(f'{tag}绯晚到{late:.2f}')
        if not opener:
            t_main += self.lp('MAIN_OFS')
        self.shift = 0.0
        self.play(self.op_main_rows() if opener else self.lp('MAIN'), t_main, f'{tag}绯')
        r2_due = t_main + (self.OP_R2_AT if opener else self.lp('R2_AT')) + self.shift
        if opener:
            # R2 前那次「有没有怪」到点才现读会晚一帧(11-01 17:04 晚 0.016s), 提前读掉, 到点直接按
            self.idle_until(r2_due - self.R2_PRECHECK)
            self.poll(tf_gap=0.0)
            self.tf_gate(f'{tag}R2前', self.TF_R2_WAIT)
            r2_due = max(r2_due, time.time())
        elif self.main_abort:
            # 没处决直接 R2: 补了连续居合就跟最后一刀一样等扣寒意(不早于按下 +0.17, 表上的间隔), 没补直接放
            r2_due = time.time() if self.iaic_skip else self.lmb_at + (self.lp('R2_AT') - self.lp('MAIN')[-1][0])
            r2_due = self.r2_wait_cut(r2_due, f'{tag}绯')
        elif self.lp('R2_CUT'):
            r2_due = self.r2_wait_cut(r2_due, f'{tag}绯')
        self.idle_until(r2_due)
        if self.iai_watch is not None and not self.iai_watch.get('done'):
            self.iai_note(self.iai_watch)
        self.iai_on = False
        # 这一只在主段里死了: R2 留给下一只
        self.tf_gate(f'{tag}R2前', self.TF_R2_WAIT)
        g = self.fire_r2(tag, self.OP_R2_HOLD if opener else self.lp('R2_HOLD'))
        iai_note = (f'居合按了 {self.iai_k} 次(枯霜门 {"/".join(self.iai_kus) or "-"}): '
                    f'{" ".join(self.iai_log)}')
        if g is None:
            self.logger.warning(f'{tag}: R2 没放出来 — {iai_note}')
            self.r2_back_at = time.time()
        else:
            self.hy_form = 'base'
            self.r2_back_at = self.wait_hud_back(f'{tag}R2演出', g, self.R2_ANIM_CAP, self.R2_MIN_GONE, confirm=2)
            self.logger.info(f'{tag}: {iai_note}')
        # R2 打死了这一只: 等下一只刷出来再开下一圈; 等过就让下一圈从调进来那一刻起算
        if self.tf_gate(f'{tag}收尾', self.TF_ROUND_WAIT) > 0:
            self.r2_back_at = -99.0
        self.logger.info(f'{tag} round timeline (最晚 {self.worst_late:.2f}s): {self.macro_timeline(marks)}')
        self.timeline_logged = True

    def macro_enter(self, entry_char):
        if self.teammate('Lucilla') is None or self.teammate('Suisui') is None:
            return None
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = entry_char or self.on_field_char() or self
        for char in self.task.chars:
            if char is not None:
                char.check_f_on_switch = False
        self.use_si = self.game_foreground()
        if not self.use_si:
            self.logger.warning('hls: 游戏窗口不在前台, 按键退回 PostMessage(左键按着跨切人会失效)')
        self.held = {}
        self.sw_target = None
        self.shift = 0.0
        self.macro_start()
        self.timeline_logged = False
        self.hud_seen_at = time.time()
        self.hud_watch = True
        self.tf_reset()
        self.warm_templates()
        self.tf_on = True
        return previous

    def macro_exit(self, previous):
        # 按住的键跨了好几个方法, 中途抛异常的话会一直按着。这里是唯一保证松手的地方
        self.release_all()
        self.iai_on = False
        self.sw_target = None
        if not self.timeline_logged and self.macro_marks:
            self.logger.info(f'hls timeline (没跑完): {self.macro_timeline()}')
            self.timeline_logged = True
        self.hud_watch = False
        self.tf_on = False
        self.tf_waiting = False
        self.task.skip_combat_check = previous
        self.mark_busy(False)

    def perform_opener(self, entry_char=None):
        """启动轴: 穗穗在场按下左键起算。返回时 R2 演完、绯雪在场。"""
        previous = self.macro_enter(entry_char)
        if previous is None:
            return False
        self.logger.info(f'hls opener entry: {self.cur.name}')
        try:
            self.loop_count = 0
            self.hy_form = 'base'
            if self.cur is not self.teammate('Suisui'):
                self.sw_press('Suisui', 'op')
                self.need_field('op')
                # 表上 0.654s 就切回绯雪: 起算点往后挪, 让那一下落在绯雪的切人冷却之后
                self.macro_wait(max(0.15, self.SW_COOLDOWN + 0.05 - self.OP_PRE[1][0]))
            self.run_round(True, time.time(), 'op')
            self.logger.info(f'hls opener total {time.time() - self.macro_t0:.2f}s')
        except MacroBroken as e:
            self.logger.warning(f'hls opener broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    def perform_loop(self, entry_char=None):
        """循环轴一圈: 绯雪在场(上一圈 R2 演完)起算。"""
        previous = self.macro_enter(entry_char)
        if previous is None:
            return False
        try:
            self.loop_count += 1
            tag = f'L{self.loop_count}'
            fresh = time.time() - self.r2_back_at < self.LP_ANCHOR_FRESH
            if self.cur is not self:
                self.sw_press('Hiyuki', tag)
                self.need_field(tag)
                fresh = False
            if self.hy_form == 'yq':
                self.hy_finish_form(tag)
                fresh = False
            if self.tf_gate(f'{tag}圈首', self.TF_ROUND_WAIT) > 0:
                fresh = False
            # 断过就从现在起算: 锚点往前推到"现在正好该按下左键"
            anchor = self.r2_back_at if fresh else time.time() - self.lp('PRE')[0][0]
            self.run_round(False, anchor, tag)
            kind = '6居' if self.lp('MAIN') is self.L6_MAIN else '5居'
            self.logger.info(f'hls loop #{self.loop_count} ({kind}) {time.time() - self.macro_t0:.2f}s')
        except MacroBroken as e:
            self.logger.warning(f'hls loop broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    # ==================== 框架接口 ====================

    def drive(self, entry):
        """三个人的 do_perform 都走这里。entry = 框架这次调的是谁(= 场上是谁)。"""
        # 【HUD 不在就什么都不做】: 关卡结束/结算/过场时框架还会接着调进来
        if not self.wait_hud_up(cap=self.DRIVE_HUD_CAP, tag='drive'):
            if time.time() - self.drive_log_at > 10:
                self.drive_log_at = time.time()
                self.logger.info('hls: 队伍 HUD 不在(结算/过场/窗口没了?), 宏不动')
            return None
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            return self.perform_opener(entry)
        self.logger.info(f'hls: opener not pending ({blocked}), running the loop')
        return self.perform_loop(entry)

    def do_perform(self):
        if not self.is_in_hls:
            return super().do_perform()
        self.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时不让框架把绯雪切上来(宏自己切)。"""
        if self.is_in_hls and self.opener_pending():
            return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_all()
        return super().on_combat_end(chars)
