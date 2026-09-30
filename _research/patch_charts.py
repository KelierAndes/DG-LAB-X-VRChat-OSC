# -*- coding: utf-8 -*-
"""Patch charts.py: replace selection preview with a live scrolling chart."""
import io

src = io.open('ui/charts.py', encoding='utf-8').read()

start = src.index('def render_wave_chart(')
end = src.index('PRESSURE_WINDOW_S = 60.0')
new_fn = '''def render_wave_live(samples: list[tuple[float, tuple, tuple]],
                     dark: bool = False) -> bytes:
    """Scrolling time/strength chart of actual output (last ~5 s).

    ``samples``: [(t, segs_a4, segs_b4)] oldest-first, one entry per output
    tick (100 ms, four 25 ms strength segments).  Right edge = now.
    """
    palette = _palette(dark)
    width, lane_h, gap, margin = 720, 52, 10, 4
    height = margin + 2 * (lane_h + gap) + 2
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    window = 5.0
    now = time.monotonic()
    t0 = now - window
    x0, x1 = 8, width - 8
    span = max(0.001, now - t0)

    def tx(t: float) -> int:
        return int(x0 + (t - t0) / span * (x1 - x0))

    lanes = (("A", 0), ("B", 1))
    for lane_index, (name, idx) in enumerate(lanes):
        y0 = margin + lane_index * (lane_h + gap)
        y1 = y0 + lane_h
        _lane(draw, x0, x1, y0, y1, palette)
        _label(draw, x0 + 4, y0 + 2, f"{name} STR 0-100", palette)
        for t, segs_a, segs_b in samples:
            if t < t0:
                continue
            segs = segs_a if idx == 0 else segs_b
            bar_w = max(1, int((x1 - x0) / (window / 0.1) * 0.8))
            for i, v in enumerate(segs):
                x = tx(t) + int(i * bar_w / 4)
                v = max(0, min(100, int(v)))
                h = int((y1 - y0 - 6) * (v / 100))
                if h <= 0:
                    continue
                draw.rectangle([x, y1 - 1 - h, x + bar_w, y1 - 1],
                               fill=palette["strength"])

    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    return buffer.getvalue()


'''
src = src[:start] + new_fn + src[end:]
io.open('ui/charts.py', 'w', encoding='utf-8').write(src)
print("charts patch OK")
