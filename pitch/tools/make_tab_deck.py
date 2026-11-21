"""Short deck: one slide per StockFlow AI tab (what it shows, use case, business benefit) + separate speaker notes (.docx).

Run: python make_tab_deck.py   ->  StockFlowAI_app_tour.pptx  +  StockFlowAI_app_tour_speaker_notes.docx
"""
from pathlib import Path

from docx import Document
from docx.shared import Pt as DPt, RGBColor as DRGB
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

D = Path(__file__).parent
ASSETS = D.parent / "SYNAPSE" / "stock_mover" / "assets"
TABS = D / "tabs"
CROP = D / "tabs_cropped"
CROP.mkdir(exist_ok=True)

MAROON, EMBER, GOLD, BROWN = RGBColor(0x66, 0x14, 0x2E), RGBColor(0xE0, 0x4E, 0x1C), RGBColor(0xD4, 0xA0, 0x17), RGBColor(0x51, 0x1D, 0x00)
CREAM, INK, GREY, SAND, WHITE = RGBColor(0xFF, 0xFB, 0xF2), RGBColor(0x22, 0x22, 0x22), RGBColor(0x62, 0x62, 0x62), RGBColor(0xE1, 0xCE, 0xAC), RGBColor(0xFF, 0xFF, 0xFF)
GOLD_L = RGBColor(0xF5, 0xC4, 0x6B)
HEAD, BODY = "Cambria", "Calibri"

SLIDES = [
    dict(img="1_leaders", title="Leaders: the morning headline",
         shows="Four headline numbers, up to five key insights, the decisions waiting, and impact by city.",
         use="Owners and regional heads see the whole day in about two minutes.",
         benefit="Attention goes to the biggest rupee risks first: ₹1.10 Cr of sales protected today.",
         notes="This is the first screen leadership sees. Four numbers sum up the day: sales protected, hidden lost sales found, "
               "old stock put back to work, and decisions waiting. Below them, at most five plain-language insights, such as "
               "Muzaffarpur losing sales because its shelves are only 37% full. Then the decisions that need a person. The point is "
               "focus: in two minutes an owner knows where the money is at risk today, without opening a single report."),
    dict(img="2_plan", title="Plan: everything moving today",
         shows="Today's moves in plain words, pieces by kind of move, a city-to-city flow map, and area-manager alerts.",
         use="Merchandising and logistics plan the day's dispatches; each area manager gets their top five alerts.",
         benefit="Stock reaches stores before the wedding peaks: 2,710 pieces worth ₹2.17 Cr, each with a reason.",
         notes="The Plan tab is the day's work in one place. It explains in plain words what is moving and why, for example 136 items "
               "in 20 stores that would run out before a normal delivery. The flow map shows stock moving between the warehouse and "
               "cities, and every area manager gets only their top five alerts, because that is how many they will actually read. "
               "The business benefit is timing: stock arrives before the wedding-season peaks, not after."),
    dict(img="3_problems", title="Problems: what is wrong in the data, and what we did",
         shows="Each problem in plain words: why it matters, what the plan did about it, and a chart.",
         use="Buying and operations fix root causes: recount stock, re-order best-sellers, speed up supply.",
         benefit="Recovers ₹75.9 L of hidden lost sales and flags 8 sold-out best-sellers for re-order.",
         notes="Here the tool shows the problems it found in our own data. Sizes that look slow but simply were not on the shelf. "
               "A record claiming 300 sherwanis that do not exist. Best-sellers that sold out and were never re-ordered. Each card says "
               "why it matters and what the plan did, with a chart as proof. This is where hidden money is: about seventy-six lakh "
               "rupees of sales we were quietly losing, now visible and acted on."),
    dict(img="4_stores", title="Stores: one store at a glance",
         shows="Stock against capacity, category stock against minimum display, how long stock lasts, and what comes in and goes out.",
         use="Area and store managers prepare to receive and send stock, with the reason for every move.",
         benefit="Full shelves, displays that meet the minimum, and no overfilled stockrooms.",
         notes="Pick any store and see its situation in one view: how full it will be after the plan, whether each category meets its "
               "minimum display, how many days each size will last, and exactly what is arriving or leaving with the reason. Store and "
               "area managers use this to prepare. The benefit is simple: shelves stay full, displays stay compliant, and no store is "
               "sent more than it can hold."),
    dict(img="5_approve", title="Approve: the ten-minute sign-off",
         shows="39 decisions cover all 1,288 moves in three steps; download the approved list.",
         use="The merchandiser signs off each morning; any move over ₹1 lakh gets a person's approval.",
         benefit="A full day's plan approved in about ten minutes, with people firmly in control.",
         notes="Nobody can review 1,288 lines every morning, so the tool groups them into 39 decisions. Step one: urgent refills, one "
               "decision per store. Step two: each move worth over one lakh rupees, signed off individually as our rules require. Step "
               "three: everything else in four routine groups. Everything starts approved; the merchandiser unticks anything they "
               "disagree with and downloads the list. Ten minutes, and people stay in charge."),
    dict(img="6_ask", title="Ask: questions in plain English",
         shows="A chat assistant that answers only from today's plan, with one-click example questions.",
         use="Anyone can ask, for example: which stores need the most stock, or why is MZF-01 a problem?",
         benefit="Answers in seconds without an analyst; it politely declines anything off-topic.",
         notes="The Ask tab lets anyone question the plan in plain English. Ask which stores need the most stock and you get the list "
               "with pieces and rupee value in seconds. It answers only from today's plan, and if someone asks something unrelated, "
               "it politely declines, so it cannot make things up about other topics. The benefit is speed and access: managers get "
               "answers without waiting for an analyst."),
    dict(img="7_feedback", title="Feedback: the tool learns from the team",
         shows="Star ratings for each plan, requested changes, rating trends, and suggested settings.",
         use="After the stock arrives, the team rates how the plan worked and asks for adjustments.",
         benefit="The settings improve with use, which builds trust and adoption over time.",
         notes="Once stock has arrived, the team can rate the plan: were the moves right, was it easy to understand, did it save time. "
               "They can ask for changes, such as sending more stock to stores. Those requests turn into suggested settings that a "
               "manager can accept for future plans. The business benefit is a tool that gets better the more it is used, and a team "
               "that trusts it because they shaped it."),
    dict(img="8_method", title="Method: how it decides, in the open",
         shows="A diagram of the method, a plain-word glossary, all 11 rule checks, and the season effect by state.",
         use="New users, auditors and leadership can check exactly how decisions are made.",
         benefit="Transparent and safe: 11 of 11 business rules pass on every plan.",
         notes="Finally, the Method tab explains how the tool decides, with nothing hidden. A simple diagram, plain definitions of "
               "terms like selling speed and cover, the eleven business rules with a pass or fail for each, and how much more "
               "wedding dates sell in each state. This matters for trust and governance: anyone can see why a move was made, and "
               "that no rule was broken."),
]
TITLE_NOTES = ("Thank you for your time. In the next few minutes I will walk you through StockFlow AI, the tool we built for Manyavar, "
               "one tab at a time. For each tab I will cover three things: what it shows, who uses it and when, and how it helps the "
               "business. Everything you will see is live and running on the hackathon's sample data.")
