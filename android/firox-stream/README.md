# Firox Stream for Android

Stream a Firox computer to an Android phone, tablet or TV: its picture and
sound here, and this device's touch screen, mouse, keyboard and game
controllers sent back. It speaks the same protocol as `firox-stream` on the
desktop (TLS on TCP 48300; see the top of `/usr/bin/firox-stream`).

- **Finding computers:** the ones on this network that allow streaming
  (`_firox-stream._tcp`), the ones paired before, or an address.
- **Pairing:** the first time, type the 4-digit PIN the computer shows. The
  token it gives back is kept, so later connections don't ask again.
  Long-press a paired computer to forget it.
- **Picture and sound:** H.264 and Opus, decoded in hardware (MediaCodec)
  with low-latency settings; letterboxed, full screen, landscape.
- **Touch:** tap to click, long-press to right-click, drag to drag, two
  fingers to scroll.
- **Mouse:** moves the pointer directly. *Capture the mouse* (menu) sends
  relative movement for games; Ctrl+Alt+Shift+Z releases it.
- **Keyboard:** a hardware keyboard, or the on-screen one (menu → Keyboard).
- **Game controllers:** up to four, each a virtual Xbox 360 controller on
  the computer; rumble comes back to controllers that have motors.
- **Menu (⋮):** Keyboard, capture the mouse, wake its screen, unlock it with
  its password, disconnect.

Android 8.0 (API 26) or newer. No libraries beyond the Android SDK.

## Building

Needs a JDK (17 or newer), Gradle 8.9 or newer and the Android SDK
(platform 35, build tools 35):

    echo "sdk.dir=/path/to/android-sdk" > local.properties
    gradle assembleRelease      # app/build/outputs/apk/release/app-release-unsigned.apk
    gradle assembleDebug        # app/build/outputs/apk/debug/app-debug.apk (debug-signed)

Android installs only signed APKs. Sign the release one with your own key:

    zipalign -p 4 app-release-unsigned.apk aligned.apk
    apksigner sign --ks my.keystore --out firox-stream.apk aligned.apk

(`keytool -genkeypair -keystore my.keystore -alias firox -keyalg RSA -keysize 4096 -validity 10000`
makes a key.) The debug APK is signed with the SDK's debug key and installs
as it is (allow installing from unknown sources).
