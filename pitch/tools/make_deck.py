"""StockFlow AI owners' deck (16:9) with speaker notes. Run: python make_deck.py"""
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION

D = Path(__file__).parent
ASSETS = D.parent / "SYNAPSE" / "stock_mover" / "assets"
SHOTS = D / "shots"
MAROON, EMBER, GOLD, BROWN = RGBColor(0x66, 0x14, 0x2E), RGBColor(0xE0, 0x4E, 0x1C), RGBColor(0xD4, 0xA0, 0x17), RGBColor(0x51, 0x1D, 0x00)
CREAM, INK, GREY, SAND, WHITE = RGBColor(0xFF, 0xFB, 0xF2), RGBColor(0x22, 0x22, 0x22), RGBColor(0x62, 0x62, 0x62), RGBColor(0xE1, 0xCE, 0xAC), RGBColor(0xFF, 0xFF, 0xFF)
HEAD, BODY = "Cambria", "Calibri"

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BL = prs.slide_layouts[6]


def bg(s, color):
    f = s.background.fill
    f.solid()
    f.fore_color.rgb = color


def tx(s, x, y, w, h, text, size=16, color=INK, bold=False, font=BODY, align=None, italic=False):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    lines = text if isinstance(text, list) else [text]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        r = p.add_run()
        r.text = line
        r.font.size, r.font.bold, r.font.italic, r.font.name = Pt(size), bold, italic, font
        r.font.color.rgb = color
        if align is not None:
            p.alignment = align
        p.space_after = Pt(6)
    return tb


def card(s, x, y, w, h, fill=WHITE, line=SAND):
    c = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    c.adjustments[0] = 0.06
    c.fill.solid()
    c.fill.fore_color.rgb = fill
    c.line.color.rgb = line
    c.shadow.inherit = False
    return c


def dot(s, x, y, d, color, label):
    c = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    c.fill.solid()
    c.fill.fore_color.rgb = color
    c.line.fill.background()
    t = c.text_frame
    t.text = label
    r = t.paragraphs[0].runs[0]
    r.font.size, r.font.bold, r.font.color.rgb, r.font.name = Pt(20), True, WHITE, BODY


def title(s, text, color=BROWN):
    tx(s, 0.6, 0.4, 12.1, 0.9, text, 36, color, True, HEAD)


def brand(s, dark=False):
    if (ASSETS / "icon.png").exists():
        s.shapes.add_picture(str(ASSETS / "icon.png"), Inches(0.6), Inches(6.85), Inches(0.38), Inches(0.38))
    tx(s, 1.05, 6.85, 4, 0.4, "StockFlow AI · customized for Manyavar", 11, CREAM if dark else GREY)


def notes(s, text):
    s.notes_slide.notes_text_frame.text = text


def stat(s, x, y, w, big, label, color):
    card(s, x, y, w, 1.9)
    tx(s, x + 0.25, y + 0.2, w - 0.5, 0.9, big, 40, color, True, BODY)
    tx(s, x + 0.25, y + 1.1, w - 0.5, 0.7, label, 14, GREY)


# 1 Title
s = prs.slides.add_slide(BL); bg(s, MAROON)
if (ASSETS / "icon.png").exists():
    s.shapes.add_picture(str(ASSETS / "icon.png"), Inches(0.8), Inches(1.6), Inches(1.3), Inches(1.3))
tx(s, 2.4, 1.55, 10, 1.3, "StockFlow AI", 60, CREAM, True, HEAD)
tx(s, 2.45, 2.75, 10, 0.7, "Right stock. Right store. Right size.", 26, GOLD, True)
tx(s, 0.85, 4.0, 11, 1.2, ["A daily, ready-to-approve stock plan for Manyavar stores",
                           "Presentation to the owners · Vedant Fashions AI Hackathon 2026"], 18, CREAM)
if (ASSETS / "manyavar.png").exists():
    tx(s, 0.85, 5.55, 3, 0.4, "CUSTOMIZED FOR", 12, SAND, True)
    s.shapes.add_picture(str(ASSETS / "manyavar.png"), Inches(2.9), Inches(5.35), Inches(2.6))
notes(s, "Good morning. Thank you for your time. I'm going to show you StockFlow AI, a tool we built for Manyavar during the "
         "hackathon. In one sentence: every morning it finds stock that is in the wrong place across our stores and warehouse, "
         "and gives the merchandising team a list of moves they can approve in about ten minutes. Everything you'll see today "
         "comes from the hackathon's sample data pack: 20 stores, one warehouse, two years of sales. The numbers are real outputs "
         "of the tool on that data, and the tool is live on the web, so you can try it yourselves after this meeting. "
         "I'll take about fifteen minutes, then leave time for your questions.")

