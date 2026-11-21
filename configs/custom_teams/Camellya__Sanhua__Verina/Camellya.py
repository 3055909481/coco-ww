import time
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN, ROUND_UP
from ok import color_range_to_bound
from src.char.BaseChar import BaseChar, SwitchPriority
import cv2
import numpy as np


class Camellya(BaseChar):
    """
    椿 (Camellya) 角色自动化控制类。
    继承自 BaseChar，实现了椿在“椿 + 散华 + 维里奈”队伍中的战斗逻辑。
    """

    def __init__(self, *args, **kwargs):
        """初始化椿的角色状态。"""
        super().__init__(*args, **kwargs)
        # 记录最后一次重击的时间
        self.last_heavy = 0
        # 标记是否正在等待“ forte ”（能量/动能）值下降，用于检测重击是否生效
        self.waiting_for_forte_drop = False
        # 记录 forte 开始下降的时间戳
        self.forte_drop_timestamp = 0
        # 缓存 forte 差值，用于判断趋势
        self.forte_diff_buffer = []
        # 上一次检测到的 forte 值
        self.last_forte = 0

    def reset_state(self):
        """重置角色的状态，通常在切换角色或战斗开始时调用。"""
        super().reset_state()
        # 重置等待 forte 下降的标志
        self.waiting_for_forte_drop = False

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """
        决定当前角色是否应该被切换到。
        如果当前角色拥有入场技 (has_intro)，则强制切换 (MUST)。
        否则，使用基类的默认逻辑。
        """
        if has_intro:
            # 如果拥有入场技，则必须切换到此角色
            return SwitchPriority.MUST
        # 否则，使用基类的优先级判断
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def wait_resonance_not_gray(self, timeout=5):
        """
        等待共鸣技能图标不再为灰色（即可用）。
        通过不断点击来尝试激活或刷新状态。
        """
        start = time.time()
        # 当共鸣技能不可用时循环
        while self.current_resonance() == 0:
            self.click()  # 点击屏幕，可能用于激活技能或刷新UI
            self.sleep(0.1)  # 短暂休眠
            # 如果超时，记录错误并退出
            if time.time() - start > timeout:
                self.logger.error('wait wait_resonance_not_gray timed out')

    def on_combat_end(self, chars):
        """战斗结束时，切换至其他角色。"""
        self.switch_other_char()

    def do_perform(self):
        """
        执行椿的主要战斗循环。
        这是椿在场上时的核心逻辑。
        """
        # --- 1. 入场处理 ---
        # 如果椿是通过入场技进入战场的
        if self.has_intro:
            # 先进行一段普攻（持续1.2秒），以进入战斗状态
            self.continues_normal_attack(1.2)
            self.sleep(0.1)
            # 然后进行重击（持续4.6秒），直到协奏值满
            self.heavy_attack(4.6, until_con_full=True)

        # --- 2. 技能释放决策 ---
        # 获取当前协奏值
        start_con = self.get_current_con()
        # 根据协奏值决定循环时间和是否释放共鸣技能
        if start_con < 0.82:
            # 协奏值较低，循环时间短，并尝试释放共鸣技能
            loop_time = 1.1
            if self.resonance_available():
                self.click_resonance()
        else:
            # 协奏值较高，循环时间长
            loop_time = 4.6

        # --- 3. 核心战斗循环（“含苞”状态管理） ---
        # 记录进入“含苞”状态的时间
        budding_start_time = time.time()
        # 标记是否已进入“含苞”状态
        budding = False
        # 标记是否正在进行重击（按住鼠标）
        heavy_att = False
        # 用于冻结 forte 检查的标志和时间
        freeze_forte_check = False
        freeze_forte_time = 0
        self.last_forte = 0

        # 循环条件：时间未到，或者屏幕上仍检测到“含苞”状态
        while time.time() - budding_start_time < loop_time or self.task.find_one('camellya_budding', threshold=0.7):
            # --- 3.1. 进入“含苞”状态前的准备 ---
            if not budding:
                # 检查是否可以进入“含苞”状态（ ephemeral_ready ）且协奏值已满
                if self.ephemeral_ready() and self.is_con_full():
                    # 释放共鸣技能进入“含苞”状态
                    self.ephemeral_cast()
                    budding = True
                else:
                    # 否则，持续点击（进行普通攻击）积攒协奏值
                    self.click(interval=0.1)
                    current_con = self.get_current_con()
                    # 如果协奏值低于0.82
                    if current_con < 0.82:
                        # 如果协奏值未满，释放声骸技能并准备切换角色
                        if not self.is_con_full():
                            self.click_echo()
                            return self.switch_next_char()
                        # 如果协奏值已满但未进入“含苞”，延长循环时间
                        elif loop_time < 3.1:
                            loop_time += 1
                # 如果成功进入“含苞”状态
                if budding:
                    self.logger.info(f'start budding')
                    # 检查目标是否仍然存在
                    self.check_target()
                    # 重置“含苞”开始时间，并延长循环时间
                    budding_start_time = time.time()
                    loop_time = 5.1

            # --- 3.2. “含苞”状态下的操作 ---
            if budding:
                # 如果尚未开始重击，则按下鼠标（开始重击）
                if not heavy_att:
                    heavy_att = True
                    self.task.mouse_down()

                # 在“含苞”状态的前1.5秒内，如果大招可用，则释放大招（仅在此处释放）
                if time.time() - budding_start_time < 1.5 and self.liberation_available():
                    if self.click_liberation() and heavy_att:
                        self.logger.info(f'liberation retry heavy att')
                        # 释放大招可能会中断重击，因此重新按下鼠标
                        self.task.mouse_up()
                        self.sleep(0.2, False)
                        self.task.mouse_down()

            # --- 3.3. 状态检查与循环控制 ---
            # 检查目标是否存活（如果正在进行重击，则传入 heavy_att=True）
            self.check_target(heavy_att)
            # 进入下一帧
            self.task.next_frame()

            # 管理 forte 检查的冻结状态（避免过于频繁的检测）
            if freeze_forte_check and time.time() - freeze_forte_time >= 0.2:
                freeze_forte_check = False

            # 如果未冻结且正在进行重击，则检查是否需要重试重击
            if not freeze_forte_check and heavy_att:
                if self.should_retry_heavy_attack(budding) < 0:
                    # 如果需要重试，则冻结检查一段时间
                    freeze_forte_check = True
                    freeze_forte_time = time.time()

        # --- 4. 循环结束后的清理工作 ---
        self.waiting_for_forte_drop = False
        # 如果正在进行重击，则释放鼠标
        if heavy_att:
            self.task.mouse_up()
            self.sleep(0.1)
        # 如果处于“含苞”状态，释放共鸣技能（可能用于退出“含苞”或触发后续效果）
        if budding:
            self.click_resonance()
            self.sleep(0.1)
        # 释放声骸技能
        self.click_echo()
        # 切换至下一个角色
        self.switch_next_char()

    def click_echo(self, *args, **kwargs):
        """
        释放声骸技能（如果可用）。
        """
        if self.echo_available():
            self.send_echo_key()
            return True

    def ephemeral_ready(self):
        """
        检测椿是否可以进入“ ephemeral ”（含苞/瞬刻）状态。
        通过检查屏幕上特定区域（共鸣技能图标）的红色占比来判断。
        """
        # 定义屏幕区域：共鸣技能图标的大致位置（基于2560x1440分辨率）
        box = self.task.box_of_screen_scaled(2560, 1440, 2110, 1236, 2217, 1343, name='camellya_resonance',
                                             hcenter=True)
        # 计算该区域内符合“ camellya_red_color ”颜色范围的像素百分比
        red_percent = self.calculate_color_percentage_in_masked(camellya_red_color, box, 0.395, 0.496)
        self.logger.debug(f'red_percent {red_percent}')
        # 如果红色占比超过10%，则认为准备就绪
        return red_percent > 0.1

    def calculate_color_percentage_in_masked(self, target_color, box, mask_r1_ratio=0.0, mask_r2_ratio=0.0):
        """
        计算图像中特定区域（box）内，符合目标颜色（target_color）的像素在环形遮罩区域内的百分比。
        用于精确检测UI元素的状态。
        """
        # 从屏幕帧中裁剪出指定区域
        cropped = box.crop_frame(self.task.frame).copy()
        if cropped is None or cropped.size == 0:
            return 0.0
        h, w = cropped.shape[:2]

        # 计算环形遮罩的内外半径
        center = (w // 2, h // 2)
        r1, r2 = h * mask_r1_ratio, h * mask_r2_ratio
        r1 = Decimal(str(r1)).quantize(Decimal('0'), rounding=ROUND_DOWN)
        r2 = Decimal(str(r2)).quantize(Decimal('0'), rounding=ROUND_UP)

        # 创建环形遮罩（一个圆环区域）
        ring_mask = np.zeros((h, w), dtype=np.uint8)
        if r2 > 0:
            cv2.circle(ring_mask, center, int(r2), 255, -1)  # 外圆
        if r1 > 0:
            cv2.circle(ring_mask, center, int(r1), 0, -1)   # 内圆（挖空）
        # 应用遮罩
        masked_image = cv2.bitwise_and(cropped, cropped, mask=ring_mask)

        # 计算遮罩区域内的非黑色像素总数（即有效区域）
        if masked_image.ndim == 3:
            non_black_mask = np.all(masked_image != 0, axis=2)
        else:
            return 0.0

        free_space = np.count_nonzero(non_black_mask)
        if free_space == 0:
            return 0.0

        # 检测符合目标颜色范围的像素
        lower_bound, upper_bound = color_range_to_bound(target_color)
        gray = cv2.inRange(masked_image, lower_bound, upper_bound)
        colored_pixels = np.count_nonzero(gray == 255)

        # 计算百分比
        color_percent = colored_pixels / free_space
        return color_percent

    def ephemeral_cast(self):
        """
        执行进入“ ephemeral ”（含苞/瞬刻）状态的操作。
        持续按下共鸣技能键，直到 ephemeral_ready() 返回 False（表示已成功进入状态）。
        """
        self.check_combat()
        # 只要“含苞”状态就绪，就持续发送共鸣技能按键
        while self.ephemeral_ready():
            self.send_resonance_key()
            self.sleep(0.1)
        # 进入状态后，等待1.1秒让动画播放完成
        self.sleep(1.1)

    def get_forte(self, budding=False):
        """
        获取当前角色的 forte（能量/动能）值。
        根据是否处于“含苞”状态，使用不同的颜色检测配置。
        """
        # 定义检测区域（基于3840x2160分辨率）
        box = self.task.box_of_screen_scaled(3840, 2160, 1630, 2002, 2176, 2004, name='camellya_forte', hcenter=True)
        forte_percent = 0
        # 根据是否处于“含苞”状态选择不同的颜色配置
        if not budding:
            forte_percent = self.calculate_forte_percent(camellya_forte_color, box)
        else:
            forte_percent = self.calculate_forte_percent(camellya_budding_forte_color, box)
        # 四舍五入到小数点后3位
        forte_percent = Decimal(str(forte_percent)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
        if forte_percent >= 0:
            self.logger.debug(f'forte_percent {forte_percent * 100:.2f}% budding {budding}')
        return forte_percent

    def detect_stripe_region(self, gray: np.ndarray, white_ratio_range=(0.05, 0.75),
                             fft_thresh_ratio: float = 0.45, max_fail_count: int = 1) -> tuple:
        """
        在二值图像中检测条纹区域（用于识别forte条）。
        通过滑动窗口分析白色像素的分布和周期性（FFT）来定位条纹区域。
        """
        height, width = gray.shape[:2]
        # 基本有效性检查
        if height == 0 or width < 64 or not np.any((gray == 0) | (gray == 255)):
            return -1, -1
        if not np.any((gray == 255)):
            return 0, 0

        # 辅助函数：移除过短的条纹（噪声）
        def remove_short_stripes(gray: np.ndarray, threshold=3) -> np.ndarray:
            if threshold < 1:
                return gray
            result = gray.copy()
            height, width = gray.shape
            for y in range(height):
                row = gray[y]
                x = 0
                while x < width:
                    if row[x] == 255:
                        start = x
                        while x < width and row[x] == 255:
                            x += 1
                        length = x - start
                        if length < threshold:
                            result[y, start:x] = 0
                    else:
                        x += 1
            return result

        # 窗口大小和步长
        win_w = max(8, int(width * 0.045))
        step = max(1, int(width * 0.012))
        # 预处理：移除短条纹
        gray = remove_short_stripes(gray, int(width * 0.009))

        scores = []
        positions = []
        start_x = None
        end_x = None
        fail_count = 0
        x = 0
        # 滑动窗口遍历图像
        while x + win_w <= width:
            window = gray[:, x:x + win_w]
            # 计算窗口内白色像素比例
            white_ratio = np.count_nonzero(window == 255) / window.size
            if not (white_ratio_range[0] <= white_ratio <= white_ratio_range[1]):
                score = 0
            else:
                # 计算垂直投影（每列白色像素数）
                profile = np.sum(window == 255, axis=0).astype(np.float32)
                profile -= np.mean(profile)
                # 进行FFT分析，检测周期性
                spectrum = np.abs(np.fft.fft(profile))[:win_w // 2]
                score = np.max(spectrum[1:])  # 忽略直流分量
            scores.append(score)
            positions.append((x, x + win_w))
            x += step

        if len(scores) == 0:
            return -1, -1

        scores = np.array(scores)
        max_score = np.max(scores)
        # 根据最大分数动态确定阈值
        fft_thresh = max(1.5, max_score * fft_thresh_ratio)

        if max_score < 1e-3:
            return 0, 0

        # 标记符合条件的窗口
        keep = scores >= fft_thresh

        # 从右向左查找最后一个符合条件的窗口，并处理中间可能的间断
        fail_count = 0
        end_idx = 0
        for i in range(len(keep)):
            if keep[i]:
                end_idx = i
                fail_count = 0
            else:
                a, b = positions[i]
                window = gray[:, a:b]
                white_ratio = np.count_nonzero(window == 255) / window.size
                # 如果白色比例过低，认为是真正的间断
                if white_ratio < 0.375:
                    # 如果后续还有符合条件的窗口，说明是干扰，返回错误码
                    if np.any(scores[i + 1:] > fft_thresh):
                        return -2, -2
                    fail_count += 1
                    if fail_count >= max_fail_count:
                        break

        # 确定条纹区域的起始和结束位置
        start_x = 0
        if end_idx == 0:
            end_x = 0
        else:
            if positions[end_idx][1] + step >= width:
                end_x = width
            else:
                end_x = positions[end_idx][0]
        return start_x, end_x

    def calculate_forte_percent(self, forte_color, box):
        """
        计算 forte 值的百分比。
        优先使用条纹检测法（detect_stripe_region），如果失败则回退到简单的颜色百分比计算。
        """
        # 裁剪区域并转换为二值图像（符合颜色范围的为白色）
        cropped = box.crop_frame(self.task.frame)
        lower_bound, upper_bound = color_range_to_bound(forte_color)
        gray = cv2.inRange(cropped, lower_bound, upper_bound)

        # 尝试检测条纹区域
        start_x, end_x = self.detect_stripe_region(gray)
        if start_x == -2:
            # 检测到干扰，返回-1表示失败
            self.logger.debug(f'calculate_forte_percent failed due to interference')
            return -1

        if start_x != -1:
            # 成功检测到条纹区域，计算该区域内白色像素占比
            stripe_area = gray[:, start_x:end_x]
            white_pixels = stripe_area.size
            total_pixels = gray.size
            ratio = white_pixels / total_pixels if total_pixels > 0 else 0
        else:
            # 条纹检测失败，使用简单的颜色百分比（并放大2倍作为近似）
            self.logger.debug(f'using calculate_color_percentage')
            ratio = self.task.calculate_color_percentage(forte_color, box) * 2
            ratio = ratio if ratio <= 1 else 1
        return ratio

    def heavy_attack(self, duration, check_combat=True, until_con_full=False):
        """
        执行重击（按住鼠标）一段时间。
        在重击过程中，会持续检查 forte 值的变化，以判断重击是否生效。
        """
        self.logger.info(f'start heavy_attack')
        self.last_forte = 0
        freeze_forte_check = False
        freeze_forte_time = 0
        # 按下鼠标开始重击
        self.task.mouse_down()
        start = time.time()
        # 循环直到超时或满足退出条件
        while time.time() - start < duration:
            # 如果设置了 until_con_full 并且协奏值已满，或者 forte 值接近0，则提前退出
            if until_con_full and self.is_con_full() or 0 <= self.get_forte() <= 0.01:
                break
            if check_combat:
                self.check_target(True)
            self.task.next_frame()

            # 管理 forte 检查的冻结状态
            if freeze_forte_check and time.time() - freeze_forte_time >= 0.2:
                freeze_forte_check = False
            if not freeze_forte_check:
                # 检查是否需要重试重击
                if self.should_retry_heavy_attack() < 0:
                    freeze_forte_check = True
                    freeze_forte_time = time.time()

        # 重击结束，释放鼠标
        self.sleep(0.1, False)
        self.task.mouse_up()
        self.waiting_for_forte_drop = False

    def should_retry_heavy_attack(self, budding=False):
        """
        判断当前的重击是否生效，如果 forte 值长时间没有下降，则认为重击失败，需要重试。
        通过监测 forte 值的变化趋势来实现。
        """
        # 获取当前 forte 值
        current_forte = self.get_forte(budding)
        if current_forte < 0:
            # 获取失败，返回-1表示需要冻结检查
            return -1

        # 计算与上次检测值的差值
        diff = self.last_forte - current_forte
        self.logger.debug(f'diff {diff}')

        # 如果差值很小（forte 没有明显下降），开始等待下降
        if not self.waiting_for_forte_drop and 0 <= diff <= 0.01:
            self.waiting_for_forte_drop = True
            self.forte_drop_timestamp = time.time()
            self.forte_diff_buffer = []

        # 如果在等待下降
        if self.waiting_for_forte_drop:
            self.forte_diff_buffer.append(diff)
            # 如果累计差值超过阈值，说明 forte 开始下降了，取消等待
            if np.array(self.forte_diff_buffer).sum() > 0.01:
                self.waiting_for_forte_drop = False
                self.logger.debug(f'diff {diff}')

        # 如果等待下降超时（0.6秒），则认为重击失败，执行重试
        if self.waiting_for_forte_drop and self.time_elapsed_accounting_for_freeze(self.forte_drop_timestamp) > 0.6:
            self.waiting_for_forte_drop = False
            self.logger.info(f'retry heavy attack')
            # 释放鼠标，短暂停顿后重新按下，以重试重击
            self.task.mouse_up()
            self.sleep(0.1, False)
            self.task.mouse_down()
            self.sleep(0.1, False)

        # 更新 last_forte
        self.last_forte = current_forte
        return 0

    def check_target(self, is_heavy_att=False):
        """
        检查当前目标是否仍然存在。
        如果目标丢失，则尝试重新进入战斗状态，并（如果正在进行重击）恢复重击。
        """
        if not self.task.has_target():
            # 如果正在进行重击，先释放鼠标
            if is_heavy_att:
                self.logger.info(f'check_target Release')
                self.task.mouse_up()
            # 重新检查战斗状态
            self.logger.info(f'check_combat')
            self.check_combat()
            self.task.next_frame()
            # 如果之前正在进行重击，重新按下鼠标
            if is_heavy_att:
                self.logger.info(f'check_target Retry')
                self.task.mouse_down()


# --- 颜色配置常量 ---
# 用于检测椿的共鸣技能图标是否进入“含苞”状态的红色范围
camellya_red_color = {
    'r': (234, 245),  # 红色通道范围
    'g': (71, 82),    # 绿色通道范围
    'b': (168, 179)   # 蓝色通道范围
}

# 用于检测普通状态下 forte 值的颜色范围
camellya_forte_color = {
    'r': (193, 255),  # 红色通道范围
    'g': (46, 93),    # 绿色通道范围
    'b': (127, 163)   # 蓝色通道范围
}

# 用于检测“含苞”状态下 forte 值的颜色范围
camellya_budding_forte_color = {
    'r': (220, 255),  # 红色通道范围
    'g': (161, 213),  # 绿色通道范围
    'b': (168, 225)   # 蓝色通道范围
}