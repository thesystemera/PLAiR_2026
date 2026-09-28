from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "brand" / "2026-refresh"
PUBLIC = ROOT / "client" / "public" / "images"
CHARCOAL = (23, 18, 16)
IVORY = (255, 248, 233)
AMBER = (245, 158, 11)


def cleaned_radio():
    radio = Image.open(BRAND / "radio-exact-crop.png").convert("RGBA")
    alpha = radio.getchannel("A")
    draw = ImageDraw.Draw(alpha)
    draw.rectangle((0, 0, 626, 47), fill=0)
    draw.rectangle((0, 0, 29, 626), fill=0)
    draw.rectangle((615, 0, 626, 626), fill=0)
    draw.rectangle((0, 612, 626, 626), fill=0)
    radio.putalpha(alpha)
    return radio


def make_wordmark(color, output):
    wordmark = Image.new("RGBA", (2000, 600))
    draw = ImageDraw.Draw(wordmark)
    font = ImageFont.truetype("C:/Windows/Fonts/ariblk.ttf", 440)
    start_x = 290
    top_y = 235
    draw.text((start_x, top_y), "PLA", font=font, fill=color, anchor="lt")
    stem_x = start_x + round(font.getlength("PLA")) + 10
    draw.rounded_rectangle((stem_x, top_y + 2, stem_x + 76, top_y + 320), radius=12, fill=color)
    draw.text((stem_x + 88, top_y), "R", font=font, fill=color, anchor="lt")
    center_x = stem_x + 38
    center_y = 190
    draw.arc((center_x - 160, center_y - 160, center_x + 160, center_y + 160), 203, 337, fill=AMBER, width=25)
    draw.arc((center_x - 105, center_y - 105, center_x + 105, center_y + 105), 205, 335, fill=AMBER, width=24)
    draw.ellipse((center_x - 34, center_y - 34, center_x + 34, center_y + 34), fill=AMBER)
    wordmark.save(output)
    return wordmark


def make_mono():
    mask = Image.new("L", (1024, 1024))
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((90, 100, 934, 924), radius=160, fill=255)
    draw.rounded_rectangle((170, 215, 854, 630), radius=70, fill=0)
    font = ImageFont.truetype("C:/Windows/Fonts/ariblk.ttf", 142)
    draw.text((220, 405), "PLAiR", font=font, fill=255, anchor="lt")
    draw.arc((500, 260, 700, 460), 205, 335, fill=255, width=28)
    draw.arc((535, 295, 665, 425), 205, 335, fill=255, width=24)
    draw.ellipse((579, 365, 621, 407), fill=255)
    for y in (715, 765, 815):
        draw.rounded_rectangle((340, y, 684, y + 23), radius=11, fill=0)
    for center_x in (230, 794):
        draw.ellipse((center_x - 65, 704, center_x + 65, 834), fill=0)
        draw.ellipse((center_x - 34, 735, center_x + 34, 803), fill=255)
    white = Image.new("RGBA", (1024, 1024), (255, 255, 255, 0))
    white.putalpha(mask)
    return white


def make_social(radio, wordmark):
    card = Image.new("RGBA", (1200, 630), (*CHARCOAL, 255))
    glow = Image.new("RGBA", card.size)
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-130, -80, 690, 740), fill=(245, 158, 11, 80))
    card = Image.alpha_composite(card, glow.filter(ImageFilter.GaussianBlur(130)))
    radio_card = radio.resize((540, 540), Image.Resampling.LANCZOS)
    card.alpha_composite(radio_card, (25, 45))
    mark = wordmark.resize((650, 195), Image.Resampling.LANCZOS)
    card.alpha_composite(mark, (540, 135))
    draw = ImageDraw.Draw(card)
    tagline_font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 43)
    draw.text((600, 372), "Your music, your voice", font=tagline_font, fill=IVORY)
    draw.rounded_rectangle((600, 453, 1040, 459), radius=3, fill=AMBER)
    card.convert("RGB").save(BRAND / "social-card.png")
    card.convert("RGB").save(PUBLIC / "og-image.jpg", quality=92, subsampling=0)


def make_frame():
    frame = Image.new("RGB", (1080, 1920), CHARCOAL)
    pixels = frame.load()
    for y in range(frame.height):
        fade = y / frame.height
        for x in range(frame.width):
            top_glow = max(0, 1 - ((x - 540) / 650) ** 2 - (y / 720) ** 2)
            pixels[x, y] = (
                int(23 - 9 * fade + 31 * top_glow),
                int(18 - 7 * fade + 14 * top_glow),
                int(16 - 6 * fade + 2 * top_glow),
            )
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle((85, 410, 995, 1835), radius=62, fill=(28, 24, 22), outline=(102, 70, 39), width=5)
    draw.rounded_rectangle((88, 100, 300, 113), radius=6, fill=AMBER)
    frame.save(BRAND / "screenshot-frame.png")


def main():
    PUBLIC.mkdir(parents=True, exist_ok=True)
    radio = cleaned_radio()
    symbol = radio.resize((1024, 1024), Image.Resampling.LANCZOS)
    symbol.save(BRAND / "symbol-transparent.png")
    background = Image.new("RGBA", (1024, 1024), (*CHARCOAL, 255))
    background.alpha_composite(symbol)
    icon = background.convert("RGB")
    icon.save(BRAND / "icon-master.png")
    icon.save(PUBLIC / "plair_icon.png")
    for size in (192, 512):
        icon.resize((size, size), Image.Resampling.LANCZOS).save(PUBLIC / f"plair_icon_{size}.png")
        maskable = Image.new("RGB", (size, size), CHARCOAL)
        inset = round(size * 0.1)
        inner = icon.resize((size - 2 * inset, size - 2 * inset), Image.Resampling.LANCZOS)
        maskable.paste(inner, (inset, inset))
        name = "plair_icon_maskable_192.png" if size == 192 else "plair_icon_maskable.png"
        maskable.save(PUBLIC / name)
    icon.resize((180, 180), Image.Resampling.LANCZOS).save(PUBLIC / "apple-touch-icon.png")
    icon.save(PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    wordmark = make_wordmark(IVORY, BRAND / "wordmark-white.png")
    make_wordmark(CHARCOAL, BRAND / "wordmark-dark.png")
    mono = make_mono()
    mono.save(BRAND / "symbol-mono-white.png")
    mono.resize((96, 96), Image.Resampling.LANCZOS).save(PUBLIC / "badge-mono-96.png")
    make_social(radio, wordmark)
    make_frame()


if __name__ == "__main__":
    main()
