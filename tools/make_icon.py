r"""coco-ww 图标: 在原图标上画一个被抱着的椰子(切开顶、露白肉) + 两只小手。4 倍超采样画完再缩回 300。

    python\python.exe -X utf8 tools\make_icon.py tools\icon_original.png icons\icon.png
同时写 icons\icon.ico(多尺寸)。加第三个参数 straw 会多画一根吸管(试过, 挡脸, 没用)。
改完图标要重跑 tools\make_exe.py, exe 里的图标才会跟着换。
"""
import math
import os
import sys
from PIL import Image, ImageDraw, ImageFilter

SRC, OUT = sys.argv[1], sys.argv[2]
STRAW = len(sys.argv) > 3 and sys.argv[3] == 'straw'
S = 4
W = 300 * S

LINE = (34, 26, 24, 255)
SHELL = (139, 88, 52, 255)
SHELL_DARK = (101, 60, 34, 255)
SHELL_LIGHT = (178, 122, 76, 255)
FLESH = (255, 250, 240, 255)
WATER = (236, 228, 210, 255)
SKIN = (244, 228, 216, 255)
SKIN_SHADE = (226, 196, 186, 255)
BLUSH = (236, 176, 172, 255)


def s(v):
    return int(round(v * S))


def box(cx, cy, rx, ry):
    return [s(cx - rx), s(cy - ry), s(cx + rx), s(cy + ry)]


def ellipse_with_line(d, cx, cy, rx, ry, fill, lw=2.6):
    d.ellipse(box(cx, cy, rx + lw / 2, ry + lw / 2), fill=LINE)
    d.ellipse(box(cx, cy, rx - lw / 2, ry - lw / 2), fill=fill)


base = Image.open(SRC).convert('RGBA').resize((W, W), Image.LANCZOS)
layer = Image.new('RGBA', (W, W), (0, 0, 0, 0))
d = ImageDraw.Draw(layer)

# 椰子: 下半截出画, 只露上大半个
cx, cy, rx, ry = 160, 294, 62, 54
ellipse_with_line(d, cx, cy, rx, ry, SHELL, lw=3.2)

# 壳的明暗: 右下压暗, 左上一道高光
shade = Image.new('L', (W, W), 0)
ImageDraw.Draw(shade).ellipse(box(cx + 16, cy + 14, rx - 6, ry - 4), fill=255)
shade = shade.filter(ImageFilter.GaussianBlur(s(10)))
mask_shell = Image.new('L', (W, W), 0)
ImageDraw.Draw(mask_shell).ellipse(box(cx, cy, rx - 1.6, ry - 1.6), fill=255)
dark = Image.new('RGBA', (W, W), SHELL_DARK)
layer.paste(dark, (0, 0), Image.composite(shade, Image.new('L', (W, W), 0), mask_shell))
hl = Image.new('L', (W, W), 0)
ImageDraw.Draw(hl).ellipse(box(cx - 30, cy - 26, 16, 9), fill=200)
hl = hl.rotate(28, center=(s(cx - 30), s(cy - 26)), resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(s(3)))
layer.paste(Image.new('RGBA', (W, W), SHELL_LIGHT), (0, 0), Image.composite(hl, Image.new('L', (W, W), 0), mask_shell))
d = ImageDraw.Draw(layer)

# 纤维短线
fibers = [(-44, -8, -36, 6), (-30, 4, -24, 18), (36, -6, 44, 8), (22, 10, 28, 24), (-8, 14, -2, 28), (50, 6, 54, 18)]
for x0, y0, x1, y1 in fibers:
    d.line([s(cx + x0), s(cy + y0), s(cx + x1), s(cy + y1)], fill=(88, 52, 30, 255), width=s(1.6))

# 切开的顶: 白肉一圈 + 中间椰汁
top_cy = cy - ry + 14
ellipse_with_line(d, cx, top_cy, 43, 12, FLESH, lw=2.4)
d.ellipse(box(cx, top_cy + 1.6, 28, 6.2), fill=WATER)
d.ellipse(box(cx, top_cy + 1.6, 28, 6.2), outline=(214, 200, 176, 255), width=s(1.1))
d.ellipse(box(cx - 9, top_cy, 8, 1.8), fill=(255, 255, 255, 255))

