# StockFlow phone app (Android, iPhone, any browser)

One app for people on the floor: approve trips by region, mark transfers sent and received (scan the barcodes to check),
count stock by scanning or by photographing a count sheet, and resolve issues. It talks to the StockFlow API
(`stock_mover/api.py`), so it needs that server running.

Actions taken without signal (decisions, counts, sent/received) are kept on the phone and sent when it is back online.
API data is never cached on the phone; only the app itself is.

## Three ways to put it on phones

| Way | Effort | Good for |
|---|---|---|
| **Install from the browser** (web app) | none: open `https://<api-server>/app/` and choose "Add to Home screen" (Android Chrome: "Install app") | pilots, every phone, no app stores |
| **Android app** (APK / Play Store) | build in Android Studio, or run the "Build Android app" GitHub workflow for a test APK | company Android phones, MDM |
| **iPhone app** (App Store / TestFlight) | build in Xcode on a Mac with an Apple Developer account | company iPhones |

The camera works in all three (HTTPS is required for the browser version).

## Build

```bash
cd mobile
npm ci
npm run build          # web app -> dist/ (the API serves it at /app/)
npx cap sync           # copy it into the native projects
npx cap open android   # Android Studio: Build > Generate Signed App Bundle / APK
npx cap open ios       # Xcode (Mac): set the team, then Product > Archive
```

In the native apps the first screen asks for the server address (e.g. `https://stock.yourcompany.com`); the API must
allow the app's origin, which it does by default (`capacitor://localhost`, `https://localhost`).

App id: `com.stockflow.app` (change it in `capacitor.config.json` before the first store upload). Icon source:
`resources/icon.png` (generate all sizes with `npx @capacitor/assets generate`).

## What was tested

In a phone-sized browser (390×844) against the real API: sign in, approve and reject trips, change a line, release,
mark sent, photograph a count sheet (18 lines read), save a count, open issues, and an approval made offline that was
sent automatically on reconnect. The Android and iOS projects are generated and include camera permissions; they
have not been compiled here (no Android SDK or Mac in this environment): use the workflow or Android Studio / Xcode.