# 2 Executive summary
s = prs.slides.add_slide(BL); bg(s, CREAM); title(s, "One plan every morning, approved in ten minutes")
stat(s, 0.6, 1.6, 3.9, "≈₹1.1 Cr", "sales at MRP protected by refilling stores about to run short", EMBER)
stat(s, 4.75, 1.6, 3.9, "₹75.9 L", "hidden lost sales found: sizes empty for 4+ weeks", MAROON)
stat(s, 8.9, 1.6, 3.9, "39", "decisions cover all 1,288 moves", BROWN)
tx(s, 0.6, 4.0, 12, 2.5, ["What it does: decides what to send from the warehouse, what to move between stores, and what old stock to move or bring back.",
                          "Why you can trust it: every move has a one-line reason, and all 11 business rules are re-checked on the final plan.",
                          "What we ask: approve a pilot in two cities for wedding season."], 18, INK)
brand(s)
notes(s, "If you remember one slide, it's this one. On the sample data, the plan protects roughly one point one crore rupees of "
         "sales at MRP. That's the value of stock going to stores that would otherwise run out before the next delivery. It also "
         "uncovered about seventy-six lakh rupees of sales we were quietly losing: sizes that had been missing from shelves for a "
         "month or more. And it does this without burying the team in work. The full plan is about 1,300 individual moves, but the "
         "merchandiser approves it in 39 decisions. Every move carries a plain one-line reason, and the tool re-checks every "
         "business rule before anyone sees the list. At the end I'll ask for one thing: a pilot in two cities for the wedding season.")

# 3 Problem
s = prs.slides.add_slide(BL); bg(s, CREAM); title(s, "The same size is missing in one store and sitting unsold in another")
card(s, 0.6, 1.6, 5.9, 3.3); tx(s, 0.9, 1.8, 5.4, 0.5, "Store A · Size L", 20, BROWN, True)
tx(s, 0.9, 2.5, 5.4, 2.2, ["0 pieces on the shelf", "Customer asks, finds nothing, walks out", "Sales history shows zero, so it looks 'slow'"], 18, INK)
card(s, 6.85, 1.6, 5.9, 3.3); tx(s, 7.15, 1.8, 5.4, 0.5, "Store B · Size L", 20, BROWN, True)
tx(s, 7.15, 2.5, 5.4, 2.2, ["26 pieces unsold", "Money and display space tied up", "Ages and loses value"], 18, INK)
tx(s, 0.6, 5.2, 12, 1.4, ["On the sample data: 260 store-sizes were out of stock for 4+ weeks, while ₹74 L of stock older than 120 days sat in other stores."], 18, MAROON, True)
brand(s)
notes(s, "Here is the problem in its simplest form. In one store, a customer asks for a size and it isn't there, so they walk out. "
         "Worse, because nothing sold, our reports say that size is slow, so nobody refills it. Meanwhile, in another store, the "
         "same size sits unsold, ageing and tying up money and display space. With 20 stores, hundreds of sizes and a festival "
         "calendar that differs by state, nobody can see all of this by hand every day. On the sample data we found 260 store-sizes "
         "that had been empty for four weeks or more, while around seventy-four lakh rupees of stock older than four months sat elsewhere.")

# 4 How it works
s = prs.slides.add_slide(BL); bg(s, CREAM); title(s, "How it decides, in four steps")
steps = [("1", "Real selling speed", "Sales divided by days the size was actually on the shelf, so empty shelves don't look like weak demand", EMBER),
         ("2", "Season aware", "Wedding dates sell 22–45% more by state; Diwali and Chhath are built in", GOLD),
         ("3", "Plans every move", "Warehouse → store first, then store → store, then old stock to where it sells", MAROON),
         ("4", "Checks every rule", "Capacity, minimum display, flagship top-20, stock in transit, 30-day cover", BROWN)]
for i, (n, h, d_, c) in enumerate(steps):
    x = 0.6 + i * 3.15
    card(s, x, 1.7, 2.9, 4.3); dot(s, x + 0.25, 1.95, 0.75, c, n)
    tx(s, x + 0.25, 2.9, 2.5, 0.8, h, 20, BROWN, True, HEAD)
    tx(s, x + 0.25, 3.75, 2.5, 2.2, d_, 15, INK)
