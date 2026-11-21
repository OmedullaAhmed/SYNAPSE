# Pitch materials · StockFlow AI (customized for Manyavar)

| File | What it is |
|---|---|
| `StockFlowAI_pitch.mp4` | 2-minute pitch video (1080p, voice-over and captions) |
| `StockFlowAI_app_tour.pptx` | Short 10-slide deck: one slide per app tab, covering what it shows, the use case and the business benefit |
| `StockFlowAI_app_tour_speaker_notes.docx` | Speaker notes for the app tour, one section per slide (also in each slide's notes pane) |
| `StockFlowAI_owners_deck.pptx` | 11-slide presentation for the owners, with speaker notes in each slide's notes pane |

All figures come from the hackathon's sample data pack and match the live app:
https://synapse-stock-mover.streamlit.app

## How they were made (`tools/`)

| Script | Builds |
|---|---|
| `capture.py`, `capture_chat.py`, `capture_tabs.py` | App screenshots, taken with headless Microsoft Edge through Playwright |
| `make_video.py` (+ `stills.py` for layout checks) | The pitch video: Pillow frames, Windows text-to-speech voice, ffmpeg via imageio-ffmpeg |
| `make_tab_deck.py` | The app-tour deck and its speaker-notes document (python-pptx, python-docx) |
| `make_deck.py` | The owners' deck (python-pptx) |
| `export_slides.ps1` | Exports slides to PNG with an installed PowerPoint, for visual checks |

The scripts were run from a local working copy laid out as `M. Project/pitch_video` next to `M. Project/SYNAPSE`,
so adjust their paths if you run them from inside the repo.