CLOSE_NOTES = ("To sum up what the business gets from one morning's plan: about one point one crore rupees of sales protected, "
               "seventy-six lakh rupees of hidden lost sales brought to light, eighty-one lakh rupees of old stock put back to work, "
               "and a plan that one person approves in about ten minutes. The app is live at the address on screen, and I would be "
               "happy to take your questions.")


def crop(name):
    """Keep the app's content column (drop the empty page margins)."""
    src = Image.open(TABS / f"{name}.png")
    w, h = src.size
    out = CROP / f"{name}.png"
    src.crop((int(w * 0.135), 0, int(w * 0.865), h)).save(out)
    return out


prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BL = prs.slide_layouts[6]


def bg(s, color):
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = color


def tx(s, x, y, w, h, runs, size=16, color=INK, bold=False, font=BODY, align=None, anchor=None, spacing=None):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    if anchor:
        tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    if align is not None:
        p.alignment = align
    for t, opts in (runs if isinstance(runs, list) else [(runs, {})]):
        r = p.add_run()
        r.text = t
        r.font.size = Pt(opts.get("size", size))
        r.font.bold = opts.get("bold", bold)
        r.font.name = opts.get("font", font)
        r.font.color.rgb = opts.get("color", color)
    if spacing:
        p.line_spacing = spacing
    return tb


def shape(s, kind, x, y, w, h, fill, line=None):
    sh = s.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(1)
    sh.shadow.inherit = False
    return sh


def footer(s, n, dark=False):
    c = SAND if dark else GREY
    tx(s, 0.6, 7.0, 6, 0.3, "StockFlow AI · customized for Manyavar", 11, c)
    tx(s, 11.73, 7.0, 1.0, 0.3, str(n), 11, c, align=PP_ALIGN.RIGHT)


def logo_lockup(s, x, y, h):
    if (ASSETS / "icon.png").exists():
        s.shapes.add_picture(str(ASSETS / "icon.png"), Inches(x), Inches(y), Inches(h), Inches(h))


# ---- 1 title
s = prs.slides.add_slide(BL)
bg(s, MAROON)
logo_lockup(s, 0.8, 1.55, 1.15)
tx(s, 2.2, 1.5, 10.3, 1.2, "StockFlow AI: a tour of the app", 46, CREAM, True, HEAD, anchor=MSO_ANCHOR.MIDDLE)
tx(s, 0.8, 3.15, 11.5, 0.6, "Eight tabs. What each one shows, who uses it, and how the business benefits.", 22, GOLD_L)
if (ASSETS / "manyavar.png").exists():
    tx(s, 0.8, 4.75, 3.0, 0.4, "CUSTOMIZED FOR", 14, SAND, True)
    s.shapes.add_picture(str(ASSETS / "manyavar.png"), Inches(3.05), Inches(4.45), height=Inches(0.95))
