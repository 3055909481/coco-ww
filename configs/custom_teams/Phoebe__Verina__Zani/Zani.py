import ctypes
import inspect
import threading
import time

from src.char.BaseChar import SwitchPriority
from src.char.Zani import Zani as NativeZani

# 本队三个人的 char_name。【一个角色可能对应多个 char_name】: CharFactory.py:122
# 把 char_moning / char_moning_new 映射到同一个类, 琳奈那对(char_linnai /
# char_linnai2)、千咲那对也一样 —— 只认其中一个的话, 玩家换个立绘整段轴就静默跳过。
# 这三个人目前都只有一个名字, 留着这三行是为了以后加立绘时有个显眼的落点。
# 【本文件的队伍判定不走这里】: is_in_zanfei_nai 用的是类名(char.name), 那个跟
# 立绘无关, 更稳
ZANI_NAMES = {'char_zani'}
PHOEBE_NAMES = {'char_phoebe'}
VERINA_NAMES = {'char_verina'}

# ==================== 物理 F 键监视 ====================
# opener_blocked_reason() 靠 task._f_last_press 区分"玩家按 F 重开了一局"和"战斗判定
# 抖了一下"(抖动不会有人按 F): 按过就直接放行, 不用干等 OPENER_MIN_GAP 那 60 秒。
#
# 【为什么写在角色文件里】: 这个时间戳原来由 src/ 下的代码写。2026-08-19 的 v3.5.32
# 更新用 git 检出把 src/ 整个盖回官方版, 写入方连一行痕迹都没剩, 只剩这边的 getattr
# 读取方一直拿默认值 —— 那条捷径静默失效了。configs/custom_teams/ 不受更新影响,
# 也是唯一能跟着"导出队伍"一起走的地方, 所以搬到这里来。
#
# 【为什么要单开一个线程】: 玩家按 F 是在【战斗开始之前】(进副本按 F 起手), 那一刻
# 角色代码一行都没在跑。放在 do_perform / get_switch_priority 里采样接不住这一下,
# 而且 get_switch_priority 会被框架对每个候选反复调用, 用 GetAsyncKeyState 的
# "上次调用之后按过没有"低位来判会被前一次调用吃掉。所以老老实实轮询打时间戳。
#
# 【为什么读得到玩家、读不到宏自己按的 F】: GetAsyncKeyState 读的是真实键盘的硬件
# 状态; 而框架和宏发的键走 PostMessage 直接投递给游戏窗口, 不经过硬件层, 读不到。
F_VK = 0x46              # 'F'
F_POLL_INTERVAL = 0.05   # 一次按键按住的时间远大于这个, 不会漏采
F_LOG_DEBOUNCE = 1.0     # 按住不放时别刷日志
# 界面上那张设置卡片, 在 ok_tasks/FStartCombatTask.py。放 ok_tasks/ 是因为选项必须由
# 代码声明(json 里多出来的键会被 verify_config 丢掉), 而 config.py/src 每次更新都会被
# 盖回官方版 —— 上一版的开关和时间就是这么没的。ok_tasks/ 不在仓库里, 更新不碰。
F_TASK_NAME = 'FStartCombatTask'


# 监视线程的实现在主程序 src/combat/AxisWatch.py (椰果版), 这里只导入
from src.combat.AxisWatch import f_settings, start_f_watch