if STRAW:
    # 吸管: 从椰汁里斜着伸出来, 停在下巴右边
    p0 = (cx + 14, top_cy + 1)
    p1 = (cx + 46, top_cy - 44)
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    nx, ny = -math.sin(ang), math.cos(ang)
    half = 4.2
    poly = [(p0[0] + nx * half, p0[1] + ny * half), (p1[0] + nx * half, p1[1] + ny * half),
            (p1[0] - nx * half, p1[1] - ny * half), (p0[0] - nx * half, p0[1] - ny * half)]
    d.polygon([(s(x), s(y)) for x, y in poly], fill=(250, 250, 250, 255), outline=LINE, width=s(1.8))
    # 红条纹
    for t in (0.18, 0.42, 0.66, 0.9):
        mx, my = p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t
        ux, uy = math.cos(ang) * 3, math.sin(ang) * 3
        d.polygon([(s(mx + nx * (half - 1) - ux), s(my + ny * (half - 1) - uy)),
                   (s(mx + nx * (half - 1) + ux), s(my + ny * (half - 1) + uy)),
                   (s(mx - nx * (half - 1) + ux), s(my - ny * (half - 1) + uy)),
                   (s(mx - nx * (half - 1) - ux), s(my - ny * (half - 1) - uy))], fill=(232, 86, 92, 255))
    d.ellipse(box(p1[0], p1[1], half + 0.4, 2.2), fill=(250, 250, 250, 255), outline=LINE, width=s(1.6))


def hand(hx, hy, mirror):
    """圆手套状的小手, 手指朝椰子里侧。"""
    rot = -18 if not mirror else 18
    hand_layer = Image.new('RGBA', (W, W), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hand_layer)
    sign = 1 if not mirror else -1
    ellipse_with_line(hd, hx, hy, 17, 14, SKIN, lw=2.8)
    hd.ellipse(box(hx - sign * 5, hy + 6, 8, 4), fill=SKIN_SHADE)
    # 两道短指缝, 在朝椰子那一侧
    for k in (-3.5, 3.5):
        x0 = hx + sign * 9
        hd.line([s(x0), s(hy + k), s(x0 + sign * 7), s(hy + k * 1.15)], fill=LINE, width=s(1.7))
    # 拇指: 上沿一个小鼓包
    ellipse_with_line(hd, hx + sign * 3, hy - 11, 7, 5, SKIN, lw=2.4)
    hd.ellipse(box(hx + sign * 3, hy - 8, 6.2, 3), fill=SKIN)
    hand_layer = hand_layer.rotate(rot, center=(s(hx), s(hy)), resample=Image.BICUBIC)
    layer.alpha_composite(hand_layer)


# 袖子: 黑衣服从两侧伸过来
for sx, sy, hx, hy, mirror in ((70, 300, 104, 268, False), (252, 300, 216, 268, True)):
    d.line([s(sx), s(sy), s(hx), s(hy)], fill=LINE, width=s(26))
    d.line([s(sx), s(sy), s(hx + (-4 if not mirror else 4)), s(hy + 4)], fill=(58, 58, 60, 255), width=s(20))
hand(104, 266, False)
hand(216, 266, True)

# 整体加一圈浅色描边, 小尺寸下椰子和黑衣服分得开
alpha = layer.split()[3]
glow = alpha.filter(ImageFilter.MaxFilter(s(1.5) * 2 + 1)).filter(ImageFilter.GaussianBlur(s(0.8)))
halo = Image.new('RGBA', (W, W), (255, 255, 255, 0))
halo.putalpha(glow.point(lambda v: int(v * 0.4)))

out = base.copy()
out.alpha_composite(halo)
out.alpha_composite(layer)
out = out.resize((300, 300), Image.LANCZOS)
out.save(OUT, optimize=True)
ico = os.path.splitext(OUT)[0] + '.ico'
out.resize((256, 256), Image.LANCZOS).save(
    ico, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)])
print('saved', OUT, ico)
