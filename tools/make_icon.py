"""Draw the iDesktop icon (assets/idesktop.ico + assets/idesktop.png). Usage: python tools/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024  # draw big, scale down for each icon size
out = Path(__file__).resolve().parents[1] / "assets"
out.mkdir(exist_ok=True)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# Soft shadow under the phone.
sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(sh).rounded_rectangle((300, 130, 744, 950), 120, fill=(0, 0, 0, 150))
img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(28)))

d = ImageDraw.Draw(img)
# Titanium rim, black bezel, screen.
d.rounded_rectangle((282, 90, 742, 930), 118, fill=(150, 147, 140, 255))
d.rounded_rectangle((292, 100, 732, 920), 110, fill=(8, 8, 10, 255))
screen = Image.new("RGBA", (400, 780), (0, 0, 0, 0))
g = ImageDraw.Draw(screen)
for y in range(780):  # blue -> violet wallpaper
    t = y / 779
    g.line([(0, y), (400, y)], fill=(int(40 + 110 * t), int(110 - 60 * t), int(255 - 40 * t), 255))
mask = Image.new("L", screen.size, 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, 399, 779), 92, fill=255)
img.paste(screen, (312, 120), mask)
# Dynamic Island and a "desktop window" glyph on the screen.
d.rounded_rectangle((452, 150, 572, 186), 18, fill=(0, 0, 0, 255))
d.rounded_rectangle((382, 420, 642, 600), 22, outline=(255, 255, 255, 235), width=22)
d.line((382, 470, 642, 470), fill=(255, 255, 255, 235), width=18)
d.rectangle((476, 600, 548, 660), fill=(255, 255, 255, 235))
d.rounded_rectangle((430, 652, 594, 680), 12, fill=(255, 255, 255, 235))

img.resize((512, 512), Image.LANCZOS).save(out / "idesktop.png")
img.save(out / "idesktop.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out / "idesktop.ico", "and", out / "idesktop.png")
