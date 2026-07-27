# Build and install from an Android phone

This package is already configured for Codemagic cloud builds. You do not need a computer or Android Studio.

## What you need
- This extracted project folder
- A GitHub account
- A Codemagic account (sign in with GitHub)
- A web browser on your Android phone

## Steps
1. In your phone's Files app, extract this ZIP.
2. Open `github.com` in the browser, sign in, select **+** → **New repository**, and name it `clipboard-stacker`.
3. Upload the *contents* of the extracted `clipstacker` folder to the new repository. Important: `codemagic.yaml`, `settings.gradle`, `build.gradle`, and the `app` folder must be directly at the repository root—not inside another `clipstacker` folder.
4. Open `codemagic.io`, sign in using the same GitHub account, and choose **Add application**.
5. Select the `clipboard-stacker` repository and choose **Android**.
6. Choose the `Clipboard Stacker — installable debug APK` workflow, then choose **Start new build**.
7. When the build succeeds, open **Artifacts** and download `app-debug.apk` to your phone.
8. Open the downloaded APK. If Android blocks it, allow your browser or file manager to **Install unknown apps** when prompted, then install.

## Notes
- This builds a debug APK for personal installation. It does not need Google Play signing.
- The app captures copied text while its screen is open and saves text shared to it through Android's Share sheet.
- Do not upload this project to a public GitHub repository if you later add keys, private services, or personal data.