class Zani(NativeZani):
    """赞妮。赞菲维队(赞妮/菲比/维里奈)里她兼任【整条轴的持宏者】。

    为什么整条轴写在一个文件里: 自定义角色文件由 CustomCharLoader 按路径单独加载,
    互相 import 不到; 而且半秒级的三人接力一旦经过框架的选人循环就散了。
    挑赞妮持宏是因为【她同时是入口】—— 轴的第一个动作是她开局那一下防反 E,
    最后一个动作是她的 R2, 一圈的首尾都在她身上, 不用再借别人的 do_perform 起跑。

    ================= 轴的出处 =================

    B站视频《【轮椅3终夜】赞妮, 菲比, 维里奈》(UP主: 吃我无痕)。视频左侧有作者自己
    做的「学习笔记」面板, 四个操作组合直接写在上面; 「爆发手法」那一章给了三种,
    【本文件实现的是最后一种(情况3)】—— 用户 2026-09-08 指定。

      情况1  蓄力满放终夜--剩110 / 一套AAA------剩40 / 一套AAA------剩0
      情况2  蓄力反击放终夜--剩90 / 一套断终夜----剩30 / 一套AAA------剩0
      情况3  直接两套断终夜----剩30 / 一套完整AAA----剩0        <-- 这一条

    「三终夜」= 一发大招里打三次终夜。情况3 不蓄力: 上来就猛点攻击, 两下重斩解锁终夜,
    【攻击+闪避取消第一段, 再按攻击直接跳起放第二段】(视频 389~398 秒的字幕),
    这样一套 60 焰光; 打两套剩 30, 最后一套不闪避、完整 AAA 打完正好清空。

    ================= 视频里的四个操作组合 =================

      组合1  赞妮防反+普攻 (变奏菲比) 切维里奈
      组合2  维里奈 AAA / 技能+声骸+大招 / 跳A打满协奏
      组合3  菲比变奏中长按E / 看见回路变色开大招 / AAA重击 打两套
      组合4  赞妮变奏中连按E / 劈出一剑开大招 / 打完爆发防反+普攻 / 变奏菲比

    ================= 两条轴 =================

    实战演示那一段(视频 466~541 秒)逐帧扒下来的按键序列, 秒数都能在常量区找到:

    启动轴 (3 次切人, 视频 469.6~507.2 = 37.6 秒):
      赞妮   E(防反)-A                       -> 切维里奈
      维里奈 3A-E-Q-R-跳-A                   -> 变奏菲比
      菲比   长按E-R-(AAA+重击)x2            -> 变奏赞妮
      赞妮   连按E-Q-连按E(强化E)-A-R        -> 三终夜 -> R2

    循环轴 (4 次切人, 视频 507.2~550 附近, 一圈约 43 秒):
      赞妮   R2 之后一直戳 E                 -> 变奏菲比
      菲比   落地什么都不做(垫回路能量)      -> 切维里奈
      维里奈 3A-E-Q-R-跳-A                   -> 变奏菲比
      菲比   长按E-R-(AAA+重击)x2            -> 变奏赞妮
      赞妮   连按E-Q-连按E(强化E)-A-R        -> 三终夜 -> R2

    【两条轴只差开头一格】: 开局赞妮没有大招也没有协奏, 直接切维里奈; 循环里她刚
    放完 R2, 协奏必满, 所以走"戳E-变奏菲比-菲比过场"。后面四格逐字相同, 共用
    run_body()。

    【菲比一圈上场两次】(跟柯折卜那队的折枝同一个形状): 第一次是过场, 视频 510.5~511.5
    只有一秒、只按了一下 A —— 作者说的是"菲比落地啥也不用干", 那一秒的意义是【接住
    赞妮的变奏、把回路能量垫掉一半】(视频 427 秒字幕), 下一次她上场就能长按 E 直接
    把回路充满。所以这一格【不能省】, 也【不能在这里打输出】。

    ================= 判据 vs 秒表 =================

    这条轴上有两类格子, 分得很清楚:

    1) 【读屏当锚点的】—— 三处, 全部直接调内置角色已经标定过的方法:
       - 赞妮的三终夜:  NativeZani.nightfall_combo()。它内部就是"猛点攻击直到终夜
         就绪(攻击图标外圈那个白环, is_nightfall_ready)再点 0.5 秒", cancel_last_smash=True
         那一支正是视频里的【断终夜】(点到白环灭 -> 0.25 秒 -> 右键闪避)。
         终夜解锁快慢取决于焰光和命中, 按秒表写死一定漂 —— 这一格必须读屏。
       - 菲比的重击:    NativePhoebe.starflash_combo()。左键点到重击图标亮 -> 长按
         放重击星辉, 就是视频里的"A三下一个重击 / 长按攻击键也可以直接放重击星辉"。
       - 赞妮的强化E:   NativeZani.crisis_response_protocol_combo()。普通E -> 普攻到
         强化E就绪 -> 再发 E, 就是"连按E, 劈出一剑"。
    2) 【按视频秒数写窗口的】—— 维里奈那一段(她全程固定动作)、以及各处的切人时机。

    ================= 改这个文件之前先读的几条 =================

    - 动画期间的按键是【被丢掉的, 不排队】。所以"点一下 -> 等一个动画长度 -> 再点一下"
      要求预先知道动画多长, 猜错一次那一段就整个没了。正确做法是【连点一个窗口】
      (macro_spam), 技能键同理(spam_key)。
    - 连点间隔【不要低于 0.12】。爱琳莫那条轴上 0.08 那一轮, 角色在空中被多灌进去的
      点击变成了下落攻击, 把整条尾巴带崩。
    - 【长按期间绝对不要补 mouse_down()】: PostMessage 后端每次都会投一条 WM_ACTIVATE,
      窗口收到就把已经按住的状态打断。一次 mouse_down 就一直有效, 【包括跨过切人】。
    - 【技能键不能跟切人并行发】: 切人期间发出去的键会被游戏带过换人边界落在新角色
      身上。点击落错人无所谓, 技能键落错人就是白烧一个 CD。
    - 【赞妮的处决被内置版关掉了】(NativeZani.f_break 直接 return False, 理由是会打断
      强化 E 和夜闪)。本文件沿用, 一整圈里不按 F。引擎里那几个 f_break_* / spam_f
      是从达妮娅那份引擎原样搬过来的, 这条轴【一次都没调】。
    - 判"某状态消失"之前必须先确认它出现过; 判"某状态出现"之前必须先确认它消失过。
    - configs/custom_teams/ 是按 mtime+size 热加载的, 改完不用重启;
      src/ 下的框架文件是启动时 import 的, 改了必须重启 ok-ww。
    - 【自定义 __init__ 里跟内置版取值不同的字段会被打回内置值】(CharFactory 换类时
      replacement.__dict__.update(char.__dict__))。要保住就在 reset_state() 里重申。
      本文件新加的字段内置版没有, 不受影响。
    - 【分享出去之前先想版本】(2026-09-26 的教训): 有人拿这套代码在 v3.6.9-beta.1
      上跑, 第一圈就崩在
      `Phoebe.absolution_or_confession() got an unexpected keyword argument 'dodge_cancel'`
      —— 那一版把这个参数删了。**内置方法改签名比改名常见得多**, 而改名能被
      hasattr 查出来、改签名查不出来, 到实机才炸而且是整条轴当场停掉。
      所以【所有跨类调内置菲比的地方一律走 native_call】: 它按签名过滤关键字参数,
      方法不在就记一笔返回默认值。自己(赞妮)的方法走继承、直调 —— 那种缺失本来
      就该炸得明显。本文件是照 **v3.6.7** 标定的。
    - 引擎那一半(macro_* / quick_switch / fire_lib / poke_until_* / f_break_* …)
      跟 `Denia__Mornye__Qingxiao/Denia.py`、`Aemeath__Chisa__Denia/Denia.py` 是同一份
      拷贝, 改引擎要三边同步。
    """

    # ==================== 全局节奏 ====================

    # 连点间隔。0.12 是实测的地板: 爱琳莫那条轴上 0.08 会让空中角色打出下落攻击。
    # 【改它等于所有窗口作废】: 下面每一个 *_WINDOW 都是按"窗口/0.12 下"标定的
    A_INTERVAL = 0.12
    # 加密节奏: 用在"这一格要抢在连段超时之前接上"的地方
    A_INTERVAL_FAST = 0.06
    # 技能键的补发间隔。【必须比点击间隔大】: 补发的意义是"铺开覆盖整个动画",
    # 不是连发 —— 挤在 0.12 秒里发 3 下等于只试了一个时刻
    KEY_INTERVAL = 0.10
    # 一个技能键的默认铺开窗口(够盖住一次入场/收招)
    KEY_WINDOW = 0.10

    # ---- 切人 ----
    SWITCH_TIME_OUT = 2.5        # 超过这个还没切过去就是撞在动画里了, 记一笔往下走
    # 【切人要穿过一整段大招演出时用这个】。演出期间游戏不接受换人, 切人键会一直重发
    # 到演出结束那一帧 —— 而演出本身就能有四五秒, 用 2.5 的话必然判失败
    SWITCH_THROUGH_LIB_TIME_OUT = 9.0
    R_PREINPUT_MIN_LAG = 0.10    # 预输入大招键的最小滞后(switch_into_lib)
    SWITCH_KEY_DOWN_TIME = 0.02  # 按住更久没用而且更慢(send_key 会阻塞整个按住时长)
    SWITCH_KEY_INTERVAL = 0.0    # 0 = 每一轮轮询都发一次切人键, 多发没有副作用
    SWITCH_SETTLE = 0.0          # 切到之后的缓冲, 默认不留
    UNTRUSTED_CONFIRM = 3        # count 不达标时同一个 index 连读到这么多次也认
    TRACE_SWITCH = True          # 每次切人记录轮询到的 in_team index, 查"键发了却不换人"
    SWITCH_CD = 0.88             # 游戏的换人 CD(框架按 1 秒判, 这里压紧一点)
    PRECHARGE_TO_SWITCH = 0.10   # 长按预输入: 按下沿留给离场角色消化的时间

    # ==================== 普攻的段长 ====================
    # 【按"最后一下递出去"标定, 不是"演完"】。这三个角色的真实段长还没量过,
    # 沿用别的队伍那条 0.35。本条轴上只有 segment_window/spam_segments 会用到它,
    # 而那两个方法这条轴一次都没调 —— 所有格子都是直接按视频秒数写窗口的
    SEG_ZANI = 0.35
    SEG_PHO = 0.35
    SEG_VER = 0.35
    # A_LEAD: 段数模型给"最后一段能出手"留的余量, 同上, 本轴未用
    A_LEAD = 0.20

    # ---- 大招 ----
    LIB_FIRE_WINDOW = 1.20       # 连发 R 等演出开始的上限。【给短】: 长了会白按掉下一发
    LIB_KEY_INTERVAL = 0.03
    LIB_READ_EVERY = 2           # 每发这么多次 R 才读一次屏(读屏 0.04 秒, 别拖慢发键)
    LIB_ANIM_TIME_OUT = 8.0      # 等演出结束(HUD 回来)的上限
    LIB_ANIM_TAIL = 0.15         # HUD 回来之后还要再点一会儿(收招期间的按键照样被吞)
    LIB_ANIM_SPAM = 2.50         # fire_lib_through_anim 的默认盲发窗口
    HUD_NO_ANIM_GRACE = 0.35     # 一直读到 HUD 在场 = 这次压根没有演出(大招没放出来)

    # ---- 协奏 ----
    CON_WAIT_TIME_OUT = 2.0
    CON_POLL_INTERVAL = 0.05
    CON_ENOUGH = 0.99
    # 【0.99 是个哨兵值, 不是"快满了"】(2026-09-08 定位, 用户连报四次
    # "菲比没满协奏就切了, 就差那么一丝丝")。BaseCombatTask.get_current_con() 里:
    #     if not max_is_full and percent >= 1:
    #         logger.warning('is_con_full not full but percent greater than 1, set to 0.99')
    #         percent = 0.99
    # 也就是【面积已经够了、但能量环还没闭合】时就返回 0.99。而 count_rings 判出
    # "完整环"(max_is_full)才会 percent = 1 —— 那才是真满, 也是 is_con_full() 和
    # 框架 switch_next_char(`current_con == 1`)认的那个。
    # 【所以要等真满就必须用 1.0 当门槛】: 拿 CON_ENOUGH 去等, 一定停在环没闭合的
    # 那一刻。引擎里 wait_con_for_intro 用的就是 CON_ENOUGH —— 别的队伍没暴露出来
    # 是因为它们的角色协奏本来就有富余, 冲过那一格看不出来
    CON_FULL_LEVEL = 1.0
    CON_SAMPLE_MAX = 24
    CON_STALL_GIVE_UP = 1.2      # 读数一动不动这么久就别等满超时了

    # ---- 回路条 ----
    FORTE_WAIT_CAP = 2.5         # 等回路条满的默认上限
    FORTE_SAMPLE = 0.25          # 等的期间把两路读数采样进日志的间隔(查判据用)

    # ---- 场上是谁 ----
    # on_field_char() 等队伍 HUD 稳定下来的上限。开局 HUD 渐入的头 0.8 秒里
    # in_team() 会说谎(三个槽位的文本不是同时出现的), 所以要等 count 达标
    FIELD_SETTLE_TIME_OUT = 1.5

    # ---- 处决(本轴未用, 引擎依赖) ----
    F_WINDOW = 0.60
    F_RETRY_INTERVAL = 0.10
    F_SPAM_INTERVAL = 0.08
    F_GIVEUP = 0.10

    # ==================== 启动轴: 赞妮开局 ====================
    # 视频 469.60 E / 469.87 A / ~470.2 切维里奈。
    # 【开局这一下 E 是防反, 不是起手输出】: 视频 225 秒作者说的是"不少人被古龙开局
    # 这个拍掌+地刺搞得很烦" —— 标准防御协议吃下这一套, 顺手把强化 E 攒出来
    O_ZANI_GUARD_E_WINDOW = 0.10   # E 只要一下, 铺两发盖住入场那一帧
    O_ZANI_GUARD_E_INTERVAL = 0.05
    # 469.60 -> 469.87。【0.27 减到 0.07】(用户 2026-09-09: "启动轴赞妮EA切维里奈
    # 可以再快0.2s") —— 这一格是【纯等待】(macro_wait 不点击), 整条轴上最该被砍的
    # 就是它: 减掉的 0.2 秒一下输出都不损失。A 那一格是在打人, 不动
    O_ZANI_E_TO_A = 0.07
    O_ZANI_A_WINDOW = 0.35         # 469.87 -> ~470.2 切人

    # ==================== 维里奈段(启动/循环共用) ====================
    # 视频两圈: 470.37~476.0 和 511.50~518.0, 两次的分格一致
    # 【3A 那一格删掉了】(用户 2026-09-09: "把维里奈的3A去掉直接接E后面的流程")。
    # 视频里她是 3A-E-Q-R(3A 占 1.26~1.30 秒), 上一版还因为"E放早了"把它加到 1.80 ——
    # 结果是 E 越推越晚。删掉之后入场第一件事就是发 E
    V_E_CAP = 1.50           # "发 E 到它进 CD"的上限。入场动画会吞掉前几发
    V_E_TO_Q = 0.05
    V_Q_WINDOW = 0.20        # 声骸。471.93~472.03 / 513.30~513.53
    V_Q_TO_F = 0.10          # 472.03 -> 472.13(原来这一格直接接 R)
    # 【处决放在维里奈的 Q 和 R 之间】(用户 2026-09-08: "把F让给维里奈, 让她在R之前
    # 接F")。选这里是因为她这一段本来就是站桩: 3A-E-Q 打完就等 R, 处决插进去不挤
    # 任何东西; 而赞妮 R2 那一格是收招+击破动画两段叠着, 塞不下。
    V_F_WINDOW = 0.60        # 找处决提示的窗口。有就打(task.f_break 阻塞到打完), 没有就走
    V_F_ANIM = 3.0           # 真打出处决时, 等击破动画(HUD 回来)的上限
    V_F_TO_R = 0.10          # 处决/窗口结束 -> 按 R
    # 大招演出(HUD 回来)之后再点多久。【0】(用户 2026-09-09: "R之后没有马上跳起来A,
    # 而是在地上A了一下才跳A") —— 0.15 秒正好是一下普攻, 就是那一下。
    # poke_until_hud_back 的 tail 本来是给"HUD 回来时角色还在收招、按键还会被吞"
    # 留的余量, 但这里紧跟着的是【跳跃】: 第一跳被吞掉还有第二跳(V_JUMP_GAP 之后)
    # 接着来, 容错本来就在轴里, 不需要再拿一下普攻去垫
    V_R_TAIL = 0.0
    V_JUMP_GAP = 0.30        # 两下跳之间。474.37 -> 474.67(二段跳)
    V_JUMP_SETTLE = 0.16     # 474.67 跳 -> 474.83 第一下空中 A
    V_AIR_A_WINDOW = 0.95    # 跳A + 落地 A, 打满协奏。474.83~475.73 / 516.50~517.43
    # 【维里奈的保底就一条: 协奏满了才切】(用户 2026-09-08: "维里奈很简单, 保底就是
    # 协奏值满切人就行")。2.5 秒不够就一直打 —— 她这一段本来就富余(视频里最后一下 A
    # 到切人只隔 0.27 秒), 真等满了立刻走, 这个数只是上限
    V_CON_CAP = 6.0

    # ==================== 菲比: 过场那一秒 ====================
    # 视频 510.5~511.5。【只有一下 A】(510.63), 作者原话"菲比落地啥也不用干"
    P_PASS_A_WINDOW = 0.30
    P_PASS_SETTLE = 0.35     # 落地缓冲: 变奏入场是滞空的, 太早切人会带着下落攻击走

    # ==================== 菲比: 输出两套 ====================
    # 视频 476.0~487.0 / 518.0~529.1
    P_INTRO_TO_HOLD = 0.55   # 变奏落地 -> 开始长按 E。476.0 -> 476.57
    # 长按 E 的时长【不在这里】: 走内置 absolution_or_confession() 里固定的 1.2 秒,
    # 跟视频量到的 1.16 / 1.40 是同一件事。写在这里只会跟内置版打架
    P_HOLD_E_TO_R = 0.10     # 477.73 -> 477.80
    P_FORTE_CAP = 1.20       # 长按 E 之后回路还没读满时, 最多再补打这么久
    P_R_TAIL = 0.20          # 大招演出结束之后再点多久
    P_HEAVY_SETS = 2         # 「AAA重击 打两套」
    # 【两套是保底, 不是"试两次"】(用户 2026-09-08: "菲比就一定要保底打出两个强化重击,
    # 切协奏值满Q切人")。判"这一套算数"用 state['starflash_combo'] 有没有涨 ——
    # starflash_combo() 只在真的打出重击时才 +1(内部 cast 确认), 图标没亮就打不出来。
    # 打空了就再来一次, 上限 P_HEAVY_MAX_TRIES 防止怪死了在这儿空转
    P_HEAVY_MAX_TRIES = 4
    P_CHARGE_CAP = 5.0       # 自己点亮重击图标的上限(内置那个也是 5 秒)
    P_HEAVY_GAP = 0.15       # 两套之间的间隔。483.67 -> 483.90
    # ---- 菲比的收尾: (协奏没满就补) -> Q -> 变奏赞妮 ----
    # 【第二个重击打出去就直奔 Q 和切人】(用户 2026-09-09: "菲比第二个强化重击
    # 打出去要马上接Q切人")。中间那些格子是一路减掉的:
    #   2026-09-08  收尾接一发 E 再放 Q(为了补协奏), 启动轴还配过两发;
    #   2026-09-09  E 挪到【两个重击之间】穿插(用户指定), 收尾这一发就多余了 ——
    #               她的 E 有 CD, 中间那发用掉之后这里多半也是空按;
    #               而且【她的 E 是两段的】(第二段是传送), 更不该在收尾多按。
    # 【补协奏那一步不能省, 但它只在没满时才花时间】(用户 2026-09-08 连报四次
    # "没满协奏就切了"): 满协奏离场赞妮才有变奏入场。两个重击加中间那发 E 打完
    # 协奏一般已经满了, wait_con_at_least 第一次读就返回, 不影响"马上"。
    # 【门槛必须是 CON_FULL_LEVEL(1.0) 不是 CON_ENOUGH(0.99)】—— 后者是"环还没
    # 闭合"的哨兵值, 拿它等就是差那么一丝丝, 见 CON_FULL_LEVEL 那段说明。
    # 【8 秒是上限不是等待时长】: 正常一两秒就够; 真到点还不满, 那就不是补的问题
    # (重击打空 / 站位), 日志里 con 的采样轨迹能分出来
    P_CON_FULL_CAP = 8.0
    P_CON_POLL = 0.10        # 补协奏时读协奏的间隔。【读一次不便宜】(能量环连通域分析)
    P_Q_WINDOW = 0.20        # 声骸铺开窗口

    # ==================== 赞妮: 连按E -> 开大 ====================
    # 视频 487.13~491.57 / 529.17~534.97
    # 【入场一直按 E + 一直点普攻, 按到强化E("劈出一剑")出手为止】。
    # 机制: 普通E(标准防御协议) -> 普攻攒回路 -> 回路条右边那格亮(is_e_forte_full)
    # = 强化E(危机应对协议)就绪 -> 下一发 E 就是那一剑。
    #
    # 【这一格来回改过四版, 最后回到判据版】(用户 2026-09-09 一路试下来):
    #   v1 照视频分三段(连按E -> Q -> 等图标 -> 连按E) -> 固定间隔把强化E越推越晚;
    #   v2 判"图标亮过又灭"                            -> 上限 8/12/15/18 一路加;
    #   v3 v2 + R 前补一发普通E(判"E进CD")             -> "不行, 还会卡";
    #   v4 干脆盲发 12 秒不判据                        -> "还是不行";
    #   v5 【回到 v2 的判据 + 强化E之后再持续按 2 秒 E】 <- 现在, 用户定的
    # 【卡顿的根子是能量, 不是这一格按没按到】: 强化E打完能量常常还差一点点,
    # 所以真正的解法是【那一剑之后再补一段 E】(Z_E_AFTER_ENHANCED), 而不是把
    # 前面这一段拉长或者改成盲发 —— 前面拉长只是让强化E更晚出手。
    Z_E_HOLD_CAP = 18.0      # "按到强化E出手"的上限, 到点也往下走
    Z_E_MIN = 0.35           # 这么久之内不判"图标灭"(躲开入场那几帧的读数抖动)
    # 【强化E出手之后再持续按这么久的 E】(用户 2026-09-09: "就加一条强化E放完
    # 再持续按2sE就好")。这一段补的是【普通E】: 赞妮的大招能量大头来自共鸣技能,
    # 强化E刚打完时它的 CD 也差不多转好了, 补一发能量就满, R 立刻接得上。
    # 期间照常点普攻 —— 用户此前明确要过"强化E按了要马上A出去"。
    # 【Q 就在这 2 秒里一起发, 不再单开一格】(用户 2026-09-09: "0.30缓冲去掉,
    # 持续2S的E和Q不冲突, Q不打断动作")。声骸不打断当前动作, 所以它跟补 E 那一段
    # 可以叠在一起 —— 【但两个技能键必须错开帧】: 挤在同一轮里游戏只认后按的那个
    # (达妮娅那条轴上 E 被 R 顶掉栽过两次)。所以一拍 E、一拍 Q, 各占各的帧。
    Z_E_AFTER_ENHANCED = 2.0
    Z_Q_PRESSES = 2          # 这一段里 Q 发几次。多发没用(落 CD 上是空操作)
    Z_E_STEP = 0.12          # 发 E / 发 Q / 点普攻的拍长
    Z_R_RESCUE_WINDOW = 3.0
    # 【两个键必须错开帧】: 挤在同一轮里挨着发, 游戏只认后按的那个 —— 这条在
    # 达妮娅那条轴上栽过(E 被 R 顶掉), spam_attack_and_f 的注释里也写着
    Z_RE_STEP = 0.10
    # 【发 R 之前先等 HUD 回来】(用户 2026-09-09: "赞妮怎么不放R1了")。
    # fire_lib 的判据是"队伍 HUD 掉下去 = 演出开始" —— 要是按 R 那一刻 HUD 本来就
    # 不在(上一个动作的演出/特效还没过去), 它会在第二次发键之后当场判"演出开始了",
    # 日志记 zaniR_fired, 而【R 一次都没真按出去】, 后面三终夜整段落空。
    # 这一格前面刚盲发完 12 秒 E, 强化E的劈砍动画正好压在这儿, 撞上的概率很大。
    # 判"某状态消失"之前必须先确认它出现过 —— 这里就是那个"先确认它在"
    Z_R_HUD_CAP = 2.0
    Z_R_RETRY_A = 0.60       # 大招没放出来时, 再打这么久攒能量然后重试一次
    Z_R_TAIL = 0.20          # 大招演出结束(HUD 回来)之后再点多久
    # 【大招放没放出来看 HUD 消失了多久, 不读图标栏】(2026-11-03 实机):
    # 内置 check_liber 认 2K x=1909 那格有没有锁定图标, 现在大招里那格叠了月牙
    # 普攻位、锁定图标还在原地(读到 1.00), 于是开了大招也判"不在", 补救连发
    # 11R+11E、三终夜整段跳过。真大招演出 HUD 消失 3.35s; 假成功是按 R 之前
    # HUD 就不在, 消失时长对不上
    Z_LIB_ANIM_MIN = 2.5
    # R2 那一格: 一直发 R, 直到 HUD 掉下去(R2 演出)或 E 图标回白(大招结束)
    Z_R2_FIRE_CAP = 3.0

    # ==================== 赞妮: 三终夜 ====================
    # 视频 493.60~507.17。三套的窗口分别是 3.77 / 3.60 / 5.40 秒, 加 R2 = 13.6 秒,
    # 而大招总时长 20 秒 —— 【留了 6 秒余量, 所以这里不按秒表卡, 全部走读屏判据】
    NF_CANCEL_COUNT = 2      # 断终夜打几套。情况3 = 2
    NF_FULL_COUNT = 1        # 完整终夜打几套。情况3 = 1
    # 【一套终夜的分格】(用户 2026-09-08 口述的机制: "赞妮R之后A, A到强化重击亮了,
    # 然后A接闪避, 再A出终夜")。跟视频 389~398 秒的字幕逐句对得上:
    #   猛按攻击(不蓄力) -> 打两下重斩 -> 图标亮=终夜解锁
    #   -> 攻击+闪避 取消终夜第一段 -> 再按攻击 直接跳起来放第二段
    NF_MIN_A = 1.60          # 开始判图标之前至少先点这么久(两下重斩的时间)
    NF_ACQUIRE_CAP = 7.0     # 等图标亮的上限, 到点也往下走
    # 【最后那套完整终夜单独给短上限】(2026-11-03 实机): 第三套偶尔在 NF_MIN_A 盲点
    # 那 1.6s 里就打出去了(31.9~32.4s 大段金色下砸), 之后普攻位一直是拳头图标、终夜
    # 不会再亮, 宏干点 7s 才放 R2。正常局第三套 2.69~3.09s 就亮。
    # 【不做"焰光见底就 R2"】: F8 原图看回路条那排黄格在第三套亮之前本来就会清空、
    # 变成白条(正常局 35.8s 清空、36.85s 亮), 见底不等于打不出来。
    # 内置 update_blazes 的框在 2K y≈1343, 比黄格(1322~1334)低一截, 恒读 0, 别用
    NF_FULL_ACQUIRE_CAP = 4.0
    # 【图标亮之后那一下 A 要【真的出手】才闪避】(用户 2026-09-08 第二次修正:
    # "终夜亮了得再A出一下用闪避打断再A")。v2 这里是个 0.18 秒的固定窗口, 太早 ——
    # 点击【递出去】不等于动作【出手】, 闪避落在起手帧上是把这一下整个取消掉,
    # 而不是"打断"。判据用图标自己灭: 灭了就说明终夜第一段吃掉了那次充能。
    # 【上限给短】: 内置 nightfall_combo 同样是等图标灭, 但它用 threshold=0.035
    # (几乎完全消失)且上限 2.5 秒 + 额外 sleep(0.25) —— 那就是上一版"慢了2S"的来源。
    # 这里用默认门槛(0.15, 跟判亮同一个)、上限 0.8 秒、灭了立刻闪
    NF_MIN_HIT = 0.15        # 开始判"灭"之前至少先点这么久(躲开起手那几帧的渲染抖动)
    # 【这一格是"充分条件 + 固定提前量", 不是纯判据】(用户 2026-09-08 第三次修正:
    # "赞妮闪避还是有点慢了, 得快0.4s")。
    # 【为什么不能只等图标灭】: 读屏判据天生比动作晚 —— 白环是渐隐的, 等它掉到
    # threshold 以下的时候那一下早就出手完了; 大招期间满屏特效, 读数还会被糊住,
    # 于是常常走满上限才退出。上一版这个上限是 0.80, 实机就是"慢了 0.4 秒"。
    # 【所以主路径改成固定窗口 0.40】(= 上一版的 0.80 - 用户量的 0.40),
    # 读到图标灭只当【更早退出】的那条腿: 灭得早说明这一下被打断了, 那就跟着提前。
    # 【还慢就接着减这个数, 别去动 NF_MIN_HIT】—— 那个是防误判的下限, 动它会让
    # 闪避落回起手帧上(v2 那次"闪早了"就是 0.18 全窗口的后果)。
    # 日志 `出手(ok|CAP, A xN, ?s)` 里的 ok/CAP 说明走的是哪条腿, 秒数就是实际用时
    NF_HIT_CAP = 0.40
    NF_DODGE = 0.10          # 打断用的闪避
    NF_SECOND_WINDOW = 0.30  # 闪避之后"再A出终夜"。视频 497.40 闪 -> 497.57 接着 A
    # 完整终夜那一套: 不闪避, 一路点把两段都打出去。
    # 【1.20 不够】(用户 2026-09-08: "赞妮最后一个终夜伤害没打出来就R2打断了") ——
    # 第二段下砸是点出来的, 点得不够长它根本没触发
    NF_FULL_TAIL = 1.60
    # 下砸落地要多久, 落地了才发 R2。【这一段不点击】—— 点了会接普攻, 反而打断下砸。
    # 【这个数被来回调过两轮, 记住哪一段管什么】:
    #   v1  连点 1.20 + 等 1.00(那时还在读 nightfall_time_left) = 2.20
    #       -> 用户: "最后一个终夜伤害没打出来就R2打断了"(下砸没演完)
    #   v2  连点 1.60 + 等 1.80 = 3.40  -> 用户: "R2慢了0.9s"(加过头了)
    #   v3  连点 1.60 + 等 0.90 = 2.50  <- 现在。比 v1 多 0.3 秒给下砸, 同时 R2 比 v2 早 0.9 秒
    # 【要调先分清是哪一头】: "伤害没打出来"加 NF_FULL_TAIL(第二段是点出来的);
    # "R2慢了"减这个 —— 反过来调会把刚修好的那一头弄坏
    NF_SMASH_SETTLE = 0.90
    NF_R2_LEAD = 0.10        # 506.80 -> 507.17

    # ==================== 赞妮: R2 之后 ====================
    # 视频 507.17~510.5。作者原话"赞妮二段大的时候你就一直戳共鸣技能"
    # ---- R2 之后: 直接 E 接 A(不再蹭处决) ----
    # 【F 挪给维里奈了】(用户 2026-09-08: "我们把F让给维里奈, 让她在R之前接F,
    # 赞妮那里R2直接接EA")。在赞妮这里试了三版都接不上:
    #   v1 f_break_persist 风格   -> 处决打完又点了一堆 A
    #   v2 F/E 交替 + resonance_gone -> 击破动画里 HUD 消失, 判据恒假, E 一发没出去
    #   v3 上面那版 + HUD 门禁     -> 还是没接上
    # 根子在于【R2 收招 + 击破动画这两段叠在一起, 留给 E 的窗口本来就没剩多少】,
    # 而维里奈那一格是纯站桩(3A-E-Q 之后就等 R), 处决插进去不挤任何东西。
    # R2 的收招照样吞按键, 按固定次数发会丢。【但不能"发到进CD"】: 赞妮的 E 不出
    # 冷却数字, 那样每次都按满上限(2026-11-03 实机 19 发)。见 zani_e_once
    Z_R2_E_CAP = 2.50        # 一直没等到 HUD 在场 + E 图标白的上限
    Z_R2_E_TAIL = 0.30       # 第一发真出去之后再连发这么久就走
    # E 之后接的那一小段 A, 然后就切。
    # 【0.30 减到 0.20】(用户 2026-09-09: "循环轴赞妮R2EA切菲比也可以再快0.1s")。
    # 这一格前后都没有纯等待可砍了(E 那段是判据驱动的、协奏那段满了就走),
    # 只能从这一小段 A 里出 —— 0.20 还够点一到两下
    Z_R2_A_WINDOW = 0.20
    Z_R2_CON_CAP = 2.5       # 协奏没满时最多再补打这么久(正常放完大招必满)

    # ==================== 防重入 / 串轴 ====================
    # 启动轴一局只跑一次。战斗判定抖一下(HUD 被特效盖住)会让框架重读队伍,
    # 没有这道门槛的话启动轴会在半路上重跑
    OPENER_MIN_GAP = 60.0
    # 启动轴跑完直接接一圈循环轴, 不回框架。【打开】: 两条轴的接缝就是赞妮的 R2,
    # 回一趟框架反而会在那里插一次选人 —— 而 R2 之后紧接着就是"戳E变奏菲比",
    # 差这一下协奏就不满了
    CHAIN_LOOP_AFTER_OPENER = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opener_armed = True
        self.last_opener = -1
        self.macro_t0 = 0.0
        self.macro_marks = []
        self.cur = None              # 宏执行期间"现在场上是谁", 交接记账要用
        self.holding_attack = False  # 长按要跨过切人, 按下和松开不在同一个方法里
        self.poking = False          # 【持续普攻】总开关
        self.switch_press_at = 0.0
        self.lib_press_at = 0.0
        self.last_anim_s = 0.0
        self.loop_count = 0

    def reset_state(self):
        super().reset_state()
        self.opener_armed = True
        # 物理 F 键监视, 说明见文件顶部 start_f_watch。挂在 task 上, 每个 task 只启一次;
        # 放这里是因为 reset_state 每次重读队伍都会跑, 而 __init__ 里设的字段会被
        # apply_team_char_classes 的 __dict__.update 打回内置值
        start_f_watch(self.task, self.logger)

    # ==================== 队伍判定 ====================

    @property
    def is_in_zanfei_nai(self) -> bool:
        """本队 = 赞妮 + 菲比 + 维里奈(内置代码里管这个组合叫"赞菲奶")。"""
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Phoebe', 'Verina'}:
                team_members += 1
        return team_members == 2

    def teammate(self, name):
        for char in self.task.chars:
            if char is not None and char.name == name:
                return char
        return None

    def opener_pending(self):
        return self.opener_blocked_by() is None

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。

        【这里不查任何"某个技能可用"】(爱琳莫那条轴上的教训): BaseChar.available()
        对场上/场下用的是两套机制 —— 场上读屏幕图标, 场下读该角色【上次在场时
        OCR 到的 CD 记录】。场下那条路读的是残值, 会得到一个假答案, 表现出来就是
        "开场愣一下, 切个人再切回来才开始"。防重入靠 opener_armed + OPENER_MIN_GAP 就够。
        """
        if not self.is_in_zanfei_nai:
            return 'team is not zani/phoebe/verina'
        if not self.opener_armed:
            return 'already armed-down (ran once this combat)'
        # 【玩家按过 F 就当成"真的重开了一局", 直接放行】: 60 秒门槛分不清
        # "战斗判定抖动"和"玩家重开副本", 而物理 F 键正好能分 —— 抖动不会有人按 F
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

    # ==================== 跨版本调内置方法 ====================

    def phoebe_charge_heavy(self, pho, cap, tag='phoChg'):
        """点到菲比的【重击图标亮】为止。返回亮没亮。

        【替代 v3.6.7 的 _charge_starflash_until_full(finish_with_right_click=False)】
        —— 那个方法在 v3.6.9 的重构里没了, 而新版 starflash_combo() 自带的充能段
        收尾有一下 continues_right_click(0.05), 位置在【重击之前】, 正是用户
        2026-09-09 点名要去掉的那一下("菲比的闪避取消掉, 感觉有浪费时间")。
        【自己先点亮就能整段跳过】: 新版那段外面裹着
            if not condition() and not self.heavy_attack_ready():
        图标已经亮着时整个 if 不进, 右键跟着一起跳过 —— 跟当初传
        finish_with_right_click=False 是同一个效果, 而且两个版本都成立。

        判据用 is_forte_full(): 菲比重写过它, star_available 之后读的就是
        重击图标(v3.6.9 的 heavy_attack_ready() 就是它的别名), 两版语义一致。
        """
        start = time.time()
        n = 0
        lit = False
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            if pho.is_forte_full():
                lit = True
                break
            self.click()
            n += 1
            self.task.next_frame()
        self.macro_mark(f'{tag}({"亮" if lit else "CAP"},A x{n},'
                        f'{time.time() - start:.2f}s)')
        return lit

    def phoebe_ground(self, pho, cap=2.0, tag='phoGround'):
        """等菲比落地。【v3.6.7 的 _ensure_grounded 在 v3.6.9 没了】, 自己做一个。

        滞空时重击打不出来(新版 perform_heavy_attack 里遇到 flying 会中断重来),
        所以重试之前先把人弄下来。点普攻既是催落地也是输出。
        """
        if not pho.flying():
            return True
        start = time.time()
        n = 0
        down = False
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            if not pho.flying():
                down = True
                break
            self.click()
            n += 1
            self.task.next_frame()
        self.macro_mark(f'{tag}({"落地" if down else "CAP"},A x{n})')
        return down

    def phoebe_short_e(self, pho):
        """菲比的【短按 E】(镜之环定身), 跨版本。返回按出去了没有。

        三个版本三个名字, 按优先级试:
          v3.6.9  click_resonance_once()   —— 内置就是"按到它进 CD 或 0.5 秒"
          v3.6.7  _insert_control_e()      —— 读屏确认再按, 没按出去补一次
          都没有  自己发一次 send_resonance_key()
        【绝不长按】: 长按是切告解形态, 会白烧一次形态资源, 而两个强化重击正好
        各吃一次 —— 烧掉一次就凑不齐了。
        【她的 E 是两段的】(第一段定身、第二段传送), 所以这一格宁可少按不要多按:
        少按只是少一次定身, 多按是把她传到怪脸上、整段站位崩掉。
        """
        for name in ('click_resonance_once', '_insert_control_e'):
            if callable(getattr(pho, name, None)):
                return bool(self.native_call(pho, name, _default=False))
        pho.send_resonance_key()
        self.macro_mark('phoE(裸发)')
        return False

    # 【同一个"这一版没有 X"只警告一次】: 不然每圈都刷同一条。类级别的 set,
    # 所有实例共享 —— 它记的本来就是"对方这个版本缺什么"这种静态事实
    _compat_warned = set()

    def native_call(self, obj, name, *args, _default=None, **kwargs):
        """调【内置角色】的方法, 跨 ok-ww 版本安全。

        - 方法不在了(改名/删掉) -> 记一笔, 返回 _default, 不抛异常;
        - 关键字参数它不认识(改了签名) -> 把那几个丢掉再调。

        【为什么需要】(2026-09-26: 别人拿这套代码在 v3.6.9-beta.1 上跑, 第一圈
        就崩在 `Phoebe.absolution_or_confession() got an unexpected keyword
        argument 'dodge_cancel'`):
        自定义队伍是跟着 zip 分享出去的, 对方的 ok-ww 版本不一定跟标定时一样;
        而内置方法【改签名比改名常见得多】—— 改名能被 hasattr 查出来, 改签名
        正好从那道检查底下漏过去, 到实机才炸, 而且是整条轴当场停掉。
        【丢参数的后果要想清楚】: 丢掉的那个参数会退回内置的默认值, 行为可能跟
        标定时不一样(比如 finish_with_right_click 丢了就等于恢复"闪避在重击之前")。
        所以这里每丢一个都打日志, 对方看日志就知道自己那一版差在哪。

        【只用在跨类调用上】(pho.* / ver.*): 赞妮自己的方法走继承, 缺了会直接
        AttributeError —— 那种情况本来就该炸得明显, 不该悄悄吞掉。
        """
        func = getattr(obj, name, None)
        if not callable(func):
            key = (type(obj).__name__, name, 'missing')
            if key not in self._compat_warned:
                self._compat_warned.add(key)
                self.logger.warning(
                    f'native_call: {type(obj).__name__}.{name}() 在这个 ok-ww 版本里不存在, '
                    f'跳过(返回 {_default!r}) —— 这一格的动作会缺失, 但不会打断整条轴')
            return _default
        if kwargs:
            try:
                params = inspect.signature(func).parameters
            except (TypeError, ValueError):
                params = None
            if params is not None and not any(
                    x.kind is inspect.Parameter.VAR_KEYWORD for x in params.values()):
                dropped = [k for k in kwargs if k not in params]
                if dropped:
                    key = (type(obj).__name__, name, tuple(dropped))
                    if key not in self._compat_warned:
                        self._compat_warned.add(key)
                        self.logger.warning(
                            f'native_call: {type(obj).__name__}.{name}() 这一版不认识 '
                            f'{dropped} 这几个参数, 已丢掉 —— 它们会退回内置默认值, '
                            f'行为可能跟标定时不一样')
                    kwargs = {k: v for k, v in kwargs.items() if k in params}
        return func(*args, **kwargs)

    # ==================== 宏原语(引擎) ====================
    # 【这一整块是从 Denia__Mornye__Qingxiao/Denia.py 逐字搬过来的引擎】(2026-09-08),
    # 那一份又是从 Aemeath__Chisa__Denia/Denia.py 搬的。里面的注释引用的实机数据、
    # 日志片段、"用户说" 全都出自【那两条轴】—— 留着是因为那些结论(动画吞按键、
    # 切人边界、HUD 判据…)跟队伍无关, 是框架的性质; 凡是提到千咲/爱弥斯/琳奈/
    # 清宵/莫宁的地方都是【历史标定记录】, 不是这条轴的参数。
    # 【改这里要三边同步】: 同一个引擎现在有三份拷贝, 这是新目录结构的代价
    # (自定义角色文件由 CustomCharLoader 按路径单独加载, 互相 import 不到)。
    #
    # 【这条轴一次都没调的】: segment_window / spam_segments(段数模型 —— 所有格子
    # 都是直接按视频秒数写窗口的)、f_prompt_now / spam_f / spam_attack_and_f /
    # f_break_window / f_break_persist(赞妮和菲比的 f_break 都被内置版关掉了,
    # 整条轴不按 F)、switch_into_lib / switch_out_while_key / hold_into_switch /
    # macro_spam_live / resend_until_fired / attack_until_lib 之外的几个等待原语。
    # 留着不删是为了跟另外两份保持逐字一致, 好对 diff。

    def macro_wait(self, sec):
        """精确等待, 【不点击】。

        不走 executor.sleep —— 它每 0.4 秒无条件插一次 next_frame 截图, 插入位置
        不可控, 而这条轴的容错在 ±0.1 秒级。切成 0.1 秒一片, 片间检查任务是否被停用。
        """
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

    def macro_gap(self, sec, tag=None):
        """间隙。【持续普攻打开时自动变成连点】—— 这就是"我没说停就一直A"。"""
        if sec <= 0:
            return 0
        if not self.poking:
            self.macro_wait(sec)
            return 0
        return self.macro_spam(sec, self.A_INTERVAL, tag=tag or 'gapA')

    def macro_start(self):
        self.macro_t0 = time.time()
        self.macro_marks = []

    def macro_mark(self, name):
        self.macro_marks.append(f'{name}@{time.time() - self.macro_t0:.2f}')

    def macro_timeline(self, since=0):
        return ' '.join(self.macro_marks[since:])

    def macro_spam(self, window, interval=None, tag='A', sample=None, sample_every=2,
                   max_clicks=None):
        """连点一个窗口。

        打出几段由 window 决定, 不由单次点击的时机决定 —— 动画期间的点击会被丢掉,
        连点保证总有一下落在动画刚结束的瞬间。时间轴上记首尾和点了几下, 对着录像
        能反推一段动画多长: 窗口里打出 n 段, 一段就约等于 window/n。
        """
        interval = self.A_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        # 次数上限: 光靠时间兜底的话, interval 被调成 0 或者机器卡一下就会瞬间
        # 灌进去几千次点击
        cap = int(window / max(interval, 0.01)) + 2
        # 【可以再封一层】: 用在【空中】那几格 —— 多余的点击会变成下落攻击,
        # 落地会打断/改变后面的动作。封顶【不缩短窗口】, 所以不花时间
        max_clicks = cap if max_clicks is None else min(cap, max_clicks)
        self.macro_mark(f'{tag}_start')
        self.spam_samples = []
        while time.time() - start < window and n < max_clicks:
            self.click()
            n += 1
            if sample is not None and n % sample_every == 0:
                self.spam_samples.append(f'{time.time() - start:.2f}:{1 if sample() else 0}')
            # 最后一段等待按剩余时间裁剪, 别整个 interval 等下去 —— 那是白送的延迟
            remaining = window - (time.time() - start)
            if remaining <= 0:
                break
            self.macro_wait(min(interval, remaining))
        self.macro_mark(f'{tag}_end(x{n})')
        return n

    def macro_spam_live(self, window, interval=None, tag='A', cap=None):
        """连点一个窗口, 但【HUD 不在的那几秒不计入窗口】。

        用在"前面刚盲发过 F"的格子(用户 2026-09-08: "还是慢了, 我感觉是被F动画
        吞了")。盲发的 F 什么时候真触发击破是【不确定的】—— 提示晚亮零点几秒,
        击破就落在这一格中间, 全局时停 + HUD 消失, 那几下点击全被丢掉。
        【所以加窗口治不了】: 加多少都可能正好被吞掉那一段。

        这里改成数"活时间": HUD 在场才累加、才点击; 击破/演出期间原地等,
        等它过去接着补满 window。这样不管击破在哪一刻发生, 打出来的段数都一样。

        Args:
            cap: 墙钟上限, 防止 HUD 一直读不到时卡死。默认 window 的三倍。
        """
        interval = self.A_INTERVAL if interval is None else interval
        cap = window * 3 if cap is None else cap
        start = time.time()
        last = start
        live = 0.0
        frozen = 0.0
        n = 0
        next_click = 0.0
        while live < window and time.time() - start < cap:
            self.task.next_frame()
            try:
                on_hud = bool(self.task.in_team()[0])
            except Exception:
                on_hud = True
            now = time.time()
            if on_hud:
                live += now - last
                if now >= next_click:
                    self.click()
                    n += 1
                    next_click = now + interval
            else:
                frozen += now - last
            last = now
            self.macro_wait(0.02)
        if frozen > 0:
            self.add_freeze_duration(start, frozen)
        self.macro_mark(f'{tag}(x{n},live{live:.2f},'
                        f'{"froze%.2f," % frozen if frozen > 0 else ""}'
                        f'{time.time() - start:.2f}s)')
        return n

    def segment_window(self, seg, n=1, lead=None):
        """n 段普攻要给多长的连点窗口。见 spam_segments 的说明。

        Args:
            lead: 覆盖 A_LEAD。【只给个别格子用】—— 某一格要"更早切人"就调它,
                不要去动全局的 A_LEAD(那会把二十来格一起改慢/改快)。
        """
        return max(0.0, (n - 1) * seg) + (self.A_LEAD if lead is None else lead)

    def spam_segments(self, seg, n=1, tag='A', lead=None, interval=None, max_clicks=None):
        """打出 n 段普攻。【调用点写的是段数, 不是秒数】。

        窗口 = (n-1) * 一段 + A_LEAD:
          - 前 n-1 段各要等自己的动画走完, 所以按段长累加;
          - 最后一段只要【递出去】就行 —— 它的动画会在切人之后继续演(合轴),
            所以不给它留时长, 只留 A_LEAD 保证有一下落在能出招的第一帧。

        【为什么还是连点, 不是点 n 下】: 动画期间的点击是被丢掉的、不排队,
        离散点击 + 固定间隔猜错一次那一段就没了。连点打出几段只由窗口长度决定,
        多出来的点击落在动画里, 丢掉不花钱。
        """
        return self.macro_spam(self.segment_window(seg, n, lead),
                               self.A_INTERVAL if interval is None else interval,
                               tag=f'{tag}[{n}seg]', max_clicks=max_clicks)

    def macro_key(self, key, settle=0.0, tag=None):
        self.task.send_key(key)
        self.macro_mark(tag or str(key))
        self.macro_gap(settle)

    def spam_key(self, send, window, interval=None, tag='key', poke=None, presses=None,
                 click_interval=None, stop_when=None):
        """在一个窗口里反复发同一个技能键, 用来盖住动画那一段。

        动画期间的按键是被丢掉的、不排队, 所以单发必丢 —— 这个形状在爱琳莫那条轴上
        踩到过六次, 症状统一是"那一步慢了"(要等整个动画演完才有第二次机会)。

        【必须在这个角色自己的回合里跑完, 不要跟切人并行】: 切人期间发出去的键会被
        游戏带过换人边界落在新角色身上。点击可以并行(quick_switch 的 click_during),
        技能键不行。唯一的例外是 switch_into_lib()。

        Args:
            poke: 发键期间要不要照常点普攻。默认跟随全局的持续普攻开关。
            click_interval: 点普攻的间隔, 默认 A_INTERVAL。给 A_INTERVAL_FAST
                就是"技能键按自己的节奏、普攻按加密节奏", 两条流互不影响。
            stop_when: 每发一次键之后调一次, 返回 True 就停。用在【多按一下会
                烧掉后面要用的充能】的地方 —— 一旦读到技能已经不可用就立刻收手。
                【会读屏, 别配太密的 interval】。
            presses: 只发这么多次, 均匀铺在窗口里。用在"连续点按两次E"那种
                【次数本身有意义】的地方 —— 连发会把两发挤成一发。
                【给了 presses 就由它说了算, window 只用来推间隔】: 否则
                window=0(要求两发贴着发)会在第一发之后就把循环停掉, 变成单发。
        """
        interval = self.KEY_INTERVAL if interval is None else interval
        poke = self.poking if poke is None else poke
        click_interval = self.A_INTERVAL if click_interval is None else click_interval
        if presses is not None and presses > 0:
            interval = max(interval, window / presses)
        start = time.time()
        n = 0
        clicks = 0
        next_click = 0.0
        while True:
            send()
            n += 1
            if stop_when is not None and stop_when():
                break
            if presses is not None:
                if n >= presses:
                    break
                left = interval
            else:
                left = window - (time.time() - start)
                if left <= 0:
                    break
            step_end = time.time() + min(interval, left)
            # 发键的节奏和点击的节奏各走各的 —— 挂在一起的话点击会被技能键的
            # 间隔卡住(爱琳莫那边 R 被 E2_POLL 卡住就是这个)
            while True:
                now = time.time()
                if now >= step_end:
                    break
                if poke and now >= next_click:
                    self.click()
                    clicks += 1
                    next_click = now + click_interval
                self.macro_wait(min(0.02, step_end - time.time()))
        # 【点击数也要打进去】: x{n} 只数了技能键, 看不出这段时间有没有在持续普攻。
        # 用户 2026-08-16 问过一次"E的期间也要一直普攻" —— 实际一直在点,
        # 只是日志里看不见。A x0 就是真的没点(poke=False 的那几格)
        self.macro_mark(f'{tag}(x{n},A x{clicks})')
        return n

    def resonance_gone(self, char):
        """这个角色的 E 是不是已经不可用了(放出去了 / 进了 CD)。

        【读屏】(task.available -> has_cd), 只给 spam_key 的 stop_when 用,
        别放进热路径。冷启动那段发机甲 E 的循环用的是同一个判据。
        """
        try:
            self.task.next_frame()
            return not char.resonance_available()
        except Exception:
            return False

    def resend_until_fired(self, char, cd_name, key, time_out, interval=None, tag='E'):
        """反复发同一个技能键, 直到它真的放出去了(进了 CD)。

        【发出去】和【放出来】是两回事: 动画期间的按键会被丢掉, 而日志上只看得到
        "键发过了"。进了 CD 立刻停, 不会多按掉后面还要用的那一发。
        """
        interval = self.KEY_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        max_presses = int(time_out / max(interval, 0.01)) + 2
        while time.time() - start < time_out and n < max_presses:
            if self.task.get_cd(cd_name) > 0:
                char.record_resonance_use()
                self.macro_mark(f'{tag}_ok(x{n})')
                return True
            self.task.send_key(key)
            n += 1
            self.macro_gap(interval)
        self.macro_mark(f'{tag}_timeout(x{n})')
        self.logger.warning(f'{tag} never entered cd after {n} presses '
                            f'({time_out:.1f}s), swallowed by an animation?')
        return False

    def macro_hold_attack(self, hold, settle=0.0, tag='hold'):
        self.task.mouse_down()
        self.holding_attack = True
        self.macro_mark(f'{tag}_down')
        try:
            self.macro_wait(hold)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}_up')
        self.macro_gap(settle)

    def release_attack(self):
        if self.holding_attack:
            self.task.mouse_up()
            self.holding_attack = False

    def quick_switch(self, target, click=False, click_during=None, settle=None, time_out=None,
                     read_con=False, assume_con=False, click_interval=None,
                     click_first=False):
        """按数字键直切给指定角色。

        不走 switch_next_char(): 那个要先跑 _choose_switch_target 选人, 顺序是框架
        说了算; 而且它每次发数字键都跟一次左键, 想不要那下 A 就没法不要。

        Args:
            click: 到场之后补一下普攻(复刻框架 BaseCombatTask.py:644 的行为)。
                这条轴上一般不用 —— 持续普攻本身就在点, 再补就是多推一段连段。
            click_during: 等换人生效的这段时间里继续点。默认跟随持续普攻开关。
                这就是用户说的"狂点普攻切人": 换人不是瞬间的, 这几下落在上一个
                角色身上, 正好把他最后那一段打出来。
            read_con: 按键之前读一次协奏, 决定要不要按"带变奏"记账。
                【不便宜】(能量环连通域分析), 只有真的要变奏的那两三处才传 True。
            assume_con: 不读屏, 直接按满协奏记。用在【刚放完大招】之后 ——
                那时协奏必满, 而演出期间能量环读数不可信。

        Returns: 是否真的切过去了。
        """
        if target is None:
            return False
        if target is self.cur:
            # 目标已经在场。【这里必须早退】: 不早退的话到场判据立刻成立,
            # 整步 50 毫秒走完, 后面那些"给他的"按键其实发在了别人身上
            self.macro_mark(f'>{target.name[:3]}(already)')
            return True
        entry = time.time()
        time_out = self.SWITCH_TIME_OUT if time_out is None else time_out
        settle = self.SWITCH_SETTLE if settle is None else settle
        click_during = self.poking if click_during is None else click_during
        click_interval = self.A_INTERVAL if click_interval is None else click_interval
        source = self.cur if self.cur is not None else self
        # 【第一个数字键要抢在读协奏之前发】: 读协奏是能量环的连通域分析, 少则
        # 二三十毫秒、睡完之后的第一次读屏能到 0.86 秒 —— 夹在"上一个动作"和
        # "数字键"之间就是白白的延迟。切人本身要 0.06~0.11 才落地, 来得及读完
        slot = target.index + 1
        # 【记下第一个数字键的时刻】: 有几格是靠它当锚点的。
        # 用户逐帧量的都是"从切人到 A 出手", 而窗口默认从【检测到到场】算起 ——
        # 中间隔着切人生效 + 读屏确认, 两者差好几十毫秒
        self.switch_press_at = time.time()
        # 【先点几下再发第一个数字键】(click_first = 点几下)。用在"边打A边不停切人"
        # 的格子上: 游戏在角色动作期间会拒绝切人 —— 这正是那种写法不会掐掉 A 的原因。
        # 但如果这一刻场上角色【刚到场、还没起手】, 切人键就没有东西挡着, 会当场生效,
        # 她一下都不打就走。
        # 【一下不够】(用户 2026-08-16: "这里千咲又没A出来了"): 那一下要是没被接受
        # 就白给了。所以改成可以指定点几下 —— 每多一下只把切人键推迟一个
        # click_interval(0.06), 但多一次撞上"能出招的那一帧"的机会
        for i in range(int(click_first)):
            self.click()
            if i + 1 < int(click_first):
                self.macro_wait(click_interval)
        self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
        if assume_con:
            con_full = True
        elif read_con:
            con_full = bool(source.is_con_full())
        else:
            con_full = False
        con_cost = time.time() - entry

        start = time.time()
        last = start   # 第一个数字键已经在上面发过了
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
            # 点击的节奏跟切人键解耦: 切人键 0.03 秒一发, 点击照旧 A_INTERVAL,
            # 否则这段时间会以 0.03 的密度灌点击, 远低于安全下限
            if click_during and now >= next_click:
                self.click()
                clicks += 1
                next_click = now + click_interval
            self.task.next_frame()
            in_team, index, count = self.task.in_team()
            # 【count 不达标的 index 打折扣, 但不能一票否决】: in_team() 在只找到
            # 一个槽位文本时照样返回 True 和一个 index(那是渲染顺序的产物);
            # 但一票否决会造成假失败 —— 有槽位被特效盖住时 count 就是不达标,
            # 切人明明成功了也判不出来, 卡满超时, 整条轴从这里崩
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
                if trace:
                    self.logger.info(f'switch trace -> {target.name}: {" ".join(trace)}')
                if click:
                    self.click()
                    self.macro_mark(f'{target.name[:3]}A')
                self.macro_gap(settle)
                return True
        # 【失败时更要打 trace】:
        #   一直是旧角色的 index  -> 游戏真的在拒绝切人(上一个动作还没演完)
        #   带 ? 的都是目标 index -> 切过去了, 是 count 判据把它挡住了
        #   全是 x               -> HUD 整个不在(演出/时停里)
        self.logger.warning(f'switch to {target.name} slot {slot} timed out '
                            f'after {time_out:.1f}s (stuck in an animation?)'
                            + (f' trace: {" ".join(trace)}' if trace else ''))
        self.macro_mark(f'>{target.name[:3]}FAIL')
        return False

    def handoff(self, from_char, to_char, con_full):
        """复刻 switch_next_char() 尾部那套交接记账 (BaseCombatTask.py:663-672)。

        直接按数字键绕开了框架逻辑, 不补的话没人的 is_current_char 是 True,
        get_current_char() 返回 None, AutoCombatTask 主循环当场崩。
        """
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

    def switch_cd_left(self, char):
        """某个角色的换人 CD 还剩多久 —— 【只算不等】。

        【不读屏】: 头像上那个小数字是 OCR, 不便宜也不稳; 而 last_switch_time 是
        我们自己在 handoff -> switch_out 里落的笔, 免费且精确(框架自己判换人 CD
        用的也是它, BaseCombatTask.py:451)。
        """
        last = getattr(char, 'last_switch_time', -1)
        if last is None or last < 0:
            return 0.0
        return max(0.0, self.SWITCH_CD - (time.time() - last))

    def wait_switch_ready(self, char, tag='swCD', lead=0.0):
        """等某个角色的换人 CD 转好。

        用户在启动轴上明写: "要等千咲切人CD转好后立刻E1切千咲"。

        Args:
            lead: 【提前这么多就返回】, 把剩下这段留给调用方拿去跟切人并行。
                用在"等CD -> 发技能 -> 切人"这种串行链上 —— quick_switch 本来
                就会按 SWITCH_KEY_INTERVAL 一直重发数字键, 所以让它压着 CD 尾巴
                开始按, 游戏 CD 一到那一帧就生效, 而不是"CD到了才开始按、再花
                0.13 秒确认到场"。
                【实测值】(2026-08-19 日志, 连续两轮一致): 这一格切千耗时 0.13,
                整条链 denA12结束@14.36 -> 等CD 0.44 -> denE1@14.81 -> 千到场@14.94。
                lead 就是从那 0.13 里省出来的。
        Returns: 实际等了多久。
        """
        left = self.switch_cd_left(char)
        wait = left - lead
        if wait <= 0:
            self.macro_mark(f'{tag}(0.00|left{left:.2f})')
            return 0.0
        self.macro_gap(wait, tag=tag)
        self.macro_mark(f'{tag}({wait:.2f}|lead{left - wait:.2f})')
        return wait

    def switch_out_while_key(self, target, source, key_send, click=True,
                             read_con=False, time_out=None, tag=None):
        """一边切人, 一边给【离场角色】补技能键 —— 键只在 HUD 还读得到他时才发。

        用户 2026-08-19: "E和切人同时进行, 同时持续攻击"。

        【为什么要那道门】: 切人期间发出去的技能键会被游戏【带过换人边界, 落在新上场
        的角色身上】—— 真同时发的话那发 E 很可能变成达妮娅放的, 白烧她一层充能,
        而且小爱没变成机甲。这条在 达E1 那格已经烧过一次(所以那里改成了单发)。
        所以判据是【HUD 还显示离场角色在场 = 这一发还会落在他身上】, 一从 HUD 上
        消失就立刻停发。

        跟 switch_into_lib 是同一个机制、相反的方向: 那边要 R 落在【新】角色身上,
        所以【等】离场角色消失才开始发; 这边要 E 落在【老】角色身上, 所以他一消失
        就【停】。

        点击不受这道门约束 —— 点击落错人无所谓(顶多多推一段连段)。
        """
        if target is None:
            return False
        if target is self.cur:
            self.macro_mark(f'>{target.name[:3]}(already)')
            return True
        entry = time.time()
        time_out = self.SWITCH_TIME_OUT if time_out is None else time_out
        src = source if source is not None else (self.cur or self)
        slot = target.index + 1
        self.switch_press_at = entry
        # 【第一个数字键要抢在读协奏之前发】(quick_switch 里早就是这么做的, 这个新方法
        # 漏了 —— 用户 2026-08-19 报"小爱E切达还是快0.2s")。
        # 读协奏是能量环的连通域分析: 帧是热的时候二三十毫秒, 但【睡完之后的第一次
        # 读屏要等一张新截图, 实测能到 0.86 秒】。夹在"上一个动作"和"数字键"之间
        # 就是纯延迟, 而切人本身要 0.06~0.11 才落地, 完全来得及在换人之前读完
        self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
        con_full = bool(src.is_con_full()) if read_con else False
        team_size = len([c for c in self.task.chars if c is not None])
        n_key = n_click = 0
        untrusted_hits = 0
        arrived = False
        next_click = 0.0
        while time.time() - entry < time_out:
            now = time.time()
            self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
            self.task.next_frame()
            in_team, index, count = self.task.in_team()
            # 只要 HUD 还认为离场角色在场, 这一发技能键就还落在他身上
            if in_team and index == src.index:
                key_send()
                n_key += 1
            if click and now >= next_click:
                self.click()
                n_click += 1
                next_click = now + self.A_INTERVAL
            hit = in_team and index == target.index
            trusted = in_team and count >= team_size
            untrusted_hits = untrusted_hits + 1 if (hit and not trusted) else 0
            if hit and (trusted or untrusted_hits >= self.UNTRUSTED_CONFIRM):
                arrived = True
                break
        label = tag or f'>{target.name[:3]}+key'
        if arrived:
            self.handoff(src, target, con_full)
            self.macro_mark(f'{label}{"^" if con_full else ""}'
                            f'({time.time() - entry:.2f}|key x{n_key},A x{n_click})')
        else:
            self.logger.warning(f'switch_out_while_key: {target.name} never came in '
                                f'({n_key} keys, {n_click} clicks)')
            self.macro_mark(f'{label}FAIL(x{n_key})')
        return arrived

    def switch_into_lib(self, target, lag_presses=1, tag='R'):
        """一边疯狂切人一边预输入大招键 —— 【本文件里唯一允许技能键跨切人边界的地方】。

        用户 2026-08-16: "疯狂切达的同时就开始预输入R, 但是R的输入要比切人慢一拍,
        这样就不会误触小爱的R"。

        【为什么要这么写】: 切人期间发出去的键会被游戏带过换人边界落在新角色身上 ——
        平时这是灾难(白烧一个 CD), 这里正是要的机制: 演出/切人的那零点几秒里
        R 已经递进去了, 达妮娅一落地就出手, 省掉"等她到场再发 R"的一整段。

        【为什么要慢一拍】: 第一发 R 要是在切人键之前发出去, 场上还是爱弥斯 ——
        那就是她的大招, 整条轴当场作废。所以先发 lag_presses 次数字键, 再开始
        跟着发 R。这是这个方法唯一的安全边界, 别把 lag 调成 0。
        """
        if target is None:
            return False
        if target is self.cur:
            self.macro_mark(f'>{target.name[:3]}(already)')
            return True
        entry = time.time()
        source = self.cur if self.cur is not None else self
        slot = target.index + 1
        team_size = len([c for c in self.task.chars if c is not None])
        n_key = 0
        n_lib = 0
        untrusted_hits = 0
        arrived = False
        last = 0.0
        # 【R 的放行门】: 只要队伍 HUD 还【明确显示离场角色在场】, 就一发 R 都不发。
        # 原来是按【按键次数】lag(lag_presses=1), 而后来 SWITCH_KEY_INTERVAL 被改成 0
        # (每轮都发切人键), 于是“一拍”变成了几毫秒 —— 爱弥斯还在场上就开始
        # 收到 R, 误开她的大招(用户 2026-08-17: "爱切达还是经常容易误开爱的R1")。
        # 换成看状态就不会随发键密度漂: HUD 还读到她 = 绝对不发。
        # 另加一道时间下界, 防的是“HUD 读不到/读错”时马上就发
        r_open = False
        while time.time() - entry < self.SWITCH_TIME_OUT:
            now = time.time()
            if now - last >= self.SWITCH_KEY_INTERVAL:
                self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
                n_key += 1
                if r_open and now - entry >= self.R_PREINPUT_MIN_LAG:
                    target.send_liberation_key()
                    n_lib += 1
                last = now
            self.task.next_frame()
            in_team, index, count = self.task.in_team()
            # 离场角色已经不在 HUD 上了 -> 切人基本已经坐实, 可以开始预输入 R
            if not (in_team and index == source.index):
                r_open = True
            hit = in_team and index == target.index
            trusted = in_team and count >= team_size
            untrusted_hits = untrusted_hits + 1 if (hit and not trusted) else 0
            if hit and (trusted or untrusted_hits >= self.UNTRUSTED_CONFIRM):
                arrived = True
                break
        if arrived:
            self.handoff(source, target, False)
            self.macro_mark(f'>{target.name[:3]}+R({time.time() - entry:.2f}|'
                            f'key x{n_key},R x{n_lib})')
            if n_lib == 0:
                # 一发 R 都没递进去: 预输入没生效, 后面那句 fire_lib 会补上,
                # 只是慢一点。比误开爱弥斯的 R1 强得多
                self.logger.info('switch_into_lib: R never sent before arrival '
                                 '(HUD still showed the outgoing char) — R will be sent after')
        else:
            self.logger.warning(f'switch_into_lib: {target.name} never came in '
                                f'({n_key} keys, {n_lib} R)')
            self.macro_mark(f'>{target.name[:3]}+RFAIL(x{n_key})')
        return arrived

    def fire_lib(self, char, tag='R', window=None):
        """把大招发出去。判据是【队伍 HUD 掉下去 = 演出开始】。

        不用 click_liberation(): 它一路阻塞到演出结束才返回, 而这条轴上好几处要在
        演出【期间】继续做事(预输入普攻 / 连发 E / 疯狂切人)。

        【发键之前不读屏确认可放】: 那次读屏是纯延迟, 夹在"上一个动作"和"R键"之间;
        而且它并不能替我们做决定 —— 读到不可用我们照样要发(读数会抖, "没放出来"
        的代价远大于"多按一下")。可用性只在失败分支里读一次, 用来分辨
        "能量不够" 和 "键被动画吞了"。

        【窗口给短】: 正常一两发就进去了。给长的话万一 HUD 判据失灵, 这里会一直
        按 R —— 把后面轮到的那一发大招白按掉。宁可这一次没放出来, 也不要乱放。
        """
        window = self.LIB_FIRE_WINDOW if window is None else window
        start = time.time()
        # 【记下按 R 的时刻】: 后面几格拿它当锚点, 不拿"HUD 回来"。
        # 按键时刻是我们自己发的、精确; HUD 要读屏, 每轮抖几十毫秒。
        # 用户 2026-08-16 逐帧: 达按 R -> A 抬手 = 7s22~12s01 = 4.65 秒,
        # 同一格 HUD 消失 4.18 秒 —— 【HUD 回来之后她还要 0.47 秒才能出招】
        self.lib_press_at = start
        n = 0
        while time.time() - start < window:
            # 【先发键再读屏】(用户 2026-08-16: "QR的还是慢了一点点")。
            # 原来是"读一帧 -> 判HUD -> 才发R", 于是【每次放大招前都白读一次屏】——
            # 实机 denQ@4.49 -> denR1_fired@4.59, 那 0.10 秒全是这次读屏。
            # 而第一轮那次判断本来就没意义: 刚按完 Q, 不可能已经在演出里。
            # 多发的那一下不会有副作用 —— 演出期间的按键被丢掉、不排队
            char.send_liberation_key()
            n += 1
            # 【发键的节奏和读屏的节奏各走各的】(同 spam_key 里那条):
            # 读屏要 0.04, 挂在一起就等于把重试周期翻倍, 而 R 被接受的时刻
            # 只跟【按键什么时候落下】有关
            if n % self.LIB_READ_EVERY == 0:
                self.task.next_frame()
                if not self.task.in_team()[0]:
                    char.record_liberation_use()
                    self.macro_mark(f'{tag}_fired(x{n})')
                    return True
            self.sleep(self.LIB_KEY_INTERVAL, check_combat=False)
        # 最后再确认一次: 键刚发出去、演出还差一帧才开始的情况很常见
        self.task.next_frame()
        if not self.task.in_team()[0]:
            char.record_liberation_use()
            self.macro_mark(f'{tag}_fired(x{n})')
            return True
        self.macro_mark(f'{tag}_NOFIRE(x{n})')
        # 失败了才去读一次能量环: lit=False 是"能量不够/大招还没转好",
        # lit=True 是"键被上一个动作的动画吞了" —— 两种要改的地方完全不同
        try:
            lit = bool(char.liberation_available())
        except Exception:
            lit = 'err'
        self.logger.warning(f'{tag}: {char.name} liberation never started an animation '
                            f'after {n} presses (icon lit={lit})')
        return False

    def fire_lib_through_anim(self, char, tag='R', lead_in=0.0, spam=None,
                              poke_after_hud=False):
        """在一段【已经在演出中】的动画尾巴上把大招键连发出去。【不判定它出没出手】。

        用在清宵的天钧荡煞之后。这一格前后翻过三次车, 三条结论都记在这:

        1. 【不能用 fire_lib】: 它的判据是"HUD 掉下去 = 演出开始", 而进来的时候
           HUD 本来就是掉的(天钧荡煞隐藏 HUD 180F = 3.00 秒, 帧表 r1208) ——
           第一次读屏就假成立, 一发都没进去却记成放出去了。
        2. 【不能从头连发】(用户 2026-09-07: "R按快了"): 帧表 r1206 天钧荡煞的
           中断优先级是 6, r1242 大招-前置是 10 —— **大招能打断天钧荡煞**。
           所以这一格跟别处不同, 演出期间的按键【不是全被丢弃】, 中途会真被响应,
           把后半段伤害打断。连发的起点要用 lead_in 压到动画后段。
        3. 【也不能靠"HUD 回来过再掉下去"判出手】(用户: "R结束后愣了一会才动"):
           天钧荡煞的演出和大招的演出是【连着的】, HUD 从掉到掉中间根本不回来,
           那个判据永远不成立 —— 实机 qingR_NOFIRE(x119,8.02s), 白白愣了 8 秒。

        所以现在的形状最简单: lead_in 秒只等不发, 然后连发 spam 秒, 然后【就返回】,
        把"什么时候能做下一件事"整个交给调用方的 poke_until_hud_back ——
          · R 出手了 -> HUD 因为大招还掉着 -> 它等到演出结束, 对;
          · R 没出手 -> HUD 早回来了 -> 它 0.35 秒就返回, 也对。
        两种情况都不需要我们分辨, 也就没有超时可吃。

        Args:
            poke_after_hud: 【HUD 回来之后, 边等 R 的 CD 边点普攻】
                (用户 2026-09-08: "循环轴1 清宵处决完 R cd 没好会有一段时间愣着,
                 加上A吧, 多偷点伤害")。实机 qingR_NOFIRE 那几轮是连发满 3.5 秒
                一下不打的, 那就是他看到的"愣着"。
                【必须等 HUD 回来了才点】: HUD 还掉着 = 天钧荡煞的演出没走完,
                这时候的点击跟 R 一样会被响应, 把后半段伤害切掉(上面第 2 条)。
                HUD 一回来演出就结束了, 这之后纯粹是在等 CD, 点击白赚。
                【普攻挡不住 R】: 帧表 r1242 大招-前置 中断优先级=10, 普攻=2,
                所以 CD 一好 R 照样能把普攻打断出手 —— 老注释里"给了普攻 R 就要
                再等一个动作"那句只对天钧荡煞的演出期成立。
                【点击和 R 不共帧】: 该点的那一拍就【只点不发 R】, 下一拍再发 R,
                两个键各占各的帧(F 被 R 顶掉那次的教训, 见 spam_attack_and_f)。
        """
        spam = self.LIB_ANIM_SPAM if spam is None else spam
        start = time.time()
        n = 0
        if lead_in > 0:
            # 【这一段只等不发】: 演出期间点普攻同样会被响应, 所以也不点
            self.macro_wait(lead_in)
        # 【判定加回来了】(用户 2026-09-07: "清宵的R还是没按出来, R的持续时间
        # 再单独长一些")。窗口拉长之后, 没有判定就意味着 R 早出手了也照样把整段
        # 发完 —— 白占后面 3A 的时间。
        # 【判据: HUD 回来过之后【再】掉下去】。进来这一刻天钧荡煞可能还剩一点没演完
        # (它 3.00 秒, 而前面两段加起来 2.90), 那时 HUD 还是掉着的 —— 直接用
        # "HUD 掉了"会当场假成功。所以先等它回来一次, 之后再掉才算大招的演出。
        back_seen = False
        n_a = 0
        next_click = 0.0
        while self.time_elapsed_accounting_for_freeze(start) < lead_in + spam:
            now = time.time()
            if poke_after_hud and back_seen and now >= next_click:
                # 这一拍只点普攻, R 让给下一拍
                self.click()
                n_a += 1
                next_click = now + self.A_INTERVAL
                self.sleep(self.LIB_KEY_INTERVAL, check_combat=False)
                continue
            char.send_liberation_key()
            n += 1
            if n % self.LIB_READ_EVERY == 0:
                self.task.next_frame()
                try:
                    on_hud = bool(self.task.in_team()[0])
                except Exception:
                    on_hud = True
                if on_hud:
                    back_seen = True
                elif back_seen:
                    char.record_liberation_use()
                    self.add_freeze_duration(start, time.time() - start)
                    self.macro_mark(f'{tag}_fired(x{n}'
                                    f'{f",A x{n_a}" if n_a else ""},'
                                    f'{time.time() - start:.2f}s)')
                    return True
            self.sleep(self.LIB_KEY_INTERVAL, check_combat=False)
        # 记账: 落在 CD 上是空操作, 不花钱; 真没放出去的话下面那行日志会说
        char.record_liberation_use()
        self.macro_mark(f'{tag}_NOFIRE(x{n}{f",A x{n_a}" if n_a else ""},'
                        f'back{int(back_seen)},'
                        f'{time.time() - start:.2f}s)')
        try:
            lit = bool(char.liberation_available())
        except Exception:
            lit = 'err'
        self.logger.warning(f'{tag}: 连发 {n} 次没等到大招的演出 (icon lit={lit}, '
                            f'HUD 回来过={back_seen}) — lit=False 就是能量不够; '
                            f'back=0 说明整段 HUD 一直掉着(前一发重击还没演完)')
        return False

    def spam_key_through_lib(self, send, tag='key', time_out=None, tail=None, poke=None):
        """演出期间一直发某个技能键, 发到【演出真的结束】(HUD 回来)再补一小段。

        【为什么不能按固定时长】(爱琳莫那条轴上标定出来的): 大招哪一刻出手是浮动的 ——
        早出手时演出早结束, 固定窗口的尾巴白发; 晚出手时演出还没完窗口就停了,
        那些键一发都没进去(症状是"R完不接E")。盯 HUD 就没有这个问题。
        【tail 不能省】: HUD 回来那一刻角色多半还在收招里, 那几下照样被吞。

        【"HUD 回来"要先确认它消失过】: 上一句 fire_lib 是"HUD 一掉下去就返回"的,
        进到这里时画面正在渐变, 读一帧很可能读回 True —— 那就会在演出刚开头
        就判成"演出结束了"。所以要么先看到它真的消失过, 要么连着 NO_ANIM_GRACE
        秒都读到在场(说明这一次压根没有演出, 多半是大招没放出来)。

        Returns: 演出有没有正常结束(False 说明跑满了上限, 后面多半也不对)。
        """
        time_out = self.LIB_ANIM_TIME_OUT if time_out is None else time_out
        tail = self.LIB_ANIM_TAIL if tail is None else tail
        poke = self.poking if poke is None else poke
        start = time.time()
        n = 0
        gone_seen = False
        back_at = None
        next_click = 0.0
        while time.time() - start < time_out:
            send()
            n += 1
            now = time.time()
            if poke and now >= next_click:
                self.click()
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            on_hud = self.task.in_team()[0]
            if not on_hud:
                gone_seen = True
                back_at = None
            elif back_at is None:
                if gone_seen or time.time() - start >= self.HUD_NO_ANIM_GRACE:
                    back_at = time.time()
            elif time.time() - back_at >= tail:
                break
            # 【必须 check_combat=False】: 演出期间读不到目标, 默认的 sleep 会跑
            # 战斗判定并抛异常, 整个连发在第一轮就被打断、接着判脱战
            self.sleep(self.KEY_INTERVAL, check_combat=False)
        self.add_freeze_duration(start, time.time() - start)
        self.macro_mark(f'{tag}(x{n},{"ok" if back_at else "timeout"},'
                        f'{"anim" if gone_seen else "NOANIM"},{time.time() - start:.2f}s)')
        return back_at is not None

    def poke_until(self, deadline, tag='poke', interval=None, max_clicks=None,
                   quiet_tail=0.0):
        """一直点普攻到某个【绝对时刻】为止, 顺路记录 HUD 什么时候回来。

        【锚点是按键时刻, 不是 HUD】(用户 2026-08-16 逐帧标定):
        达妮娅按 R 到 A 抬手是 4.65 秒, 同一格 HUD 消失 4.18 秒 ——
        【HUD 回来之后她还要约 0.47 秒才能出招】。两个锚点差的是常量,
        但按键时刻是我们自己发出去的、精确到毫秒, 而 HUD 要靠 in_team() 读屏,
        每轮抖动几十毫秒 —— 所以锚在按键上。

        HUD 回来的时刻照样记进日志(hud=...), 用来盯那个 0.47 稳不稳:
        它要是每轮都在变, 说明演出长度本身有抖动, 那就得改回 HUD 锚点。
        """
        interval = self.A_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        hud_at = None
        gone_seen = False
        next_click = 0.0
        while True:
            now = time.time()
            if now >= deadline:
                break
            # 【安静尾巴】: 截止前的最后这一段不再点。
            # 目的是让最后一下点击有时间被消费掉, 而不是贴着切人键又起一个
            # 新动作把切人堵住(那个现象实测过)。
            # 【比 max_clicks 好】: 封顶次数会把“后面才能出招”的机会一并堵掉 ——
            # 千咲 A2 那格就是这么死的: 25 轮里 23 轮的 >Den 只有 0.07,
            # 说明根本没有动作挡着切人, 也就是 A2 压根没起手
            if (now >= next_click and now < deadline - quiet_tail
                    and (max_clicks is None or n < max_clicks)):
                self.click()
                n += 1
                next_click = now + interval
            self.task.next_frame()
            if not self.task.in_team()[0]:
                gone_seen = True
            elif gone_seen and hud_at is None:
                hud_at = time.time() - start
            self.macro_wait(0.01)
        # 这一段是大招演出, 计入 freeze, 否则框架会把它算成"这个角色磨蹭了几秒"
        self.add_freeze_duration(start, time.time() - start)
        self.macro_mark(f'{tag}(x{n},{time.time() - start:.2f}s,'
                        f'hud{"%.2f" % hud_at if hud_at is not None else "-"})')
        return hud_at

    def poke_until_hud_back(self, tag='anim', time_out=None, tail=None):
        """演出期间一直点普攻, 等队伍 HUD 回来(演出结束)再往下走。

        【为什么点】: 演出结束的第一帧就要有输入落进去, 而演出期间的点击是被丢掉的、
        不排队 —— 连点免疫被吞, 单发要么埋在演出里要么等太久。
        【为什么还要 tail】: HUD 回来那一刻角色多半还在收招里, 那几下照样被吞。

        用户在启动轴上明写了这一格: "达妮娅动画期间一直点普攻触发不了, 所以需要等
        一点点时间等出手再切, 或者说你识别到了HUD就可以切了"。

        【"HUD 回来"要先确认它消失过】: 同 spam_key_through_lib —— 进来的这一刻
        画面还在渐变, 读一帧很可能读回 True, 那就是"演出刚开头就判成结束"。
        """
        time_out = self.LIB_ANIM_TIME_OUT if time_out is None else time_out
        tail = self.LIB_ANIM_TAIL if tail is None else tail
        start = time.time()
        n = 0
        gone_seen = False
        back = False
        next_click = 0.0
        while time.time() - start < time_out:
            now = time.time()
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            if not self.task.in_team()[0]:
                gone_seen = True
            elif gone_seen or time.time() - start >= self.HUD_NO_ANIM_GRACE:
                back = True
                break
            self.macro_wait(0.01)
        # 演出这一段计入 freeze, 否则框架的各种 time_elapsed_accounting_for_freeze
        # 会把它算成"这个角色磨蹭了 5 秒"
        self.add_freeze_duration(start, time.time() - start)
        # 赞妮拿它判大招是不是真放出来了(见 Z_LIB_ANIM_MIN)
        self.last_anim_s = (time.time() - start) if gone_seen else 0.0
        self.macro_mark(f'{tag}({"ok" if back else "timeout"},'
                        f'{"anim" if gone_seen else "NOANIM"},x{n},'
                        f'{time.time() - start:.2f}s)')
        if tail > 0:
            self.macro_spam(tail, self.A_INTERVAL, tag=f'{tag}Tail')
        return back

    def wait_con_for_intro(self, char, next_name, poke=True, poke_after=0.0,
                           time_out=None, stall_give_up=None):
        """等协奏结算到满再交棒 —— 满协奏离场, 下一个人才有变奏入场。

        伤害打中和协奏涨上去之间有延迟, 打完立刻切就没有变奏。
        【协奏必须打中才涨】(回路条是递出去就涨, 两条规则不同的能量, 别混)。
        所以这里等不到, 往上游找哪笔伤害打空了, 而不是加大这个超时。

        Args:
            poke: 等的期间补不补普攻。默认补 —— 干站着等在画面上就是"愣了一会才切人",
                补普攻既填空白又能把协奏推快一点。【角色在空中时必须传 False】,
                那时的点击会变成下落攻击。
        """
        start = time.time()
        samples = []
        full = False
        stalled_at = None
        stalled_since = start
        next_click = 0.0
        time_out = self.CON_WAIT_TIME_OUT if time_out is None else time_out
        stall_give_up = (self.CON_STALL_GIVE_UP if stall_give_up is None
                         else stall_give_up)
        while time.time() - start < time_out:
            con = char.get_current_con()
            if len(samples) < self.CON_SAMPLE_MAX:
                samples.append(f'{time.time() - start:.2f}:{con:.2f}')
            if con >= self.CON_ENOUGH:
                full = True
                break
            # 【读数一动不动就别等满超时了】: 真不涨的时候多等的那一秒纯亏。
            # 阈值不能太小 —— 协奏会在 0.99 上卡一下再跳满
            if stalled_at is None or abs(con - stalled_at) > 0.005:
                stalled_at = con
                stalled_since = time.time()
            elif time.time() - stalled_since >= stall_give_up:
                self.macro_mark(f'con(stalled@{con:.2f})')
                self.logger.info(f'{char.name} con stuck at {con:.2f} for '
                                 f'{self.CON_STALL_GIVE_UP:.1f}s, {next_name} gets no intro')
                return False
            now = time.time()
            if poke and now - start >= poke_after and now >= next_click:
                self.click()
                next_click = now + self.A_INTERVAL
            self.macro_wait(self.CON_POLL_INTERVAL)
        self.macro_mark(f'con({"full" if full else "timeout"})')
        # 轨迹是判断"上一下打没打中"的唯一手段:
        #   一跳到顶(相邻两个采样 0.8 -> 1.00) = 打中了, 这段等待是伤害飞行时间
        #   一路慢爬 = 那一下打空了, 协奏是补刀的普攻蹭上去的 —— 位置问题, 不是时机问题
        self.logger.info(
            f'{char.name} con {" ".join(samples)} '
            f'{"(full, handing over)" if full else f"(never filled, {next_name} gets no intro)"}')
        return bool(full)

    def on_field_char(self, time_out=None):
        """现在场上是谁 —— 【只在拿不到入口角色时才用】, 因为它开局会说谎。

        in_team() 的判据是"哪个槽位的名字文本【找不到】, 谁就在场"(BaseWWTask.py:930)。
        战斗刚开始时队伍 HUD 还在渐入, 三个槽位的文本不是同时出现的 —— 这时它只找到
        一个文本, 但 exist_count == 1 那个分支【照样返回 True 和一个 index】,
        而那个 index 只是渲染顺序的产物。可信判据是 count >= 队伍人数。
        """
        time_out = self.FIELD_SETTLE_TIME_OUT if time_out is None else time_out
        expected = len([c for c in self.task.chars if c is not None])
        end = time.time() + time_out
        while True:
            self.task.next_frame()   # in_team() 读的是缓存帧, 不刷就是反复读同一张图
            in_team, index, count = self.task.in_team()
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

    def hold_into_switch(self, target, hold, tag='hold'):
        """切人的同时按住攻击键, 到场之后再按住 hold 秒。

        【按下沿必须提到切人之前】: 按住 = 先普攻、续按才变重击, 所以
          - 切完再按住 -> 新角色多出一下平A;
          - 按完立刻切 -> 上一个角色来不及吃掉这个按下沿, 新角色带着"按住"状态上场,
            同样多一下平A。
        爱琳莫那条轴上两处都栽过, 定下来的做法是"按下 -> 留一点点被离场角色消化 -> 切"。

        【一次 mouse_down 就一直有效, 包括跨过切人】—— 但期间【绝对不要】再补按:
        mouse_down 内部会投 WM_ACTIVATE, 窗口收到就把已经按住的状态打断。
        """
        self.task.mouse_down()
        self.holding_attack = True
        self.macro_mark(f'{tag}_down')
        try:
            self.macro_wait(self.PRECHARGE_TO_SWITCH)
            self.quick_switch(target, click_during=False)
            # 用户给的 2.2 秒是【从切到千咲开始】算的, 所以计时起点在这里
            self.macro_wait(hold)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}_up')

    def f_prompt_now(self):
        """自己查一次屏幕中央的处决提示 —— 【绕开框架那 1 秒的频率限制】。

        `CombatCheck.check_f_break()` 有两路判据:
          - 顶部 `f_break_full`(threshold=0.92): 每次调用都查;
          - 屏幕中央 `f_break` + `not is_pick_f()`: 【外面裹着
            `time.time() - last_break_check_time > 1`】。
        而 f_break_persist 是 0.1 秒问一次, 于是中央那一路【十次里有九次被整个跳过】。
        更糟的是宏跑的时候 task.skip_combat_check 是开着的, 战斗判定线程也不在
        刷新 can_break —— 两条腿一起瘸, 表现就是"处决从来不放"。

        这里把被跳过的那一路自己补上, 判据跟框架逐字一致(中央找到 f_break, 且不是
        拾取提示 —— F 同时是拾取/交互键, is_pick_f 就是为了区分这个)。
        """
        try:
            box = self.task.box_of_screen(0.2, 0.2, 0.75, 0.8, hcenter=True, vcenter=True)
            if self.task.find_one('f_break', box=box, target_height=720):
                return not self.task.is_pick_f()
        except Exception:
            pass
        return False

    def spam_f(self, window, tag='Fspam', interval=None):
        """【盲发 F, 盖住一整段演出】—— 不读屏、不判断。

        用户 2026-08-22: "第二个重击放动画的时候就开始F, 跟爱弥斯那里逻辑一样,
        确保动画结束第一时间接F再接R"。

        为什么要盲发: 演出期间的按键是【被丢掉的、不排队】, 所以"等演出结束再按"
        必然慢一拍(还要先读一次屏才知道结束了)。连发覆盖整段, 演出结束的第一个
        合法帧就有一发落进去 —— 这是爱弥斯那条轴上验过的形状。
        【没有处决可打时按 F 不触发别的东西】, 所以盲发是安全的。

        发完之后照例再走一遍 f_break_persist: 那个才是"提示真的亮了就连按到打完"。
        """
        interval = self.F_SPAM_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        while time.time() - start < window:
            self.task.send_key('f')
            n += 1
            self.macro_wait(interval)
        self.macro_mark(f'{tag}(x{n},{time.time() - start:.2f}s)')
        return n

    def spam_attack_and_f(self, window, interval=None, tag='AF', lib_char=None):
        """一边点普攻一边按 F。

        【为什么要一直点】(用户 2026-09-07: "得继续攻击, 可能就是因为不攻击导致
        索敌没有了, 所以循环轴那里我们一直给F加窗口也没有F")。
        这一格之前是 macro_wait 的【纯等待】—— 一下都不点, 角色丢了索敌,
        处决提示自然不出现。所以窗口从 0.4 一路加到 1.2 都没用: 治的是错的病。

        【F 是盲发的】: 这里不读屏、不判提示 —— 提示要靠"一直在打"才会出现,
        而读屏本身要花时间。打不到也不亏, 没有处决可打时按 F 不触发别的东西。

        Args:
            lib_char: 传了就【连大招键一起发】(用户 2026-09-07: "R没放出来,
                因为cd还差0.1s, 处决和R要一起连发")。大招差一点点转好时, 单独
                等处决打完再发 R 就错过了那一帧 —— 两个键一起铺, 谁先转好谁先出。

        【F 和 R 必须错开帧, 不能同一轮里挨着发】(用户 2026-09-08: "循环轴1里
        清宵第二个重击接F接R 没有接F了")。加 lib_char 之前用户验过"F处决可以了",
        加完之后 F 就不出了 —— 犯的是这条轴上栽过两次的老毛病:
        【两个键挤在同一帧, 游戏只认后按的那个】(达妮娅 E 被 R 顶掉那次, 见
        O_DEN_E_TO_R / L_DEN_E_TO_R)。原来一轮里 click -> f -> R 背靠背发出去,
        中间连一次 macro_wait 都没有, R 稳稳把 F 盖掉。
        改成【一拍 click+f, 下一拍 R】, 各占各的帧; 拍长取 interval/2,
        所以两个键的密度跟改之前的单键密度一样, 窗口的起止时刻也没变
        (那是 R 的首发时刻, 单独调过很多轮, 不能动)。
        """
        interval = self.F_SPAM_INTERVAL if interval is None else interval
        step = interval / 2.0 if lib_char is not None else interval
        start = time.time()
        nf = nr = 0
        while self.time_elapsed_accounting_for_freeze(start) < window:
            self.click()
            self.task.send_key('f')
            nf += 1
            self.macro_wait(step)
            if lib_char is not None:
                if self.time_elapsed_accounting_for_freeze(start) >= window:
                    break
                lib_char.send_liberation_key()
                nr += 1
                self.macro_wait(step)
        self.macro_mark(f'{tag}(F x{nf}{f"+R x{nr}" if lib_char else ""},'
                        f'{time.time() - start:.2f}s)')
        return nf

    def f_break_window(self, window, giveup=None, tag='F', check_every=2, interval=None):
        """盲发 F 盖住演出的【同时】盯着提示: 亮了就打完, 到 giveup 还没见到就直接走。

        用户 2026-08-22: "有时候那个时候有处决你放处决了, 当然处决没事, 但是有一次
        没处决你也慢了好久才放R"。

        原来这一段是【两段串起来的】: 先无条件盲发 spam_f(1.61秒), 再 f_break_persist
        (又 1.08 秒) —— 实机 qingZ2@46.15 -> qingR_fired@49.10 = 【2.95 秒】,
        而那一轮根本没有处决可打, 两段全是白等。
        合成一段之后: 有处决 -> 照打(那点时间值); 没处决 -> giveup 到了就走,
        不再把没有的东西等满。

        【盲发和检测各走各的节奏】: 发键每轮都发(动画期间的按键被丢掉, 只能靠密度),
        读屏每 check_every 轮才做一次(f_prompt_now 是一次中央找图, 不便宜)。
        """
        giveup = self.F_GIVEUP if giveup is None else giveup
        interval = self.F_SPAM_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        seen = False
        # 【分清"提示没读到"和"读到了却按不出去"】(2026-09-07 加, 用户要查
        # "明明可以处决了但是没有显示F"):
        #   own   = 我们自己在屏幕中央找到提示的次数(最可信的一路)
        #   stale = 进这一轮时框架的 task.can_break 就已经是 True
        #   probe = 一共查了几次(own/stale 都是 0 时用它确认"确实查过")
        own = stale = probe = 0
        while self.time_elapsed_accounting_for_freeze(start) < window:
            n += 1
            if n % check_every == 0:
                probe += 1
                pre_can = bool(getattr(self.task, 'can_break', False))
                hit = self.f_prompt_now()
                if hit:
                    own += 1
                if pre_can:
                    stale += 1
                if hit or pre_can:
                    seen = True
                    self.task.can_break = True
                    if self.f_break():
                        self.macro_mark(f'{tag}_ok(x{n},own{own},stale{stale},'
                                        f'{time.time() - start:.2f}s)')
                        return True
            self.task.send_key('f')
            if not seen and self.time_elapsed_accounting_for_freeze(start) >= giveup:
                # 【没有处决可打就别等满】: 这一段每多一秒, R 就晚一秒
                self.macro_mark(f'{tag}_skip(x{n},probe{probe},'
                                f'{time.time() - start:.2f}s)')
                return False
            self.macro_wait(interval)
        self.macro_mark(f'{tag}_none(x{n},probe{probe},own{own},stale{stale},'
                        f'{time.time() - start:.2f}s)')
        if own or stale:
            self.logger.warning(
                f'{tag}: 提示读到了 {own + stale} 次(own{own}/stale{stale}), '
                f'但 f_break() 一次都没打出去 —— 那是框架那一侧的问题, 不是窗口不够')
        return False

    def f_break_persist(self, window=None, tag='F', quiet_first=0.0):
        """在一个窗口内反复尝试处决, 而不是只赌调用那一瞬间。

        【框架的 f_break() 只在"提示当下就亮着"时才动作】(CombatCheck.f_break:
        `if self.can_break or self.check_f_break():` 不成立就直接返回 None) ——
        提示晚出来哪怕 0.1 秒, 这一次调用就整个跳过。这是"处决经常不放"的根因。
        提示一亮 f_break() 自己会连按到打完(它内部阻塞至少 0.5 秒), 所以这里
        只负责【一直问到它亮】。

        【别在切人附近调】: 击破动画带全局时停、队伍 HUD 会消失, 切人循环读到
        not in_team 会直接判脱战。轴上处决的位置都离切人有一格距离, 而且三个角色的
        check_f_on_switch 在宏开跑时就全关了(否则框架还会在切人时自己按一次)。
        """
        window = self.F_WINDOW if window is None else window
        start = time.time()
        n = 0
        own = 0
        # 【quiet_first: 开头这么久只查提示、不点普攻】
        # (用户 2026-09-07: "接处决慢了, A动作出去了才接")。
        # 症状: 进来第一轮提示还没亮(击破槽是靠上一发大招的伤害打满的, 提示比
        # 演出结束晚一点), f_break() 返回 False -> 立刻 click() 打了一下 A ->
        # 0.1 秒后才查第二次, 处决就接在那一下 A 后面了。
        # 【别给太长】: 不点会丢索敌, 提示反而更不容易出现
        while self.time_elapsed_accounting_for_freeze(start) < window:
            # 【记一下这一发是谁判出来的】(用户 2026-08-21: "莫宁也还是没处决到" ——
            # 日志明明是 morF_ok, 所以要分清是【真提示】还是【假阳性/残留标志】):
            #   stale = 进这一轮时 task.can_break 就已经是 True 了。它可能是战斗判定
            #           线程置的, 也可能是上一次没被清掉的残留 —— 后者会让 f_break()
            #           空按半秒, 画面上什么都不会发生, 但日志照样记 _ok;
            #   own   = 我们自己在屏幕中央找到了提示(最可信);
            #   fw    = 前两个都不是, 那就是框架顶部那个 f_break_full 匹配上的。
            pre_can = bool(getattr(self.task, 'can_break', False))
            # 【先自己查一遍中央提示】: 框架那一路被 1 秒频率限制挡掉了九成,
            # 查到就把 can_break 置上, 下一句 f_break() 会直接走"已经能击破"的分支。
            # 置的是框架自己的字段, f_break() 打完会把它清回 False
            own_hit = self.f_prompt_now()
            if own_hit:
                own += 1
                self.task.can_break = True
            if self.f_break():
                why = 'stale' if pre_can else ('own' if own_hit else 'fw')
                self.macro_mark(f'{tag}_ok({why},x{n},own{own},'
                                f'{time.time() - start:.2f}s)')
                return True
            # 等提示的这段也别站着 —— 处决打不打得出来跟这几下无关, 但空白很显眼
            if self.time_elapsed_accounting_for_freeze(start) >= quiet_first:
                self.click()
                n += 1
            self.sleep(self.F_RETRY_INTERVAL, check_combat=False)
        self.macro_mark(f'{tag}_none(x{n})')
        self.logger.info(f'{tag}: no break prompt in {window:.1f}s ({n} tries, '
                         f'own detector hit {own} times) -- 这一轮本来就没有处决可打')
        return False

    def wait_forte_full(self, char, tag='forte', cap=None, poke=True, interval=None):
        """边打边等【回路条满】。到上限也往下走, 不把轴卡死。

        用户 2026-08-21: "达妮娅要在变奏动画后A一下, 回路条满了再开R1"。

        判据用 `is_forte_full()`(共鸣回路那条的白色占比), 不是 `is_mouse_forte_full()`
        —— 后者是给【重击角色】用的找图判据(莫宁走那条)。日志里两路都采样,
        读不到的那一轮能直接看出是判据挑错了还是条真没满。
        【读数滞后动作约 0.7~0.8 秒】, 所以读到满的时候实际早就满了。
        """
        cap = self.FORTE_WAIT_CAP if cap is None else cap
        interval = self.A_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        full = False
        trace = []
        last_sample = 0.0
        next_click = 0.0
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            self.task.next_frame()
            try:
                full = bool(char.is_forte_full())
            except Exception:
                full = False
            now = time.time()
            if now - last_sample >= self.FORTE_SAMPLE:
                last_sample = now
                try:
                    mforte = bool(char.is_mouse_forte_full())
                except Exception:
                    mforte = False
                trace.append(f'{now - start:.1f}:f={int(full)}mf={int(mforte)}')
            if full:
                break
            if poke and now >= next_click:
                self.click()
                n += 1
                next_click = now + interval
        self.macro_mark(f'{tag}({"full" if full else "CAP"},A x{n},'
                        f'{time.time() - start:.2f}s)')
        if not full:
            self.logger.info(f'{tag}: forte never read full in {cap:.1f}s ({n} clicks) '
                             f'trace: {" ".join(trace)}')
        return full

    def attack_until_lib(self, char, window, tag='waitR', interval=None):
        """边打边盯大招能量环, 【一亮就立刻返回】。

        用户 2026-08-22: "达妮娅在循环轴里有次R亮了没及时按"。

        大招重试的间隙原来是 macro_spam(固定窗口) —— 打满了才回去看一眼能量。
        于是能量在这一段【刚打完第一下就攒够】的轮次, 要一直等到窗口跑完
        (而这个窗口后来还跟着 E 的连发一起变长了)才发得出 R。
        改成边打边看: 亮了当场退出去发 R, 没亮就把窗口打满 —— 打的这几下本来就是
        在攒能量, 不是干等。

        【读能量环不便宜】但比"白等一秒"便宜多了; 一轮点击一次读屏, 跟得上。
        """
        interval = self.A_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        lit = False
        next_click = 0.0
        while self.time_elapsed_accounting_for_freeze(start) < window:
            now = time.time()
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + interval
            self.task.next_frame()
            try:
                if char.liberation_available():
                    lit = True
                    break
            except Exception:
                pass
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}({"lit" if lit else "cap"},A x{n},'
                        f'{time.time() - start:.2f}s)')
        return lit

    def wait_con_at_least(self, char, level, tag='con', cap=None, interval=None, poke=True):
        """边打边等协奏圈涨到 level 以上(0~1)。到上限也往下走, 不把轴卡死。

        跟 wait_con_for_intro 的区别: 那个是"等满才交棒"(门槛是 CON_ENOUGH=0.99,
        【那个值是"环还没闭合"的哨兵, 不是真满】—— 要真满就把 level 传 1.0), 这个是
        【等到某个门槛才做下一个动作】—— 用户 2026-08-21: "达妮娅有时候被打断,
        能不能确保协奏圈超过75%才开R2Q切人"。

        【为什么是门槛不是满】: R2 和 Q 自己还会把圈推上去一截, 所以起手时到 75%
        就够她离场时是满的。等满再开 R2 反而是把这一截白等掉。

        【读协奏不便宜】(能量环连通域分析), 所以轮询间隔别调太小。
        【等的期间必须继续打】: 圈是靠打中涨的, 干站着等只会等到超时。
        """
        cap = self.CON_WAIT_TIME_OUT if cap is None else cap
        interval = self.CON_POLL_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        con = 0.0
        hit = False
        samples = []
        next_click = 0.0
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            try:
                con = float(char.get_current_con())
            except Exception:
                con = 0.0
            if len(samples) < self.CON_SAMPLE_MAX:
                samples.append(f'{time.time() - start:.2f}:{con:.2f}')
            if con >= level:
                hit = True
                break
            now = time.time()
            if poke and now >= next_click:
                self.click()
                n += 1
                next_click = now + self.A_INTERVAL
            self.macro_wait(interval)
        self.macro_mark(f'{tag}({con:.2f},{"ok" if hit else "CAP"},A x{n},'
                        f'{time.time() - start:.2f}s)')
        if not hit:
            self.logger.info(f'{tag}: con only reached {con:.2f} (< {level:.2f}) in '
                             f'{cap:.1f}s — {" ".join(samples)}')
        return hit

    def wait_hud_back(self, tag='anim', time_out=None, tail=0.0):
        """等演出结束(队伍 HUD 回来), 【期间一下都不点】。

        跟 poke_until_hud_back 的唯一差别就是不点普攻 —— 【角色在空中时必须用这个】:
        那时候的点击会变成下落攻击, 一下就把她砸回地面, 后面整段空中戏就没了。

        【"HUD 回来"要先确认它消失过】: 进来的这一刻画面可能还在渐变, 读一帧很可能
        读回 True, 那就是"演出刚开头就判成结束"。
        """
        time_out = self.LIB_ANIM_TIME_OUT if time_out is None else time_out
        start = time.time()
        gone_seen = False
        back = False
        while time.time() - start < time_out:
            self.task.next_frame()
            if not self.task.in_team()[0]:
                gone_seen = True
            elif gone_seen or time.time() - start >= self.HUD_NO_ANIM_GRACE:
                back = True
                break
            self.macro_wait(0.01)
        self.add_freeze_duration(start, time.time() - start)
        self.macro_mark(f'{tag}({"ok" if back else "timeout"},'
                        f'{"anim" if gone_seen else "NOANIM"},{time.time() - start:.2f}s)')
        self.macro_wait(tail)
        return back

    def wait_forte_e(self, char, tag='forteE', cap=None, poke=True, interval=None):
        """边打边等【回路条右边那一格的 E 图标亮】= 强化技能准备好了。

        用户 2026-09-06: "特别是回路条右边的识别, 基本上回路条满了它就会亮,
        一般会指代强化攻击或者强化技能已经准备好了"。
        判据 is_e_forte_full() = task.find_e_forte()(二值化后找图), 跟重击那一路
        (is_mouse_forte_full = find_mouse_forte)是同一排的两个格子。

        【到上限也往下走】: 这个图标在连续出招期间常常渲染不出来, 拿它当硬门会白等 ——
        它的价值是"亮了就立刻走", 不是"没亮就不许走"。
        """
        cap = self.FORTE_WAIT_CAP if cap is None else cap
        interval = self.A_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        lit = False
        trace = []
        last_sample = 0.0
        next_click = 0.0
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            self.task.next_frame()
            try:
                lit = bool(char.is_e_forte_full())
            except Exception:
                lit = False
            now = time.time()
            if now - last_sample >= self.FORTE_SAMPLE:
                last_sample = now
                try:
                    bar = bool(char.is_forte_full())
                except Exception:
                    bar = False
                trace.append(f'{now - start:.1f}:e={int(lit)}bar={int(bar)}')
            if lit:
                break
            if poke and now >= next_click:
                self.click()
                n += 1
                next_click = now + interval
        self.macro_mark(f'{tag}({"lit" if lit else "CAP"},A x{n},'
                        f'{time.time() - start:.2f}s)')
        if not lit:
            self.logger.info(f'{tag}: e_forte never lit in {cap:.1f}s ({n} clicks) '
                             f'trace: {" ".join(trace)}')
        return lit

    def send_echo_now(self, char, tag='Q', presses=2, window=0.0):
        """立刻放 Q。【三个角色统一走这里, 而且都放在离场前的最后一步】
        (用户 2026-08-21: "因为声骸套装不一样, 统一设定最后放")。

        【不读屏】: click_echo(time_out=0) 的第一件事是读屏 echo_available(),
        读到 False 就掉进慢路径 —— 那条路要 OCR 一次 echo 的 CD, 再轮询最多 1 秒。
        而 Q 的位置个个都在处决/大招刚结束那一带, 那几帧的读数正是最不可信的时候。
        发键不读屏: 落在 CD 上是空操作, 不花钱。
        """
        self.spam_key(char.send_echo_key, window, interval=0.0, tag=tag,
                      presses=presses, poke=False)
        char.record_echo_use()


    # ==================== 赞妮: 开局那一格(只有启动轴有) ====================

    def open_zani_guard(self, ver):
        """组合1「赞妮防反+普攻」-> 切维里奈。视频 469.60 E / 469.87 A / ~470.2 切人。

        【这一下 E 是防反不是输出】: 视频 225 秒作者说"不少人被古龙开局这个拍掌+地刺
        搞得很烦" —— 标准防御协议正好吃下开场那一套, 同时把强化 E 攒出来。
        【不等它出手】: 后面那一下 A 本来就要落在 E 的动作里, 是用来把协奏推起来的,
        不是要它单独打一段。
        """
        self.poking = False
        self.spam_key(self.send_resonance_key, self.O_ZANI_GUARD_E_WINDOW,
                      interval=self.O_ZANI_GUARD_E_INTERVAL, tag='zaniE防反', poke=False)
        self.record_resonance_use()
        self.macro_wait(self.O_ZANI_E_TO_A)
        self.macro_spam(self.O_ZANI_A_WINDOW, tag='zaniA')
        # 【开局协奏一定不满, 所以这里是普通切人不是变奏】—— read_con 传 False,
        # 省掉一次能量环连通域分析(那是纯延迟, 结论还是已知的)
        return self.quick_switch(ver)

    # ==================== 维里奈段(启动/循环共用) ====================

    def seg_verina(self, ver, pho):
        """组合2「维里奈 AAA / 技能+声骸+大招 / 跳A打满协奏」-> 变奏菲比。

        视频两圈的分格一模一样(470.37~476.0 和 511.50~518.0), 所以两条轴共用这一段。
        【顺序是 E -> Q -> (F处决) -> R -> 跳x2 -> 空中A】。跟视频比少了开头的 3A
        (用户 2026-09-09 要求去掉, 见 V_E_CAP 上面那段), 跟内置 Verina 的
        3A -> R -> E -> Q 比则是把 E/Q 提到了 R 前面 —— 光合作用那一格先落地,
        大招的治疗和增益才盖在已经铺开的场上。

        【末尾必须等协奏满】: 满协奏离场菲比才有变奏入场, 而"菲比变奏中长按E"是
        下一段的第一个动作。等不到就往上游查哪一下打空了, 不要加大这个超时。

        【全队唯一的处决在这一段的 Q 和 R 之间】(用户 2026-09-08 从赞妮 R2 那一格
        挪过来的): 她这一段本来就是站桩, 处决插进去不挤任何东西; 而且赞妮/菲比的
        内置 f_break 都是 return False, 只有她这里按得出来。
        """
        self.poking = False
        # 【3A 去掉了, 入场直接发 E】(用户 2026-09-09: "把维里奈的3A去掉直接接E
        # 后面的流程")。视频里她是 3A-E-Q-R, 但那 1.8 秒的普攻在实机上只是把 E
        # 往后推 —— 光合作用那一格越早落地, 后面的 Q/R 才越早接上。
        # 【发到进 CD 为止】: 入场动画会把单发的 E 吞掉(视频里点两下就是为了保险),
        # 判据驱动比按次数发更准; poke=True 让等的那几拍顺手攒点协奏
        self.press_e_until_cd(ver, self.V_E_CAP, tag='verE', poke=True)
        ver.record_resonance_use()
        self.macro_wait(self.V_E_TO_Q)
        self.send_echo_now(ver, tag='verQ', window=self.V_Q_WINDOW)
        self.macro_wait(self.V_Q_TO_F)
        # 【处决放在这里】(用户 2026-09-08 把它从赞妮 R2 那一格挪过来的), 见 V_F_WINDOW
        if self.f_break_now(self.V_F_WINDOW, tag='verF'):
            # 【打出处决之后必须等 HUD 回来再发 R】: 击破动画带全局时停 + 队伍 HUD
            # 消失, 而 fire_lib 的判据正是"HUD 掉下去 = 演出开始" —— 撞在击破动画上
            # 会当场把 R 判成假成功(那一发大招其实压根没放出去)。
            # 【等的时候不点】: task.f_break() 内部自己就在连打左键
            self.wait_hud_back(tag='verFanim', time_out=self.V_F_ANIM)
        self.macro_wait(self.V_F_TO_R)
        if not self.fire_lib(ver, tag='verR'):
            # 【没放出来不是死局】: 维里奈的 R 是治疗+增益, 漏一发这一圈照样打得完。
            # 记一笔往下走, 别把整条轴卡在这里等能量
            self.logger.info('verina R did not start an animation, carrying on')
        self.poke_until_hud_back(tag='verRanim', tail=self.V_R_TAIL)
        # 跳两下: 视频 474.37 / 474.67 两次高亮。二段跳把人抬到能连打空中A 的高度
        self.task.jump(after_sleep=0)
        self.macro_wait(self.V_JUMP_GAP)
        self.task.jump(after_sleep=0)
        self.macro_wait(self.V_JUMP_SETTLE)
        self.macro_spam(self.V_AIR_A_WINDOW, tag='verAirA')
        # 门槛用 CON_FULL_LEVEL(1.0) 而不是引擎默认的 CON_ENOUGH(0.99) —— 后者是
        # "环还没闭合"的哨兵值, 见常量区那段说明。维里奈这一格协奏本来就富余
        # (视频里最后一下 A 到切人只隔 0.27 秒), 换成严门槛不会拖慢
        self.wait_con_at_least(ver, self.CON_FULL_LEVEL, tag='verCon', cap=self.V_CON_CAP)
        return self.quick_switch(pho, read_con=True)

    # ==================== 菲比: 过场那一秒(只有循环轴有) ====================

    def loop_phoebe_pass(self, pho, ver):
        """「菲比落地啥也不用干」-> 切维里奈。视频 510.5~511.5, 整整一秒只按了一下 A。

        【这一格不能省】: 它的意义是接住赞妮 R2 的变奏, 顺手把回路能量垫掉一半
        (视频 427 秒作者原话"这样垫一半的回路能量") —— 下一次她上场长按 E 才能
        一口气把回路充满、立刻开大招。
        【也不能在这里打输出】: 多打几下会把协奏推满, 那就变成"变奏切维里奈",
        白白浪费一次变奏, 而且维里奈的入场动画会把她自己的 3A 挤掉半段。

        【先等落地再点】: 变奏入场是滞空的, 这时候的点击会变成下落攻击。
        """
        self.poking = False
        self.macro_wait(self.P_PASS_SETTLE)
        self.macro_spam(self.P_PASS_A_WINDOW, tag='phoPassA')
        return self.quick_switch(ver)

    # ==================== 菲比: 输出两套(启动/循环共用) ====================

    def seg_phoebe(self, pho):
        """组合3「菲比变奏中长按E / 看见回路变色开大招 / AAA重击 打两套」-> 变奏赞妮。

        视频 476.0~487.0 和 518.0~529.1。

        【长按 E 直接调内置的 absolution_or_confession()】: 它做的就是"固定长按 1.2 秒
        进告解形态", 跟视频里量到的 1.16 / 1.40 秒是同一件事, 而且内置版还带两态
        资源缓存(有充能就不白按)和滞空兜底。自己写一遍只会少掉那些兜底。

        【AAA+重击 直接调内置的 starflash_combo()】: 左键点到重击图标亮 -> 长按放
        重击星辉, 正是视频 294/300 秒说的"A三下一个重击 / 长按攻击键也可以直接放
        重击星辉"。这一格【必须读屏】—— 图标什么时候亮取决于打中了几下, 按秒表写死
        会在漏刀的那一轮把长按落在图标没亮的时候, 那一下就只是普通重击。

        【两套重击是保底】: 打空了就重来(最多 P_HEAVY_MAX_TRIES 次), 判据是
        state['starflash_combo'] 有没有涨 —— 用户 2026-09-08 明确要求
        "菲比就一定要保底打出两个强化重击"。
        【两套之间穿插一发 E】(用户 2026-09-09: "菲比第一个重击之后穿插一个E"):
        短按, 镜之环顺手把怪定住, 第二个重击更容易打实。

        【收尾的 Q 和补协奏在 phoebe_finish() 里】—— 那两步视频上都看不出来,
        理由写在那个方法的说明里。
        """
        self.poking = False
        self.macro_wait(self.P_INTRO_TO_HOLD)
        # team/attribute 决定长按走 E 还是走左键(告解 vs 赦罪), 这里刷一次免得读残值
        self.native_call(pho, 'decide_teammate')
        # 【走 native_call 不直调】: v3.6.9-beta.1 的 absolution_or_confession()
        # 没有 dodge_cancel 参数, 直调当场 TypeError 把整条轴打断(2026-09-26 实例)。
        # 【dodge_cancel 干脆不传】—— 我们要的就是内置默认那套, 传了也只是重复
        status = self.native_call(pho, 'absolution_or_confession', wait_team=False)
        self.macro_mark(f'phoHoldE({status})')
        self.macro_wait(self.P_HOLD_E_TO_R)
        # 【判形态进没进去看 star_available, 不要看 is_forte_full()】: 菲比重写过
        # is_forte_full —— star_available 之后它读的是【重击图标】(给 starflash_combo
        # 用的), 不是回路条。拿它当"回路变色了没"会读到一个语义完全不同的东西。
        # 形态没进去(长按被动画吞了 / 落地前就松手了)就补打一小段: 大招本身不要求
        # 形态, 但没形态的重击是普通重击, 后面两套的收益差一大截
        if not pho.star_available:
            self.macro_mark('phoNoForm')
            self.macro_spam(self.P_FORTE_CAP, tag='phoFill')
            self.native_call(pho, 'check_middle_star')
        if not self.fire_lib(pho, tag='phoR'):
            self.logger.info('phoebe R did not start an animation, carrying on')
        self.poke_until_hud_back(tag='phoRanim', tail=self.P_R_TAIL)
        # 【两套强化重击是保底: 打空了就重来, 不是"走两遍循环"】
        # (用户 2026-09-08: "菲比就一定要保底打出两个强化重击")。
        # 判据用 state['starflash_combo'] 有没有涨 —— starflash_combo() 内部只有
        # 确认打出重击(cast)才 +1; 图标没亮/滞空/被打断时它会静默走完, 计数不动
        done = 0
        tries = 0
        while done < self.P_HEAVY_SETS and tries < self.P_HEAVY_MAX_TRIES:
            tries += 1
            before = pho.state.get('starflash_combo', 0)
            self.native_call(pho, 'check_middle_star')
            # 【自己先把重击图标点亮, 好让 starflash_combo 跳过它自带的那一下右键】
            # (用户 2026-09-08 报"菲比闪避的太早了")。两个版本的 starflash_combo
            # 都是 充能 -> 【右键】 -> 长按重击, 那一下闪避落在【重击之前】;
            # 而视频里的闪避在【重击之后】(483.67 / 486.47), 作用是取消后摇。
            # 位置反了不只是难看: 闪避带位移, 紧接着的重击很可能打空 ——
            # 那也正是协奏一直补不满的根因(协奏要打中才涨)。
            # 图标先亮着, 它进来就整段跳过充能, 右键也跟着跳过。
            # 【为什么不用 _charge_starflash_until_full】: v3.6.9 的重构把那个方法
            # 删了, 见 phoebe_charge_heavy
            if not pho.is_forte_full():
                self.phoebe_charge_heavy(pho, self.P_CHARGE_CAP)
            self.native_call(pho, 'starflash_combo')
            if pho.state.get('starflash_combo', 0) > before:
                done += 1
                # 【不闪避】(用户 2026-09-08: "菲比的闪避取消掉, 感觉有浪费时间,
                # 需要闪避可以人工干预, 反正那里一直是重击")。视频里每套重击之后
                # 确实有一下闪避(483.67 / 486.47, 取消后摇), 但实机上那点收益
                # 抵不掉它占的时间; 这一格现在只认一件事: 两个强化重击打出来了没有
                self.macro_mark(f'phoHeavy{done}(try{tries})')
                if done < self.P_HEAVY_SETS:
                    # 【第一个重击之后穿插一个 E】(用户 2026-09-09)。
                    # 【必须短按】: send_resonance_key 是 send_key(down_time=0.01),
                    # 单发就是短按; 长按 E 是"进/切告解形态", 按长了会把形态资源
                    # 白烧一次 —— 而两个强化重击正好各吃一次, 烧掉一次就凑不齐了。
                    # 【E 在 CD 上就跳过】: 记一笔往下走, 不在这里等 ——
                    # 收尾那边(phoebe_finish)还有一发 E, 两处共用同一个 CD
                    # 【走 phoebe_short_e, 不能用 press_e_until_cd】——
                    # 她的 E 是两段的, "发到进CD为止"会按到第二段(传送), 见常量区。
                    # 那个方法内部挑版本: v3.6.9 用 click_resonance_once,
                    # v3.6.7 用 _insert_control_e, 都没有就裸发一次
                    if pho.resonance_available():
                        clicked = self.phoebe_short_e(pho)
                        # 【内置那两个实现里都有 sleep, 会把 skip_combat_check
                        # 置回 False】(BaseChar.sleep 结尾无条件置 False),
                        # 后面还有切人要走, 补回来
                        self.task.skip_combat_check = True
                        pho.record_resonance_use()
                        self.macro_mark(f'phoMidE({"ok" if clicked else "补发"})')
                        self.macro_wait(self.P_HEAVY_GAP)
                    else:
                        self.macro_mark('phoMidE(cd)')
                        self.macro_wait(self.P_HEAVY_GAP)
            else:
                # 【这一次没打出来】: 多半是重击图标没亮(充能超时)或者人在空中。
                # 【不闪避】—— 没打出重击就没有后摇要取消, 那一下闪避只会把人带走
                self.macro_mark(f'phoHeavy_miss(try{tries},'
                                f'star={int(bool(pho.star_available))},'
                                f'forte={int(bool(pho.is_forte_full()))})')
                self.phoebe_ground(pho)
        if done < self.P_HEAVY_SETS:
            self.logger.warning(f'zanfei: phoebe only landed {done}/{self.P_HEAVY_SETS} '
                                f'heavy hits in {tries} tries — 形态资源用完了, '
                                f'或者重击图标一直没亮(站位打空)')
        # 【第二个重击打完直接进收尾】: 不留缓冲, 见 P_CON_FULL_CAP 那段说明
        return self.phoebe_finish(pho)

    def phoebe_finish(self, pho):
        """菲比的收尾: (协奏没满就补) -> Q -> 变奏赞妮。

        用户 2026-09-09: "菲比第二个强化重击打出去要马上接Q切人"。
        所以【正常情况下这里就是"重击 -> Q -> 切"三步连着走】—— 两个强化重击加上
        中间穿插的那发 E, 协奏一般已经满了, wait_con_at_least 第一次读就返回。

        【补协奏那一步不能省, 但它只在没满时才花时间】: 满协奏离场赞妮才有变奏入场,
        而"赞妮变奏中连按E"是下一段的第一个动作。门槛必须是 CON_FULL_LEVEL(1.0)
        不是 CON_ENOUGH(0.99) —— 后者是"环还没闭合"的哨兵值, 拿它等就是差一丝丝,
        详见常量区那段说明。

        【Q 是补回来的, 视频里没有】(2026-09-08): 逐帧查过视频那两段菲比, 声骸框
        一次都没亮。但内置 `Phoebe.switch_next_char` 里【本来就挂着一发
        `click_echo()`】, 位置正是"满协奏离场之前"; 宏用 quick_switch 直接按数字键
        切人, 绕开了那个方法, 于是那一发跟着一起丢了。放在离场前的最后一步也跟别的
        队伍一致(用户 2026-08-21: "因为声骸套装不一样, 统一设定最后放")。

        【满不了也要切】: 赞妮那一段的第一个动作是"变奏中连按E", 没变奏照样能跑
        (只是少一次入场增益)。卡在这里等下去更亏 —— 日志里 con 的采样轨迹会告诉
        我们是"打空了"(一路慢爬)还是"结算延迟"(一跳到顶)。
        """
        full = self.wait_con_at_least(pho, self.CON_FULL_LEVEL, tag='phoConFull',
                                      cap=self.P_CON_FULL_CAP,
                                      interval=self.P_CON_POLL)
        # Q —— 离场前的最后一步
        self.send_echo_now(pho, tag='phoQ', window=self.P_Q_WINDOW)
        if not full:
            self.logger.info(f'zanfei: phoebe concert never read full in '
                             f'{self.P_CON_FULL_CAP:.1f}s — Zani gets no intro this round; '
                             f'看上面那行 phoConFull 的采样: 一路慢爬=重击打空了(站位), '
                             f'停在 0.9x=读数问题')
        return self.quick_switch(self, read_con=True)

    # ==================== 赞妮: 连按E -> 开大 ====================

    def seg_zani_into_lib(self):
        """组合4 的前半「赞妮变奏中连按E / 劈出一剑开大招」。

        视频 487.13~491.57 和 529.17~534.97 是: 连按E(x4) -> Q(x4) -> 连按E(x3~6)
        -> (劈出一剑的动画) -> R。

        【两轮 E 是两个不同的技能】: 第一轮是普通 E(标准防御协议), 打完之后回路条
        右边那格亮起来 = 强化 E(危机应对协议)就绪; 第二轮那一下才是"劈出一剑"。

        【但宏这边不按三段走, 而且不判据】(用户 2026-09-09 先要"入场就开始不停按E,
        第一时间把强化E按出来", 判据版试了三轮之后又定"你也不用判断了, 就连发12sE
        肯定能按到"): 整段并成一个 zani_spam_e —— 盲发 E + 一直点普攻(攒回路),
        该出普通E时出普通E, 回路一满下一拍自己就是那一剑。
        视频那三段中间有近一秒的固定间隔和一个 Q, 每一格都在推迟强化E; 而赞妮是
        近战, 前面的普攻本来就容易打空, 拖得越久越容易整段跑偏。
        【Q 跟着挪到强化E之后, 而且跟补能量那 2 秒【叠在一起】】
        (用户 2026-09-09: "持续2S的E和Q不冲突, Q不打断动作") —— 声骸不打断当前动作,
        所以不用单开一格; 照样赶在 R 之前, 大招增益一样吃得到。
        【那一段必须照常点普攻】(用户 2026-09-09: "赞妮强化E按了没有马上A出去"):
        引擎的 send_echo_now 里 poke 是写死 False 的, 直接拿来用会留一段空白 ——
        所以这一格没用它, 见 zani_spam_e。
        """
        self.poking = False
        # 【入场一直按 E, 按到强化E出手】—— 见 Z_E_HOLD_CAP 那段说明
        self.zani_press_e_until_enhanced(self.Z_E_HOLD_CAP)
        # 【那一剑之后持续按 2 秒 E 补能量, Q 就在这一段里一起发】
        # (用户 2026-09-09 指定)。这才是"R 前面愣一下"的正解: 补的是普通E,
        # 它的 CD 这会儿刚好转好; 而 Q 不打断动作, 叠进来不多花一秒
        self.zani_spam_e(self.Z_E_AFTER_ENHANCED, tag='zaniE补', echo=True)
        # 【发 R 之前先确认 HUD 在场】—— 见 Z_R_HUD_CAP, 不确认的话 fire_lib 会假成功
        self.wait_hud_up(self.Z_R_HUD_CAP, tag='zaniRhud')
        fired = self.fire_lib(self, tag='zaniR')
        if not fired:
            # 【保底: 能量不够就一边按 R 一边按 E】(见 Z_R_RESCUE_WINDOW)。
            # 【比"打一段再重试"强在哪】: attack_until_lib 那条路只是普攻攒能量,
            # 而赞妮的能量大头来自 E —— 普攻攒到猴年马月, 补一发普通 E 立刻就够了
            self.attack_until_lib(self, self.Z_R_RETRY_A, tag='zaniRwait')
            fired = self.fire_lib(self, tag='zaniR2nd')
            if not fired:
                fired = self.zani_r_with_e(self.Z_R_RESCUE_WINDOW, tag='zaniR救')
        self.poke_until_hud_back(tag='zaniRanim', tail=self.Z_R_TAIL)
        # 【确认真的进了大招, 别信 fire_lib 一个人说了算】(用户 2026-09-09:
        # "赞妮怎么不放R1了")。fire_lib 的判据是"HUD 掉下去", 撞上别的演出就会
        # 假成功 —— 前面 wait_hud_up 已经堵了一次, 这里再验一次收尾:
        # 看 HUD 消失了多久, 不够一段大招演出就当假成功再救一轮。
        # 【不读图标栏】: check_liber 在现在的 HUD 上开了大招也判"不在", 见 Z_LIB_ANIM_MIN
        if fired and self.last_anim_s < self.Z_LIB_ANIM_MIN:
            self.macro_mark(f'zaniR假成功(anim{self.last_anim_s:.2f})')
            self.logger.warning('zanfei: fire_lib reported success but we are not in '
                                'liberation — 那一发多半被别的演出骗了, 再救一轮')
            fired = self.zani_r_with_e(self.Z_R_RESCUE_WINDOW, tag='zaniR救2')
            if fired:
                self.poke_until_hud_back(tag='zaniRanim2', tail=self.Z_R_TAIL)
        if fired:
            self.mark_liberation_started()
        return fired

    def zani_press_e_until_enhanced(self, cap, tag='zaniE'):
        """入场就一直按 E + 一直点普攻, 直到【强化E("劈出一剑")真的出手】。

        机制: 普通E(标准防御协议)先出去 -> 普攻攒回路 -> 回路条右边那格亮起来
        (is_e_forte_full) = 强化E(危机应对协议)就绪 -> 再按 E 就是那一剑。
        【一直按的好处】: 每一发都可能是"当前该出的那一发", 落在 CD 或动画里被吞
        也不亏; 图标一亮, 下一拍那一发就是强化E —— 不用等窗口、不用先读屏再决定。
        【一直点普攻是必须的】: 回路是打中攒的, 赞妮又是近战 —— 不点就永远不亮。

        【判"出手了"用"亮过又灭"】: 图标灭 = 那一剑吃掉了这次充能。
        只判"亮着"不行 —— 亮着的时候那一剑还没递出去, 立刻走会把后面的 Q 和 R
        全塞进起手帧里。这也是"判某状态消失之前必须先确认它出现过"那条的正用。
        【前 Z_E_MIN 秒不判灭】: 入场那几帧 HUD 还在渐入, 读数不可信。
        【HUD 不在场时什么都不判】: 变奏入场动画/演出期间整个 HUD 都没渲染出来,
        那时 is_e_forte_full() 恒假, 会把"还没亮过"误判成"灭了"。

        到 cap 还没出手就往下走 —— 后面还有 zani_spam_e 补能量、fire_lib 失败还有
        zani_r_with_e 那道保底。
        """
        start = time.time()
        ne = na = anim = 0
        lit_seen = False
        fired = False
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            self.task.next_frame()
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if not bool(self.task.in_team()[0]):
                anim += 1
            else:
                lit = bool(self.is_e_forte_full())
                if lit:
                    lit_seen = True
                elif lit_seen and elapsed >= self.Z_E_MIN:
                    fired = True
                    break
            self.send_resonance_key()
            ne += 1
            self.click()
            na += 1
            self.macro_wait(self.Z_E_STEP)
        # crisis_time 是内置版记"危机应对协议什么时候按下去的",
        # should_end_liberation / wait_crisis_protocol_end 都读它。宏绕开了内置的
        # 入口(crisis_response_protocol_combo), 这里补上
        self.crisis_time = time.time()
        self.record_resonance_use()
        self.macro_mark(f'{tag}({"劈出" if fired else "CAP"},E x{ne},A x{na},'
                        f'anim{anim},{time.time() - start:.2f}s)')
        if not fired:
            self.logger.warning(
                f'{tag}: 强化E 在 {cap:.1f}s 内没确认出手 (E x{ne} / A x{na} / '
                f'{anim} 轮在动画里, 图标亮过={lit_seen}) —— 亮过=没确认到那一剑落地, '
                f'没亮过=回路压根没攒起来(近战打空了)')
        return fired

    def zani_spam_e(self, window, tag='zaniE补', echo=False):
        """在一个窗口里一直发 E(可选顺带发 Q) + 一直点普攻, 【不判任何东西】。

        用在【强化E出手之后】(用户 2026-09-09: "就加一条强化E放完再持续按2sE就好")。
        补的是【普通E】: 赞妮的大招能量大头来自共鸣技能, 强化E打完时能量常常还差
        一点点 —— 那就是 R 前面"愣一下"的来源; 而这会儿普通E的 CD 也差不多转好了,
        补一发能量就满, R 立刻接得上。

        【Q 叠在同一段里】(用户 2026-09-09: "持续2S的E和Q不冲突, Q不打断动作"):
        声骸不打断当前动作, 所以不用单开一格 —— 省下的是整整一个 Q 窗口。
        【但两个技能键必须错开帧】: 挤在同一轮里游戏只认后按的那个(达妮娅那条轴上
        E 被 R 顶掉栽过两次)。所以一拍 E、一拍 Q, 各占各的帧, 普攻两拍都点。

        【这一段不判据】: 判"E 有没有进 CD"要读技能圈, 而这一格正压在强化E的劈砍
        动画上, HUD 和图标都读不准(那条坑在这个文件里踩过三次)。反正 E 落在 CD 上
        是空操作、落在动画里被吞也不亏, 发满窗口最省事。
        【但窗口要短】: 长了会在回路又攒满时放出【第二个强化E】, 焰光的账和后面
        三终夜的节奏全乱 —— 2 秒是用户量的, 别随手加大。
        """
        step = self.Z_E_STEP
        start = time.time()
        ne = na = nq = 0
        echo_left = self.Z_Q_PRESSES if echo else 0
        while self.time_elapsed_accounting_for_freeze(start) < window:
            # 一拍: E
            self.send_resonance_key()
            ne += 1
            self.click()
            na += 1
            self.macro_wait(step)
            if self.time_elapsed_accounting_for_freeze(start) >= window:
                break
            # 下一拍: Q(只发前 Z_Q_PRESSES 次), 没有 Q 要发就只点普攻
            if echo_left > 0:
                self.send_echo_key()
                nq += 1
                echo_left -= 1
            self.click()
            na += 1
            self.macro_wait(step)
        if nq:
            self.record_echo_use()
        self.record_resonance_use()
        self.macro_mark(f'{tag}(E x{ne}{f",Q x{nq}" if nq else ""},A x{na},'
                        f'{time.time() - start:.2f}s)')
        return True



    def wait_hud_up(self, cap, tag='hudUp', poke=True):
        """等队伍 HUD 回到屏幕上, 期间照常点普攻。返回等到了没有。

        【发大招之前必须先过这一关】(用户 2026-09-09: "赞妮怎么不放R1了"):
        fire_lib / fire_lib_through_anim 的判据都是"HUD 掉下去 = 演出开始" ——
        如果按 R 的那一刻 HUD 本来就不在(上一个动作的演出、特效、全局时停),
        它会在第二次发键之后当场判成功: 日志记 R_fired, 实际上 R 一次都没按出去,
        后面整段爆发跟着落空, 而且 mark_liberation_started 还会把状态标成"在大招里"。
        跟 wait_hud_back 的区别: 那个要先确认 HUD 【消失过】(用来等一段已知的演出
        结束), 这个只关心"现在它在不在"—— 在就立刻走, 不在就等。

        【等的时候照常点普攻】: 这一格前面是强化E的劈砍, 后面是大招, 中间干等最亏。
        """
        start = time.time()
        n = 0
        up = False
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            self.task.next_frame()
            if bool(self.task.in_team()[0]):
                up = True
                break
            if poke:
                self.click()
                n += 1
            self.macro_wait(0.05)
        self.macro_mark(f'{tag}({"ok" if up else "CAP"},A x{n},'
                        f'{time.time() - start:.2f}s)')
        if not up:
            self.logger.warning(f'{tag}: 队伍 HUD 在 {cap:.1f}s 内一直没回来 —— '
                                f'接下来那一发大招的"出手"判据不可信')
        return up

    def zani_r_with_e(self, window, tag='R+E'):
        """R 放不出来时的保底: R 和 E 交替连发, 中间照常点普攻。

        用户 2026-09-08: "如果R没按出来就再按个普通E这样能量就满了, 也就是正常按R的
        同时按E, 两个键连发一段时间"。

        【为什么补 E 而不是继续普攻】: 赞妮的大招能量大头来自共鸣技能, 普攻只是零头。
        走到这里说明强化E那一剑没劈出来(或者劈了但能量还差一点) —— 补一发普通 E
        立刻就够, 继续 A 得攒很久。

        【两个键必须错开帧, 不能同一轮里挨着发】: 挤在一起游戏只认后按的那个。
        这条在达妮娅那条轴上栽过两次(E 被 R 顶掉)。所以一拍 R + 点击, 下一拍 E + 点击。

        【E 只在图标亮着时发】: 落在 CD 上是空操作, 白占一拍。
        【判据跟 fire_lib 一样】: 队伍 HUD 掉下去 = 演出开始 = R 真的出去了。
        """
        start = time.time()
        nr = ne = 0
        while self.time_elapsed_accounting_for_freeze(start) < window:
            self.send_liberation_key()
            nr += 1
            self.task.next_frame()
            if not self.task.in_team()[0]:
                self.record_liberation_use()
                self.macro_mark(f'{tag}_fired(R x{nr},E x{ne},'
                                f'{time.time() - start:.2f}s)')
                return True
            self.click()
            self.macro_wait(self.Z_RE_STEP)
            if self.resonance_available():
                self.send_resonance_key()
                self.record_resonance_use()
                ne += 1
            self.click()
            self.macro_wait(self.Z_RE_STEP)
        self.task.next_frame()
        if not self.task.in_team()[0]:
            self.record_liberation_use()
            self.macro_mark(f'{tag}_fired(R x{nr},E x{ne},late)')
            return True
        self.macro_mark(f'{tag}_NOFIRE(R x{nr},E x{ne},{time.time() - start:.2f}s)')
        self.logger.warning(f'{tag}: zani liberation still would not fire after '
                            f'{nr} R + {ne} E in {window:.1f}s — 这一圈没有大招, '
                            f'三终夜那一段会自己跳过')
        return False

    def mark_liberation_started(self):
        """把内置版的大招状态补上 —— 【不补的话三终夜那一整段会当场自杀】。

        内置版走的是 click_liberation()(阻塞到演出结束) -> _liberation_followup() ->
        _start_liberation(), 那里面才给 liberation_time 打时间戳。宏用的是
        fire_lib(不阻塞) + poke_until_hud_back(等演出结束), 绕开了那条路。
        而 nightfall_combo() 每一轮都问 should_end_liberation(time_only=True),
        它算的是 `20 - 已过时间`, 而 liberation_time_left() 在 liberation_time<=0
        时【直接返回 0】—— 于是第一轮就判成"大招快结束了", 掉头去放 R2,
        三终夜一发都打不出来。

        计时起点取"演出结束"跟内置版一致(_start_liberation 是在 click_liberation
        返回之后调的, 那时演出已经演完)。
        【不照抄 _start_liberation 的尾巴】: 它末尾那两句 continues_right_click(0.05)
        + continues_normal_attack(0.15) 是内置轴自己的起手, 视频里 R 演出结束之后
        直接就是猛点攻击, 没有那一下闪避。
        """
        self.crisis_time = -1
        self.state = 1
        self.in_liberation = True
        self.liberation_time = time.time()
        # 【v3.6.9 的内置 Zani 已经没有这个字段了】: 有才清, 没有就别新建一个
        # 没人读的属性(留着只会让下一个人以为它还有用)
        if hasattr(self, '_liber_handoff_token'):
            self._liber_handoff_token = 0
        self.macro_mark('zaniLiberOn')

    # ==================== 赞妮: 三终夜(情况3) ====================

    def seg_three_nightfall(self):
        """「直接两套断终夜 -> 一套完整AAA」-> R2。视频 493.60~507.17。

        用户 2026-09-08 在三种爆发手法里指定的最后一种(情况3)。三套的窗口分别是
        3.77 / 3.60 / 5.40 秒, 加 R2 一共 13.6 秒, 而大招是 20 秒 —— 【余量 6 秒,
        所以这一整段不按秒表卡, 全部交给读屏判据】。

        【一套的分格见 zani_nightfall()】。【不走内置的 nightfall_combo】——
        v1 走过, 实机闪避晚了两秒(用户 2026-09-08: "赞妮闪避的也不对, 比视频教的
        慢了2S, 没闪对地方"), 原因写在 zani_nightfall 的说明里。

        【第三套不闪避】: 作者原话"这最后一套就不需要闪避了, 直接AAA就完事"。
        那一套要把第二段下砸打出来, 落地要 1.4 秒左右 —— 【R2 必须等它落地】,
        早发会把下砸的伤害吃掉。
        """
        if not self.confirm_in_liberation():
            self.macro_mark('nf_skip(not-in-liber)')
            self.logger.info('zanfei: not in liberation after R, skipping the three-nightfall burst')
            return False
        done = 0
        full_lit = True
        # 【进来这一刻图标亮着没】: 亮着就说明上一段还有残留, zani_nightfall 的
        # NF_MIN_A 那道"先点满 1.6 秒再判"正是为这种情况留的
        self.update_blazes()
        self.logger.info(f'zanfei: nightfall burst begin ready={self.is_nightfall_ready()} '
                         f'blazes={self.blazes} left={self.liberation_time_left():.1f}s')
        for i in range(self.NF_CANCEL_COUNT):
            if not self.in_liberation:
                break
            self.zani_nightfall(cancel=True, tag=f'nf断{i + 1}')
            done += 1
            self.check_liber()
        if self.in_liberation:
            for i in range(self.NF_FULL_COUNT):
                if not self.in_liberation:
                    break
                full_lit = self.zani_nightfall(cancel=False, tag=f'nf整{i + 1}')
                done += 1
        # 【等下砸落地】: 固定 NF_SMASH_SETTLE, 【不再用 nightfall_time_left()】——
        # 那个方法算的是 `2.2 - 已过时间`, 而 2.2 是内置版给【夜闪】标的时长,
        # 跟完整终夜第二段下砸不是一回事; 更要命的是它会随着 NF_FULL_TAIL 变长而
        # 变短(连点越久剩得越少), 等于"点得越足、留给下砸的时间越少" —— 正好跟
        # 需要的方向相反。用户 2026-09-08 报"最后一个终夜伤害没打出来就R2打断了"
        # 第三套没亮(早在盲点里打出去了)就没有下砸要等, 直接 R2
        if full_lit:
            self.macro_mark(f'nf落地({self.NF_SMASH_SETTLE:.2f})')
            self.macro_wait(self.NF_SMASH_SETTLE)
            self.macro_wait(self.NF_R2_LEAD)
        confirmed = self.click_liber2()
        self.macro_mark(f'zaniR2({"ok" if confirmed else "unconfirmed"})')
        # 【把脱战判定重新关掉】: click_liber2 内部会走到 BaseChar.sleep(),
        # 而那个方法结尾【无条件】把 skip_combat_check 置回 False。后面紧接着是
        # "戳E -> 变奏菲比 -> 切维里奈"三次切人, 切人时 HUD 会消失,
        # 战斗判定线程一误报就把整条循环轴打断
        self.task.skip_combat_check = True
        self.logger.info(f'zanfei: three-nightfall done sets={done} r2={confirmed}')
        return confirmed

    def zani_nightfall(self, cancel, tag='nf'):
        """一套终夜。【不用内置的 nightfall_combo, 因为它的闪避位置是错的】。

        用户 2026-09-08 口述的机制(第二次修正之后):
          "赞妮R之后 A, A到强化重击亮了" -> "终夜亮了得再A出一下, 用闪避打断, 再A"
        跟视频 389~398 秒的字幕逐句对得上(猛按攻击 -> 两下重斩 -> 解锁终夜 ->
        攻击+闪避取消第一段 -> 再按攻击直接跳起放第二段)。

        【"出一下"要等它真出手, 但不能靠读屏等】: 点击递出去 != 动作出手 ——
        落在起手帧上的闪避是把这一下【整个取消】, 白烧焰光(v2 给 0.18 秒固定窗口,
        实机"闪早了"就是这个)。但反过来等"图标灭"也不行: 白环渐隐 + 大招满屏特效,
        读到灭的时候那一下早演完了(v3 上限 0.80, 实机"慢了 0.4 秒")。
        现在是【固定窗口 NF_HIT_CAP 当主路径, 图标灭当早退信号】—— 三次实机修正
        出来的形状, 数值的来历写在常量区。

        【内置 nightfall_combo 慢在哪】(用户: "赞妮闪避的也不对, 比视频教的慢了2S,
        没闪对地方"): 它的 cancel 分支是
            continues_normal_attack(0.5)                  # 那一下 A
            while is_nightfall_ready(threshold=0.035): click()   # 【等图标灭, 上限 2.5 秒】
            sleep(0.25)                                   # 【再停 0.25】
            continues_right_click(0.1)                    # 才闪避
        中间那两段加起来最多 2.75 秒, 正好是实机上"慢了 2 秒"。而按用户说的手法,
        闪避要【紧跟着那一下 A】—— 图标灭不灭跟闪不闪避没关系, 那个 0.035 的阈值
        本来就是"几乎完全消失"才成立, 在特效糊住图标的时候更是要等满上限。

        Args:
            cancel: True = 断终夜(A -> 闪避取消 -> 再A出第二段);
                False = 完整终夜(不闪避, 一路点到第二段下砸打完)。
        """
        # 1) 猛点攻击, 等强化重击/终夜图标亮
        # 【先点满 NF_MIN_A 再开始认】: 上一套刚打完时图标可能还亮着(判"某状态出现"
        # 之前要先确认它消失过) —— 不设这道门的话这一套会当场跳到第 2 步, 两下重斩
        # 一下都没打, 焰光的账就对不上了
        start = time.time()
        lit = False
        n = 0
        cap = self.NF_ACQUIRE_CAP if cancel else self.NF_FULL_ACQUIRE_CAP
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            if self.time_elapsed_accounting_for_freeze(start) >= self.NF_MIN_A \
                    and self.is_nightfall_ready():
                lit = True
                break
            self.click()
            n += 1
            if not self.in_liberation:
                break
            self.task.next_frame()
        self.macro_mark(f'{tag}亮({"ok" if lit else "CAP"},A x{n},'
                        f'{time.time() - start:.2f}s)')
        if not cancel and not lit:
            # 等满 NF_FULL_ACQUIRE_CAP 都没亮 = 这一套早在盲点里打出去了, 直接去 R2
            return False
        if not cancel:
            # 完整那一套: 不打断, 一路点把两段都打出去。
            # 【nightfall_time 照样打上】: R2 那边已经改成固定等待、不读它了,
            # 但内置版的 should_end_liberation / wait_switch 还在读 —— 宏散掉之后
            # 框架接手时要靠它判"下砸还没落地, 先别切人"
            self.nightfall_time = time.time()
            self.macro_spam(self.NF_FULL_TAIL, tag=f'{tag}整A')
            self.macro_mark(f'{tag}整(left{self.liberation_time_left():.1f})')
            return lit
        # 2) 【再 A 一下让它出手】—— 主路径是固定窗口 NF_HIT_CAP(读屏太晚, 见常量区),
        # 图标提前灭掉只当"这一下被打断了"的早退信号
        hit_start = time.time()
        fired = False
        m = 0
        while self.time_elapsed_accounting_for_freeze(hit_start) < self.NF_HIT_CAP:
            self.click()
            m += 1
            if not self.in_liberation:
                break
            self.task.next_frame()
            if self.time_elapsed_accounting_for_freeze(hit_start) >= self.NF_MIN_HIT \
                    and not self.is_nightfall_ready():
                fired = True
                break
        self.macro_mark(f'{tag}出手({"ok" if fired else "CAP"},A x{m},'
                        f'{time.time() - hit_start:.2f}s)')
        # 3) 【出手了才闪避】—— 这一下是打断收招, 不是取消起手
        self.continues_right_click(self.NF_DODGE)
        # 4) 再 A 出终夜第二段(视频 497.40 闪 -> 497.57 就接着点了)
        self.macro_spam(self.NF_SECOND_WINDOW, tag=f'{tag}二段')
        self.macro_mark(f'{tag}断(left{self.liberation_time_left():.1f})')
        return lit

    def check_liber(self):
        """大招状态以我们自己标的时段为准, 不读图标栏。

        内置版认 2K x=1909 那格的锁定图标: 在就判"不在大招里"。现在大招期间那格
        叠着月牙普攻位、锁定图标还在原地(2026-11-03 实机读到 1.00), 开了大招也判
        "不在" —— 三终夜跳过、R2 一发不按。mark_liberation_started 之后到 20 秒
        计时走完(或 click_liber2 收掉)都算在大招里; 没标过才退回内置判据。
        """
        if self.in_liberation and self.liberation_time > 0:
            if self.liberation_time_left() > 0:
                return True
            self.in_liberation = False
            return False
        return super().check_liber()

    def click_liber2(self):
        """R2: 一直发 R, 直到 HUD 掉下去(R2 演出)或 E 图标连续两帧回白(大招结束)。

        内置版循环条件是"x=1909 那格没有锁定图标", 而现在大招里它一直在,
        进门就退出、一发 R 都没按(原因同 check_liber)。
        不点普攻: 演出里点的会被丢掉, 演出完 seg_zani_r2_tail 自己接 E。
        """
        start = time.time()
        n = 0
        white = 0
        how = None
        while self.time_elapsed_accounting_for_freeze(start) < self.Z_R2_FIRE_CAP:
            self.send_liberation_key()
            n += 1
            self.task.next_frame()
            if not self.task.in_team()[0]:
                how = 'anim'
                break
            white = white + 1 if self.current_resonance() > 0.05 else 0
            if white >= 2:
                how = 'eWhite'
                break
            self.macro_wait(self.Z_RE_STEP)
        self.macro_mark(f'R2键({how or "CAP"},R x{n},{time.time() - start:.2f}s)')
        self.in_liberation = False
        self.state = 0
        self.blazes = -1
        self.liberation_time = -1
        if how:
            self.last_liber2 = time.time()
        return how is not None

    def zani_e_once(self, cap, tail, tag='E'):
        """赞妮 R2 后的 E: 第一发真出去之后再连发 tail 秒就走。

        【不能用 press_e_until_cd】: 赞妮的 E 不出冷却数字(大招里只是变灰),
        has_cd 一直读负数, "按到进 CD"永远不成立, 每次都按满 cap(2026-11-03 实机
        19 发 E / 2.5s)。这里只认"HUD 在场且 E 图标是白的"时发出去的那一发。
        """
        start = time.time()
        ne = 0
        first = 0.0
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            if first and time.time() - first >= tail:
                break
            self.task.next_frame()
            live = bool(self.task.in_team()[0]) and self.current_resonance() > 0.05
            self.send_resonance_key()
            ne += 1
            if live and not first:
                first = time.time()
            self.macro_wait(self.Z_RE_STEP)
        self.macro_mark(f'{tag}({"ok" if first else "CAP"},E x{ne},'
                        f'{time.time() - start:.2f}s)')
        return bool(first)

    def confirm_in_liberation(self, tries=2, gap=0.25):
        """确认真的在大招状态里 —— 【读不到就再读一次, 别一票否决】。

        判 False 的代价是整段爆发不打(一发大招白扔), 所以这里给一次重试;
        两次都说不在, 那就是真没开出来 —— 那种情况下硬打三终夜只会空挥。
        check_liber 已经改成看我们自己标的大招时段, 见上面。
        """
        for i in range(tries):
            if self.check_liber():
                return True
            self.macro_mark(f'liberProbe{i + 1}=off')
            if i + 1 < tries:
                self.macro_wait(gap)
        return False

    # ==================== 赞妮: R2 之后 -> 变奏菲比 ====================

    def seg_zani_r2_tail(self, pho):
        """组合4 的后半「打完爆发防反+普攻 / 变奏菲比」。视频 507.17~510.5。

        分格(用户 2026-09-08 定的): R2 -> (F 处决, 有就蹭) -> E -> A -> 变奏菲比。
        【不管处没处决, 后面两格都一样】—— 处决只是把 R2 收招那一秒多填满, 不改变
        后面的节奏。

        作者原话是"赞妮二段大的时候你就一直戳共鸣技能"(视频 421 秒), 视频里那一段
        是 E x6 / 1.63 秒 —— 【但宏这边是"发到它出去为止"】: 手打戳那么多是为了把
        协奏补满, 而这里协奏另有 wait_con_at_least 兜底; 戳满 1.7 秒只是在拖慢交棒,
        按固定次数发又会被处决的收招整段吞掉(上一版就是这么丢的)。

        【第一下 E 落在哪儿是打出来的, 不是等出来的】: 视频里它在 508.50, 离 R2 正好
        1.33 秒 —— 那一秒多是 R2 的收招, 期间的按键会被吞。旧版拿 macro_wait 硬等
        那么久, 现在整段交给 zani_e_once: E 一直发, 被吞就接着发, 收招一过
        它自己就出去了, 时间点跟视频对得上而且不用写死。
        """
        self.poking = False
        # 【直接 E 接 A】—— 处决挪给维里奈了, 见 Z_R2_E_CAP 那段说明。
        # 这一段不点左键: E 不需要普攻来触发, 点了就是"多A几下"
        self.zani_e_once(self.Z_R2_E_CAP, self.Z_R2_E_TAIL, tag='zaniR2E')
        self.record_resonance_use()
        # E 出去了才接 A
        self.macro_spam(self.Z_R2_A_WINDOW, tag='zaniR2A')
        # 【跟菲比收尾同一个形状】(见 phoebe_finish): 不用 wait_con_for_intro ——
        # 它的门槛是 CON_ENOUGH(0.99, 环没闭合的哨兵值), 而且"读数 1.2 秒不动就放弃"
        # 比 R2 的伤害结算还快。这里满不了的话菲比就没有变奏入场,
        # 过场那一格垫回路能量的意义也跟着没了
        full = self.wait_con_at_least(self, self.CON_FULL_LEVEL, tag='zaniR2Con',
                                      cap=self.Z_R2_CON_CAP)
        if not full:
            self.logger.info('zanfei: zani leaving after R2 without a full concert — '
                             'Phoebe gets no intro, the pass-through beat loses its point')
        return self.quick_switch(pho, read_con=True)

    def press_e_until_cd(self, char, window, tag='E', poke=False):
        """一直按 E, 直到它真的进了 CD(图标灰了)。返回出去了没有。

        用户 2026-09-08: "只要E没进cd就一直按, 按到进CD"。

        【为什么不能按固定次数发】: 收招/演出会把按键吞掉, 按两下很可能两下全丢
        (上一版 presses=2 就是这么丢的)。

        【判"图标灰了"之前必须先确认 HUD 在场】: 击破动画、大招演出期间队伍 HUD
        整个消失, 那一刻 resonance_available() 什么都读不到、恒返回 False ——
        "图标灰了"当场成立, 循环立刻退出, 而 E 一发都没真发出去
        (用户 2026-09-08: "放了F处决又直接切人了, 根本没管E, 你是不是在F动画的时候
        检测不到技能圈啊" —— 正是如此)。这是"判某状态消失之前先确认它出现过"的特例。
        而且【只有 HUD 在场时发出去的那几发才算数】, 动画里发的是被丢掉的。

        【默认不点左键】: E 不需要普攻来触发, 点了就是多出来的 A(赞妮 R2 那一格
        就是为了去掉那几下才改成这样的)。但【入场那种"顺手攒点协奏"的格子传 poke=True】
        —— 发键和点击是两条流, 互不影响, 而 E 一出去循环就停, 也点不了几下。
        """
        step = self.Z_RE_STEP
        start = time.time()
        ne = 0
        live_e = 0        # 【HUD 在场时】发出去的 E 才算数
        anim = 0          # 有多少轮是在动画里(HUD 不在场)
        while self.time_elapsed_accounting_for_freeze(start) < window:
            self.task.next_frame()
            hud = bool(self.task.in_team()[0])
            if not hud:
                anim += 1
            elif live_e > 0 and not char.resonance_available():
                self.macro_mark(f'{tag}_ok(E x{ne},anim{anim},'
                                f'{time.time() - start:.2f}s)')
                return True
            char.send_resonance_key()
            ne += 1
            if hud:
                live_e += 1
            if poke:
                self.click()
            self.macro_wait(step)
        self.task.next_frame()
        if self.task.in_team()[0] and live_e > 0 and not char.resonance_available():
            self.macro_mark(f'{tag}_ok(E x{ne},anim{anim},late)')
            return True
        self.macro_mark(f'{tag}_CD?(E x{ne},live{live_e},anim{anim},'
                        f'{time.time() - start:.2f}s)')
        self.logger.info(f'{tag}: E never read as on-cooldown — x{ne} presses '
                         f'({live_e} of them with the HUD up), {anim} rounds inside an '
                         f'animation, {window:.1f}s')
        return False

    def f_break_now(self, window, tag='F', check_every=2):
        """在一个窗口里找处决提示, 找到就让框架打完。返回打没打出来。

        【绕开角色自己的 f_break 覆盖】: 赞妮和菲比的内置版都是 `return False`
        (理由是处决会打断强化E/夜闪/赞妮大招的重击条), 而宏是挂在赞妮身上的 ——
        走 self.f_break() 一发都出不来。这里直接调 task 那一层。
        【所以这个方法只能用在"当前场上角色不是赞妮/菲比"的格子里】: 现在只有
        维里奈的 Q 和 R 之间用它。

        【自己查一遍中央提示】: 框架 check_f_break() 的中央那一路有 1 秒频率限制,
        高频重试等于只查了顶部那一路(见 f_prompt_now); 而宏跑的时候
        skip_combat_check 是开着的, 战斗判定线程也不刷新 can_break, 两条腿一起瘸。

        【发键/点击每轮做, 读屏隔轮做】: 提示要靠"一直在打"才出现, 而中央找图不便宜,
        每轮都查会把点击密度压下去(那就是之前"愣住"的来源)。
        """
        start = time.time()
        n = 0
        probes = 0
        seen = 0
        while self.time_elapsed_accounting_for_freeze(start) < window:
            n += 1
            if n % check_every == 0:
                probes += 1
                pre_can = bool(getattr(self.task, 'can_break', False))
                hit = self.f_prompt_now()
                if hit:
                    self.task.can_break = True
                if hit or pre_can:
                    seen += 1
                    if self.task.f_break():
                        self.macro_mark(f'{tag}_ok(x{n},probe{probes},'
                                        f'{time.time() - start:.2f}s)')
                        return True
            self.task.send_key('f')
            self.click()
            self.macro_wait(self.F_SPAM_INTERVAL)
        self.macro_mark(f'{tag}_none(x{n},probe{probes},seen{seen},'
                        f'{time.time() - start:.2f}s)')
        if seen:
            self.logger.warning(f'{tag}: 提示读到了 {seen} 次, 但 task.f_break() '
                                f'一次都没打出去 —— 那是框架那一侧的问题, 不是窗口不够')
        return False

    # ==================== 公共段(两条轴的后四格) ====================

    def run_body(self, pho, ver, tag):
        """维里奈 -> 菲比 -> 赞妮开大 -> 三终夜 -> R2。两条轴逐字相同的那一段。"""
        marks = len(self.macro_marks)
        self.seg_verina(ver, pho)
        self.logger.info(f'{tag} VERINA timeline: {self.macro_timeline(marks)}')
        marks = len(self.macro_marks)

        self.seg_phoebe(pho)
        self.logger.info(f'{tag} PHOEBE timeline: {self.macro_timeline(marks)}')
        marks = len(self.macro_marks)

        self.seg_zani_into_lib()
        self.logger.info(f'{tag} ZANI-R timeline: {self.macro_timeline(marks)}')
        marks = len(self.macro_marks)

        self.seg_three_nightfall()
        self.logger.info(f'{tag} NIGHTFALL timeline: {self.macro_timeline(marks)}')

    # ==================== 启动轴 ====================

    def perform_opener(self, entry_char=None):
        """启动轴。由赞妮自己的 do_perform 调进来 —— 轴的第一个动作就是她的防反 E。

        轴(视频 469.6~507.2 = 37.6 秒):
          1 赞妮   E(防反)-A                -> 切维里奈
          2 维里奈 3A-E-Q-R-跳-A            -> 变奏菲比
          3 菲比   长按E-R-(AAA+重击)x2     -> 变奏赞妮
          4 赞妮   连按E-Q-连按E-A-R -> 三终夜 -> R2

        Args:
            entry_char: 调用方自己 —— 框架就是因为他在场才调的 do_perform, 所以这是
                "现在场上是谁"最便宜也最可靠的答案。【不要让它去读屏判断】:
                in_team() 在战斗开局的头 0.8 秒会说谎(队伍 HUD 还在渐入)。
        """
        pho = self.teammate('Phoebe')
        ver = self.teammate('Verina')
        if pho is None or ver is None:
            return False

        # 整段关掉脱战判定: 高频切人时 HUD 反复消失(大招演出), 战斗判定线程一误报
        # 就抛 NotInCombatException, 宏从中间断掉比打歪严重得多
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.cur = entry_char or self.on_field_char() or self
        self.poking = False
        raw_in_team, raw_index, raw_count = self.task.in_team()
        flags = ' '.join(f'{c.name}{"*" if c.is_current_char else ""}'
                         for c in self.task.chars if c is not None)
        self.logger.info(
            f'zanfei 3-nightfall opener entry: {self.cur.name} '
            f'({"passed in by caller" if entry_char else "read off screen"}) | '
            f'in_team={raw_in_team} index={raw_index} count={raw_count} | flags: {flags}')
        try:
            self.macro_start()
            self.loop_count = 0
            # 【处决全程由宏自己按 —— 而这条轴一次都不按】: 内置版的赞妮/菲比都把
            # f_break 关掉了(会打断强化E和夜闪), 这里再把框架切人时那一发也关掉
            self.check_f_on_switch = False
            pho.check_f_on_switch = False
            ver.check_f_on_switch = False
            # 【宏绕开了内置的 do_perform, 队友识别没人替我们做】: char_phoebe /
            # blazes_threshold / _zanfei_guang 这几个字段是内置版一堆判据的输入
            # (框架在宏散掉之后还会用到它们), 每圈刷一次不贵
            self.decide_teammate()

            # 【开局赞妮不一定在场】: 框架可能因为别人先满足条件而先切了人。
            # 轴的第一个动作是她的防反 E, 所以先把自己换上来
            if self.cur is not self:
                self.quick_switch(self)

            self.open_zani_guard(ver)
            self.logger.info(f'opener GUARD timeline: {self.macro_timeline()}')

            self.run_body(pho, ver, 'opener')
            self.logger.info(
                f'opener total {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s) — 视频里这一段 37.6 秒')

            if self.CHAIN_LOOP_AFTER_OPENER:
                self.logger.info('chaining straight into the loop')
                self.run_loop_body(pho, ver)
        finally:
            # 长按跨了好几个方法, 中途抛异常的话鼠标会一直按着, 下一个角色上场就是
            # 一直按住普攻。这里是唯一保证松手的地方
            self.release_attack()
            self.poking = False
            self.task.skip_combat_check = previous
        return True

    # ==================== 循环轴 ====================

    def perform_loop(self, entry_char=None):
        """循环轴。由赞妮自己的 do_perform 调进来 —— 一圈的首尾都在她身上。

        轴(视频 507.2 起, 一圈约 43 秒):
          1 赞妮   R2 之后一直戳E           -> 变奏菲比
          2 菲比   落地什么都不做           -> 切维里奈
          3 维里奈 3A-E-Q-R-跳-A            -> 变奏菲比
          4 菲比   长按E-R-(AAA+重击)x2     -> 变奏赞妮
          5 赞妮   连按E-Q-连按E-A-R -> 三终夜 -> R2
        """
        pho = self.teammate('Phoebe')
        ver = self.teammate('Verina')
        if pho is None or ver is None:
            return False
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.cur = entry_char or self.on_field_char() or self
        self.poking = False
        try:
            self.macro_start()
            self.check_f_on_switch = False
            pho.check_f_on_switch = False
            ver.check_f_on_switch = False
            # 【宏绕开了内置的 do_perform, 队友识别没人替我们做】: char_phoebe /
            # blazes_threshold / _zanfei_guang 这几个字段是内置版一堆判据的输入
            # (框架在宏散掉之后还会用到它们), 每圈刷一次不贵
            self.decide_teammate()
            if self.cur is not self:
                self.quick_switch(self)
            self.run_loop_body(pho, ver)
        finally:
            self.release_attack()
            self.poking = False
            self.task.skip_combat_check = previous
        return True

    def run_loop_body(self, pho, ver):
        self.loop_count += 1
        marks = len(self.macro_marks)
        self.seg_zani_r2_tail(pho)
        self.logger.info(f'loop#{self.loop_count} R2TAIL timeline: {self.macro_timeline(marks)}')
        marks = len(self.macro_marks)

        self.loop_phoebe_pass(pho, ver)
        self.logger.info(f'loop#{self.loop_count} PASS timeline: {self.macro_timeline(marks)}')

        self.run_body(pho, ver, f'loop#{self.loop_count}')
        self.logger.info(
            f'loop#{self.loop_count} total '
            f'{self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
            f'(wall {time.time() - self.macro_t0:.2f}s) — 视频里一圈约 43 秒')

    # ==================== 框架接口 ====================

    def do_perform(self):
        """本队里赞妮驱动【两条轴】—— 启动轴和循环轴的入口都是她。

        【为什么两条轴都挂在她身上】: 一圈的最后一个动作是她的 R2, 跑完宏返回时她
        还在场; 下一次框架调 do_perform 又是她 —— 不用像清达莫那队一样把循环入口
        借给别人。
        """
        if not self.is_in_zanfei_nai:
            return super().do_perform()
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 【标记要在跑之前落】: perform_opener 中途抛异常(脱战/任务被停)也不能
            # 让启动轴重跑 —— reset_state 会把 armed 重新置回来
            self.opener_armed = False
            self.last_opener = time.time()
            if self.perform_opener(self):
                return
            self.logger.warning('zanfei opener aborted, falling back to the native rotation')
            return super().do_perform()
        self.logger.info(f'zanfei: opener not pending ({blocked}), running the loop')
        if self.perform_loop(self):
            return
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """本队里由宏直接按数字键切人, 框架的选人只在宏散掉之后才起作用。

        【启动轴待跑时赞妮必须先上场】—— 轴的第一个动作是她的防反 E。
        本队里菲比和维里奈在启动轴待跑时都返回 NO, 所以这个 MUST 是必需的:
        全队都是 NO 的话框架只会打一下普攻原地打转。
        """
        if self.is_in_zanfei_nai and self.opener_pending():
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        self.poking = False
        return super().on_combat_end(chars)
