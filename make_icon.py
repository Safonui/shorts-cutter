# -*- coding: utf-8 -*-
"""Рисует иконку приложения и графику для Microsoft Store.

Запуск:  .venv\\Scripts\\python.exe make_icon.py

Рисуется одна большая картинка 1024×1024 и уменьшается до нужных размеров
(так края остаются гладкими). Стиль — тот же HUD, что и в приложении: почти
чёрный фон, голубой неоновый контур, розовый акцент.

Что создаётся:
* `assets/icon.ico` — иконка приложения (16…256 px);
* `assets/icon-256.png` — крупный PNG (для документации и лаунчера);
* `assets/store/*.png` — ассеты, которые требует Microsoft Store.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
STORE = ASSETS / "store"

BG = (10, 10, 20, 255)          # #0A0A14
PANEL = (20, 20, 42, 255)       # #14142A
CYAN = (0, 229, 255, 255)       # #00E5FF
PINK = (255, 46, 151, 255)      # #FF2E97
VIOLET = (157, 78, 221, 255)    # #9D4EDD
WHITE = (234, 234, 255, 255)

SIZE = 1024
SCALE = SIZE / 1024


def s(value: float) -> float:
    """Перевести координаты из «сетки 1024» в текущий размер."""
    return value * SCALE


def draw_icon() -> Image.Image:
    """Иконка: вертикальный кадр 9:16, розовая кнопка воспроизведения, субтитры."""
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Фон со скруглением и неоновой рамкой.
    radius = s(180)
    draw.rounded_rectangle([s(24), s(24), s(1000), s(1000)], radius=radius, fill=BG,
                           outline=CYAN, width=int(s(26)))

    # Внутренний градиент-подложка (имитация свечения сверху).
    for step in range(28):
        ratio = step / 28
        colour = tuple(int(PANEL[index] + (VIOLET[index] - PANEL[index]) * (1 - ratio) * 0.55)
                       for index in range(3)) + (255,)
        draw.rounded_rectangle([s(60 + step * 2), s(60 + step * 2),
                                s(964 - step * 2), s(964 - step * 2)],
                               radius=radius - s(30), outline=colour, width=int(s(8)))

    # Вертикальный кадр 9:16 — суть приложения.
    frame = [s(316), s(150), s(708), s(874)]
    draw.rounded_rectangle(frame, radius=s(56), fill=(13, 6, 47, 255),
                           outline=VIOLET, width=int(s(18)))

    # Розовая кнопка воспроизведения в центре кадра.
    draw.polygon([(s(452), s(392)), (s(452), s(632)), (s(640), s(512))], fill=PINK)

    # Строки субтитров внизу кадра.
    draw.rounded_rectangle([s(366), s(760), s(658), s(792)], radius=s(16), fill=WHITE)
    draw.rounded_rectangle([s(406), s(812), s(618), s(840)], radius=s(14), fill=CYAN)

    return image


def save_ico(image: Image.Image) -> None:
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    image.save(ASSETS / "icon.ico", format="ICO", sizes=sizes)
    print(f"  icon.ico: {', '.join(f'{w}×{h}' for w, h in sizes)}")


def save_store_assets(image: Image.Image) -> None:
    """Ассеты, которые требует пакет Microsoft Store."""
    STORE.mkdir(parents=True, exist_ok=True)
    required = {
        "Square44x44Logo.png": (44, 44),
        "Square150x150Logo.png": (150, 150),
        "StoreLogo.png": (50, 50),
    }
    for name, size in required.items():
        image.resize(size, Image.Resampling.LANCZOS).save(STORE / name)
    # Широкий логотип 310×150: иконка слева, тёмный фон справа.
    wide = Image.new("RGBA", (310, 150), BG)
    icon = image.resize((132, 132), Image.Resampling.LANCZOS)
    wide.alpha_composite(icon, (12, 9))
    ImageDraw.Draw(wide).line([(0, 149), (310, 149)], fill=CYAN, width=3)
    wide.save(STORE / "Wide310x150Logo.png")
    # Экран заставки 620×300.
    splash = Image.new("RGBA", (620, 300), BG)
    splash.alpha_composite(image.resize((240, 240), Image.Resampling.LANCZOS), (190, 30))
    ImageDraw.Draw(splash).rectangle([0, 296, 620, 300], fill=CYAN)
    splash.save(STORE / "SplashScreen.png")
    for name, size in (("SmallTile.png", (71, 71)), ("LargeTile.png", (310, 310))):
        image.resize(size, Image.Resampling.LANCZOS).save(STORE / name)
    print(f"  ассеты Store: {len(list(STORE.glob('*.png')))} файлов в {STORE.name}/")


def main() -> int:
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Нужен Pillow: .venv\\Scripts\\python.exe -m pip install pillow")
        return 1
    ASSETS.mkdir(exist_ok=True)
    image = draw_icon()
    image.resize((256, 256), Image.Resampling.LANCZOS).save(ASSETS / "icon-256.png")
    print("Иконка нарисована:")
    save_ico(image)
    save_store_assets(image)
    print(f"  icon-256.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
