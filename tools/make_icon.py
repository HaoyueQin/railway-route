"""应用图标生成：铁路主题（与前端 logo 同风格：列车 + 轨道 + indigo 渐变）。

生成 assets/icon.ico（多尺寸）与 assets/icon.png（512px 源图）。
纯 PIL 绘制，无外部依赖。

用法: python tools/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 512
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_base(d, pad=56, radius=112):
    """indigo→violet 对角渐变圆角方块底。"""
    c1, c2 = (79, 70, 229), (124, 58, 237)
    box = [pad, pad, S - pad, S - pad]
    # 逐行渐变（简化：逐 4px 条带）
    for y in range(pad, S - pad, 4):
        t = (y - pad) / (S - 2 * pad)
        d.rounded_rectangle(
            [pad, y, S - pad, min(y + 4, S - pad)],
            radius=radius if y <= pad + 4 else 0,
            fill=lerp(c1, c2, t),
        )
    # 修正圆角（最后整体盖圆角遮罩）
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle(box, radius=radius, fill=255)
    return mask


def draw_icon():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    mask = draw_base(d)
    img.putalpha(mask)

    # ── 轨道（两条平行钢轨 + 枕木，左下）──
    rail_y = 392
    d.line([72, rail_y, 440, rail_y], fill=(255, 255, 255, 190), width=14)
    d.line([72, rail_y + 30, 440, rail_y + 30], fill=(255, 255, 255, 190), width=14)
    for x in range(96, 440, 44):
        d.line([x, rail_y, x, rail_y + 30], fill=(255, 255, 255, 90), width=9)

    # ── 列车（白色圆角车体 + 车窗 + 车轮）──
    # 车体
    d.rounded_rectangle([88, 132, 424, 300], radius=44, fill=(255, 255, 255, 255))
    # 车顶弧光
    d.rounded_rectangle([104, 148, 408, 172], radius=20, fill=(224, 231, 255, 255))
    # 分界线
    d.line([88, 240, 424, 240], fill=(224, 231, 255, 255), width=10)
    # 车窗（indigo）
    for wx in (136, 224, 312):
        d.rounded_rectangle([wx, 168, wx + 80, 224], radius=16, fill=(79, 70, 229, 255))
    # 车头灯（amber）
    d.ellipse([398, 176, 418, 196], fill=(251, 191, 36, 255))
    # 车轮
    for cx in (168, 344):
        d.ellipse([cx - 30, rail_y - 26, cx + 30, rail_y + 34], fill=(255, 255, 255, 255))
        d.ellipse([cx - 16, rail_y - 12, cx + 16, rail_y + 20], fill=(79, 70, 229, 120))

    # ── 轨道上方阴影（车底）──
    d.rounded_rectangle([140, 296, 372, 320], radius=12, fill=(255, 255, 255, 160))

    return img


def make_ico(img):
    """多尺寸 ico（256/128/64/48/32/16）。"""
    sizes = [256, 128, 64, 48, 32, 16]
    imgs = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    OUT.mkdir(exist_ok=True)
    imgs[0].save(OUT / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes],
                 append_images=imgs[1:])
    img.save(OUT / "icon.png")
    print(f"生成 {OUT / 'icon.ico'}（多尺寸）与 {OUT / 'icon.png'}")


if __name__ == "__main__":
    make_ico(draw_icon())