tx(s, 0.8, 6.2, 11, 0.4, "Live app: synapse-stock-mover.streamlit.app", 16, CREAM)
s.notes_slide.notes_text_frame.text = TITLE_NOTES

# ---- tab slides
LABELS = [("WHAT IT SHOWS", EMBER, "shows"), ("USE CASE", GOLD, "use"), ("BUSINESS BENEFIT", MAROON, "benefit")]
for i, sd in enumerate(SLIDES, start=2):
    s = prs.slides.add_slide(BL)
    bg(s, CREAM)
    tx(s, 0.6, 0.38, 12.1, 0.75, sd["title"], 32, BROWN, True, HEAD, anchor=MSO_ANCHOR.MIDDLE)
    img = crop(sd["img"])
    iw, ih = Image.open(img).size
    h = 5.55
    w = h * iw / ih
    frame = shape(s, MSO_SHAPE.RECTANGLE, 0.6 - 0.04, 1.3 - 0.04, w + 0.08, h + 0.08, WHITE, SAND)
    s.shapes.add_picture(str(img), Inches(0.6), Inches(1.3), Inches(w), Inches(h))
    x0 = 0.6 + w + 0.45
    cw = 13.333 - 0.6 - x0
    for j, (lab, col, key) in enumerate(LABELS):
        y = 1.3 + j * 1.9
        card = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x0, y, cw, 1.72, WHITE, SAND)
        card.adjustments[0] = 0.08
        shape(s, MSO_SHAPE.OVAL, x0 + 0.25, y + 0.27, 0.22, 0.22, col)
        tx(s, x0 + 0.6, y + 0.2, cw - 0.85, 0.35, lab, 13, col, True)
        tx(s, x0 + 0.25, y + 0.62, cw - 0.5, 1.0, sd[key], 16, INK, key == "benefit", spacing=1.05)
    footer(s, i)
    s.notes_slide.notes_text_frame.text = sd["notes"]

# ---- close
s = prs.slides.add_slide(BL)
bg(s, MAROON)
tx(s, 0.6, 0.45, 12.1, 0.8, "What the business gets from one morning's plan", 34, CREAM, True, HEAD, anchor=MSO_ANCHOR.MIDDLE)
stats = [("₹1.10 Cr", "sales protected by refilling stores before they run out"),
         ("₹75.9 L", "hidden lost sales found on empty shelves"),
         ("₹81.4 L", "old stock moved to where it sells or brought back"),
         ("10 min", "to approve the whole plan: 39 decisions")]
for j, (big, lab) in enumerate(stats):
    x = 0.6 + j * 3.1
    card = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, 1.75, 2.85, 2.45, RGBColor(0x4A, 0x0E, 0x1C), GOLD)
    card.adjustments[0] = 0.08
    tx(s, x + 0.25, 2.0, 2.4, 0.8, big, 36, GOLD_L, True)
    tx(s, x + 0.25, 2.95, 2.4, 1.1, lab, 16, CREAM, spacing=1.05)
tx(s, 0.6, 4.75, 12.1, 0.5, "Every move explained in one line · 11 of 11 business rules pass · works on a phone", 18, SAND)
tx(s, 0.6, 5.45, 12.1, 0.5, "Try it live: synapse-stock-mover.streamlit.app", 20, GOLD_L, True)
footer(s, len(SLIDES) + 2, dark=True)
s.notes_slide.notes_text_frame.text = CLOSE_NOTES

deck = D / "StockFlowAI_app_tour.pptx"
prs.save(deck)

# ---- speaker notes as a separate Word document
doc = Document()
st = doc.styles["Normal"]
st.font.name, st.font.size = "Calibri", DPt(12)
h = doc.add_heading("StockFlow AI: app tour · speaker notes", level=0)
doc.add_paragraph("10 slides · about 8 minutes · roughly 45 seconds per slide. "
                  "Slide titles are shown in bold; read or paraphrase the notes below each.")
all_notes = [("1", "StockFlow AI: a tour of the app", TITLE_NOTES)] + \
            [(str(i), sd["title"], sd["notes"]) for i, sd in enumerate(SLIDES, start=2)] + \
            [(str(len(SLIDES) + 2), "What the business gets from one morning's plan", CLOSE_NOTES)]
for n, t, body in all_notes:
    doc.add_heading(f"Slide {n} · {t}", level=2)
    doc.add_paragraph(body)
    if n not in ("1", str(len(SLIDES) + 2)):
        sd = SLIDES[int(n) - 2]
        p = doc.add_paragraph()
        r = p.add_run("Key line to land: ")
        r.bold = True
        r.font.color.rgb = DRGB(0xE0, 0x4E, 0x1C)
        p.add_run(sd["benefit"])
notes_path = D / "StockFlowAI_app_tour_speaker_notes.docx"
doc.save(notes_path)
print("saved", deck.name, len(prs.slides), "slides;", notes_path.name)
