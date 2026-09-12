# Clipboard Stacker (Android v1)

A local-first clipboard history app. It captures text while the activity is open and accepts shared text from Android's Share sheet. Clipboard access is intentionally foreground-only, consistent with modern Android privacy restrictions.

## Included
- Active foreground clipboard capture
- Share-to-Stack (`text/plain`)
- Local history (max 500), search, pin/unpin, type tagging
- Copy items back to clipboard
- Export history via Android share sheet
- Clear history
- Sensitive-looking item exclusion (password / OTP / 6-digit code heuristics)

## Build an installable APK
1. Install Android Studio on a computer.
2. Open this folder as an existing Android project.
3. Let Gradle sync and install the Android SDK Platform 34 when prompted.
4. Select **Build > Build APK(s)**.
5. The debug APK will be at `app/build/outputs/apk/debug/app-debug.apk`.
6. Transfer that APK to your Android device and approve installation when Android asks.

Minimum device: Android 10 (API 29).

## Use
Open the app to activate capture, then copy text. Tap a stack item to copy it back; long-press it to pin or unpin. Select **Share** in another Android app and choose Clipboard Stacker to save text without leaving it in the clipboard.

## Phone-only cloud build
This package includes `codemagic.yaml` and can be built from a phone with Codemagic. Read **BUILD_ON_PHONE.md** for the exact mobile-only steps.

## Anki deck export
This repository also carries a GitHub Actions workflow that exports a deck from your AnkiWeb account (words, scheduling, and review ratings) to CSV/JSON without installing anything locally. See **anki/README.md**.