brand(s)
notes(s, "How does it work? Four steps. First, it measures how fast each size really sells, counting only the days that size was on "
         "the shelf. That one change is what exposes hidden stockouts. Second, it reads the festival and wedding calendar for each "
         "state; in this data, wedding dates sell between 22 and 45 percent more than normal days. Third, it plans the moves: "
         "warehouse to store first, sharing fairly when there isn't enough for everyone; then store to store, nearest store first; "
         "then old stock to stores where it actually sells. Fourth, before anyone sees the plan, it re-checks every business rule "
         "you gave us: store capacity, minimum display, the flagships' top-20 styles, stock already in transit, and the 30-day "
         "cover rule for any store that gives stock away.")

# 5 What it plans (chart)
s = prs.slides.add_slide(BL); bg(s, CREAM); title(s, "Most of the plan is refilling stores before they run out")
cd = CategoryChartData()
cd.categories = ["Warehouse → store", "Back to warehouse", "Old stock → where it sells", "Store → store", "Display top-up"]
cd.add_series("Pieces", (2003, 353, 279, 74, 1))
ch = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.6), Inches(1.6), Inches(7.6), Inches(4.9), cd).chart
ch.has_legend = False
ch.plots[0].has_data_labels = True
ch.plots[0].data_labels.font.size = Pt(14)
ch.plots[0].series[0].format.fill.solid(); ch.plots[0].series[0].format.fill.fore_color.rgb = EMBER
ch.category_axis.tick_labels.font.size = Pt(14); ch.category_axis.reverse_order = True
ch.value_axis.visible = False; ch.value_axis.has_major_gridlines = False
card(s, 8.6, 1.6, 4.1, 4.9)
tx(s, 8.85, 1.8, 3.6, 4.6, ["2,710 pieces", "₹2.17 Cr at MRP", "1,288 moves", "Every move has a one-line reason, an arrival date from the lead-time table, and a priority."], 18, BROWN, True)
brand(s)
notes(s, "This is what one morning's plan looks like. About two thousand pieces go from the warehouse to stores that would otherwise "
         "run short. Around 280 pieces of old stock move to stores where the same size sells. About 350 slow pieces come back to "
         "the warehouse to free display space, and a small number move between stores where the warehouse had none. In total, "
         "2,710 pieces worth about 2.2 crore rupees at MRP. Every one of those moves comes with a one-line reason and an arrival "
         "date based on the real lead times between our locations.")

# 6 Hidden problems
s = prs.slides.add_slide(BL); bg(s, CREAM); title(s, "It found the problems hidden in the data")
items = [("MZF-01, Muzaffarpur", "37% full", "Busiest A-tier store; 32 sizes empty for a month; ₹12 L at risk. Refilled first.", EMBER),
         ("KNP-03, Kanpur", "300 → 0", "Records claimed 300 sherwanis; the physical count says 0. Ignored, recount ordered.", MAROON),
         ("KUR-04, new launch", "8× in 4 weeks", "Weekly sales 50 → 401. New, not slow. Restocked from the big delivery.", GOLD),
         ("Sherwanis", "281 vs 94", "Stores need 281 for wedding season; the warehouse has 94. Shared fairly; supply flagged.", BROWN)]
