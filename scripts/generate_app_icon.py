from pathlib import Path

from PIL import Image, ImageDraw


target = Path(__file__).resolve().parents[1] / "src" / "bankrotai" / "assets" / "app.ico"
canvas = Image.new("RGBA", (256, 256), "#12336f")
draw = ImageDraw.Draw(canvas)
draw.rounded_rectangle((18, 18, 238, 238), radius=48, fill="#12336f", outline="#64d8b1", width=10)
draw.rectangle((62, 60, 80, 198), fill="#ffffff")
draw.rounded_rectangle((62, 58, 165, 90), radius=12, fill="#ffffff")
draw.rounded_rectangle((62, 112, 154, 144), radius=12, fill="#ffffff")
draw.rounded_rectangle((62, 166, 174, 198), radius=12, fill="#ffffff")
draw.ellipse((170, 60, 208, 98), fill="#64d8b1")
draw.line((189, 98, 189, 174), fill="#64d8b1", width=12)
draw.line((166, 174, 212, 174), fill="#64d8b1", width=12)
target.parent.mkdir(parents=True, exist_ok=True)
canvas.save(target, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
