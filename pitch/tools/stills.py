"""Quick layout check: one still per scene (no audio, no encoding)."""
import make_video as mv

for i, (fn, narration) in enumerate(mv.SCENES):
    img = (mv.HEROIMG if fn in (mv.s_intro, mv.s_cta) else mv.BGIMG).copy()
    d = 12.0
    fn(img, d * 0.8, d)
    if fn not in (mv.s_intro, mv.s_cta):
        mv.logo(img, 40, 28, 48, 1.0)
        mv.text(img, (102, 52), "StockFlow AI", 30, "serif", mv.BROWN, 1.0, "lm")
        lw = mv.MVLOGO.width * 40 / mv.MVLOGO.height
        mv.mv_logo(img, mv.W - 40 - lw, 32, 40, 1.0)
        mv.text(img, (mv.W - 56 - lw, 52), "CUSTOMIZED FOR", 18, "sb", mv.MUTED, 1.0, "rm")
    mv.caption(img, mv.captions(narration, 8, 0)[1][2])
    img.resize((960, 540)).save(mv.D / f"still_{i}.png")
print("ok")