for i, (h, big, d_, c) in enumerate(items):
    x, y = 0.6 + (i % 2) * 6.2, 1.6 + (i // 2) * 2.55
    card(s, x, y, 5.9, 2.3)
    tx(s, x + 0.3, y + 0.15, 5.3, 0.5, h, 16, c, True)
    tx(s, x + 0.3, y + 0.6, 5.3, 0.8, big, 32, BROWN, True)
    tx(s, x + 0.3, y + 1.4, 5.3, 0.9, d_, 15, INK)
brand(s)
notes(s, "The organisers told us they had hidden problems in the data. Here are four the tool found on its own. Muzaffarpur is our "
         "busiest A-tier store, yet its shelves were only 37 percent full, with 32 sizes empty for a month: about twelve lakh rupees "
         "at risk. It gets refilled first. In Kanpur, the stock file claimed 300 sherwanis of one size while the weekly physical count "
         "said zero; the tool refuses to plan with a number it can't trust and asks for a recount. The new Ivory Chikankari kurta set "
         "grew eight times in four weeks; it's new, not slow, so it's restocked. And for wedding season, stores need 281 sherwanis "
         "while the warehouse has 94; the tool shares them fairly and flags supply to the buying team.")

# 7 Sold-out hits chart
s = prs.slides.add_slide(BL); bg(s, CREAM); title(s, "Zero sales isn't always a slow product")
cd = CategoryChartData()
cd.categories = ["Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"]
cd.add_series("MOJ-03 Rust Zari Mojari", (150, 142, 192, 88, 31, 1, 2, 1, 2))
cd.add_series("KUR-12 Teal Cotton Silk Kurta", (120, 145, 196, 88, 81, 33, 20, 6, 4))
ch = s.shapes.add_chart(XL_CHART_TYPE.LINE_MARKERS, Inches(0.6), Inches(1.6), Inches(7.8), Inches(4.9), cd).chart
ch.has_legend = True; ch.legend.position = XL_LEGEND_POSITION.BOTTOM; ch.legend.include_in_layout = False; ch.legend.font.size = Pt(13)
for ser, c in zip(ch.plots[0].series, (EMBER, MAROON)):
    ser.format.line.color.rgb = c; ser.format.line.width = Pt(3)
ch.value_axis.has_major_gridlines = False; ch.value_axis.tick_labels.font.size = Pt(12); ch.category_axis.tick_labels.font.size = Pt(12)
card(s, 8.8, 1.6, 3.9, 4.9)
tx(s, 9.05, 1.8, 3.4, 4.6, ["8 best-sellers ran out", "Sales fell because the shelves emptied, not because customers stopped buying.",
                            "Nothing is on order.", "Action: re-order now. The plan never pulls them back as 'slow'."], 17, BROWN)
brand(s)
notes(s, "This is the insight I'd most like you to take away. Look at the Rust Zari Mojari: 192 pairs sold in April, then almost "
         "nothing from July. A normal report would call it a slow product. It isn't. The chain simply ran out: four pairs left "
         "across all stores, and nothing on order. The Teal Cotton Silk kurta set shows the same pattern. The tool spots this "
         "automatically, flags eight such best-sellers for re-order, and makes sure none of them is ever sent back to the "
         "warehouse as dead stock. This is the brief's warning, that zero sales isn't always a slow product, turned into a decision for the buying team.")

# 8 Ten-minute approval (screenshot)
s = prs.slides.add_slide(BL); bg(s, CREAM); title(s, "1,288 moves become 39 decisions")
for i, (n, h, d_, c) in enumerate([("20", "Urgent refills", "one decision per store", EMBER),
                                   ("15", "Sign-offs", "each move over ₹1 lakh", MAROON),
                                   ("4", "Routine groups", "one per kind of move", BROWN)]):
    y = 1.65 + i * 1.6
    card(s, 0.6, y, 4.6, 1.4); tx(s, 0.85, y + 0.15, 1.4, 1.0, n, 40, c, True)
    tx(s, 2.2, y + 0.2, 2.9, 0.5, h, 18, BROWN, True); tx(s, 2.2, y + 0.7, 2.9, 0.5, d_, 14, GREY)
shot = Path(r"C:\Users\Acer\AppData\Local\Temp\claude\bc9547e810121bb8\a7d90dda-d25a-498d-8bc5-7be06e12924e\scratchpad\fixes\desk_approve.png")
if not shot.exists():
    shot = SHOTS / "07_approve.png"
if shot.exists():
    s.shapes.add_picture(str(shot), Inches(5.6), Inches(1.65), Inches(7.1))
brand(s)
notes(s, "A plan is only useful if people act on it. Nobody will read 1,288 lines every morning, so the tool groups them. Step one: "
         "20 urgent refills, one decision per store. Step two: 15 moves worth over one lakh rupees each, which your rules say need a "
         "person's sign-off. Step three: everything else in four routine groups. Everything starts approved; the merchandiser unticks "
         "what they disagree with and downloads the approved list. That's the ten-minute approval the brief asked for, and every "
         "line is still there with its reason if anyone wants to check.")

# 9 Trust
s = prs.slides.add_slide(BL); bg(s, CREAM); title(s, "Safe by design: every rule checked, independently audited")
rules = ["No store filled beyond capacity", "Minimum display kept in every category", "Flagships keep their top-20 styles",
         "Stock in transit counted, never sent twice", "Giving stores keep 30+ days of cover", "Lead times match the route table",
         "Every move explained in one line", "Moves over ₹1 lakh flagged for sign-off"]
for i, r in enumerate(rules):
    x, y = 0.6 + (i % 2) * 6.2, 1.65 + (i // 2) * 1.05
    card(s, x, y, 5.9, 0.85); dot(s, x + 0.18, y + 0.14, 0.57, EMBER, "✓")
    tx(s, x + 0.95, y + 0.2, 4.8, 0.5, r, 17, INK)
tx(s, 0.6, 6.0, 12, 0.6, "11 of 11 rules pass. A separate audit rebuilt every check from the raw data, using three different definitions of '30 days of cover'.", 15, MAROON, True)
brand(s)
notes(s, "You'll rightly ask whether you can trust a machine-made plan. Two answers. First, every business rule from the brief is "
         "built in and re-checked on the finished plan: capacity, minimum display, flagship protection, stock in transit, the "
         "30-day cover rule, lead times, one-line reasons, and sign-off for big moves. All eleven checks pass. Second, we audited "
         "it independently: a separate script rebuilt every check from the raw data without trusting the tool's own numbers, "
         "including three different ways of measuring 30 days of cover. It passes all of them. The plan is also identical every "
         "time you run it, and it takes about twelve seconds.")

# 10 Product
s = prs.slides.add_slide(BL); bg(s, CREAM); title(s, "Built for the people who use it")
for i, (h, d_) in enumerate([("Leaders view", "Key numbers, insights and decisions on one screen"),
                             ("Problems explained", "What, why it matters, what we did, with a chart"),
                             ("Ask the plan", "A chatbot that answers only about the plan"),
                             ("Works on a phone", "Area managers can act from the shop floor")]):
    y = 1.65 + i * 1.2
    dot(s, 0.6, y + 0.05, 0.65, [EMBER, MAROON, GOLD, BROWN][i], str(i + 1))
    tx(s, 1.45, y, 5, 0.5, h, 19, BROWN, True); tx(s, 1.45, y + 0.48, 5, 0.6, d_, 15, GREY)
mob = SHOTS / "10_mobile_leaders.png"
if mob.exists():
    s.shapes.add_picture(str(mob), Inches(9.3), Inches(1.4), height=Inches(5.3))
tx(s, 0.6, 6.4, 8.5, 0.4, "Live: synapse-stock-mover.streamlit.app", 15, EMBER, True)
brand(s)
notes(s, "The tool is a web app that's already live, styled in Manyavar's own look. Leaders get one screen with the key numbers "
         "and the decisions waiting for them. Every problem is explained in plain language, with a chart. Anyone can ask the plan "
         "a question, such as which stores need the most stock, and the assistant answers only from the plan, politely declining anything "
         "else. And it works on a phone, so area managers can act from the shop floor. It's run manually, at the press of a button "
         "or with one command, so your team stays in control of when a plan is made.")

# 11 Ask
s = prs.slides.add_slide(BL); bg(s, MAROON)
tx(s, 0.6, 0.5, 12, 0.9, "What we ask of you today", 36, CREAM, True, HEAD)
for i, (h, d_) in enumerate([("Approve a wedding-season pilot", "Two cities, eight weeks, run each morning by one merchandiser"),
                             ("Act on today's findings", "Re-order the sold-out hits; speed up sherwani supply; recount KNP-03"),
                             ("Measure what matters", "Stockout days, sell-through of aged stock, and approval time per day")]):
    y = 1.75 + i * 1.45
    dot(s, 0.6, y, 0.75, [EMBER, GOLD, SAND][i], str(i + 1))
    tx(s, 1.6, y - 0.05, 10.5, 0.5, h, 22, CREAM, True); tx(s, 1.6, y + 0.5, 10.5, 0.6, d_, 16, SAND)
tx(s, 0.6, 6.15, 12, 0.5, "Thank you · synapse-stock-mover.streamlit.app · github.com/lorenjoseph-create/SYNAPSE", 15, GOLD, True)
brand(s, dark=True)
notes(s, "So here is what we ask. First, approve a pilot for wedding season: two cities, eight weeks, run each morning by one "
         "merchandiser who approves the plan in about ten minutes. Second, act on what the tool already found: re-order the "
         "sold-out best-sellers, speed up sherwani supply, and recount the Kanpur sherwanis. Third, let's agree how we'll measure "
         "success: fewer days with empty shelves, more of the old stock sold, and how long approval takes each day. "
         "Thank you. The app is live at the address on screen, and I'm happy to take your questions.")

out = D / "StockFlowAI_owners_deck.pptx"
prs.save(out)
print("saved", out, len(prs.slides), "slides")
