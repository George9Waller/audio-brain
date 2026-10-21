# Releasing the Android app

How the app gets from this repo onto the phone and the DAPs: a signed APK on a GitHub Release, installed and updated
with Obtainium. No Play Store.

- **One-time setup** (steps 1–4): make the signing key and give a copy to GitHub.
- **Every release** (step 5): push a tag.
- **Each new device** (step 6): install it once, and Obtainium handles updates from then on.

## How it works

```
your Mac                        GitHub                                  devices
~/keys/audiobrain-release.jks   repo secrets (encrypted)                Obtainium
        │  base64, once  ──►    ANDROID_KEYSTORE_BASE64 + passwords
                                        │
git push origin v0.2.0  ──►     android-release.yaml:
                                  decode keystore to the runner's temp dir
                                  ./gradlew :app:assembleRelease (signed)
                                  GitHub Release v0.2.0 + audio-brain-v0.2.0.apk  ──►  sees the new tag, updates
```

- **The keystore** is the app's identity. Android only installs an update if it's signed with the same key as the
  installed app. Lose the key and every device has to uninstall (losing its local data) to get the next version.
- **CI never sees your file.** It gets a base64 copy from the repo secrets, writes it to the runner's temp directory,
  signs with it, and the runner is thrown away afterwards.
- **[app/build.gradle.kts](app/build.gradle.kts)** looks for the key in env vars first (`ANDROID_KEYSTORE_FILE`,
  `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`; this is how CI passes it), then in
  `android/keystore.properties` (gitignored, for local builds). Without either, a local release build uses the debug
  key so you can still try R8 builds, but in CI the build fails instead.
- **The version comes from the tag:** `v1.2.3` gives versionName `1.2.3` (Obtainium compares this with the tag) and
  versionCode `1002003` (Android only installs a higher one). Tags must be `vMAJOR.MINOR.PATCH` and always go up.
- **Debug builds** are `com.georgewaller.audiobrain.debug` ("Audio Brain dev"), so a dev build from Android Studio sits
  alongside the release and never clashes with it.

## 1. Make the keystore (once)

On the Mac, outside the repo:

```sh
mkdir -p ~/keys
"/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/keytool" -genkeypair -v \
  -keystore ~/keys/audiobrain-release.jks -alias audiobrain \
  -keyalg RSA -keysize 4096 -validity 10000
```

- It asks for a **password**: make it strong and put it in your password manager straight away. The keystore is PKCS12,
  so the key uses the same password and you won't be asked for a second one.
- The name and organisation questions don't matter for sideloading. Your name is enough; press Enter through the rest
  and answer `yes`.
- `-validity 10000` is about 27 years.

Check it:

```sh
"/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/keytool" -list -v -keystore ~/keys/audiobrain-release.jks
```

Note the SHA-256 fingerprint shown; it identifies this key.

## 2. Back it up (once; don't skip this)

Store **both the `.jks` file and the password** somewhere off the Mac: for example, the file as an attachment on the
password-manager entry. `*.jks` is gitignored in `android/`, but the file shouldn't be in the repo folder at all.

## 3. Give GitHub a copy (once)

From the repo root, with the `gh` CLI logged in:

```sh
base64 -i ~/keys/audiobrain-release.jks | gh secret set ANDROID_KEYSTORE_BASE64
gh secret set ANDROID_KEYSTORE_PASSWORD     # paste the password when prompted
gh secret set ANDROID_KEY_ALIAS --body audiobrain
gh secret set ANDROID_KEY_PASSWORD          # the same password again
gh secret list                              # all four should be listed
```

The same is possible in the browser: repo › Settings › Secrets and variables › Actions. Secrets can't be read back
once set, only replaced.

## 4. Local signed builds (optional)

Only needed to build a properly signed release on the Mac. Create `android/keystore.properties` (gitignored):

```properties
storeFile=/Users/georgewaller/keys/audiobrain-release.jks
storePassword=...
keyAlias=audiobrain
keyPassword=...
```

```sh
cd android && JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ./gradlew :app:assembleRelease
# → app/build/outputs/apk/release/app-release.apk
```

## 5. Make a release (every time)

```sh
git checkout main && git pull
git tag v0.2.0                      # higher than the last one: git tag --list 'v*' --sort=-v:refname | head -1
git push origin main v0.2.0
```

Then:

- Watch it: `gh run watch` (or the repo's Actions tab, "Android release"). It takes a few minutes.
- Check the Release has the APK: `gh release view v0.2.0`.
- Devices pick it up the next time Obtainium checks, or tap the app in Obtainium to update now.

A tag can be redone if the build failed: `git tag -d v0.2.0 && git push origin :v0.2.0`, fix, then tag again. Don't
reuse a tag that was already installed on a device; use the next number.

## 6. A new device (once per device)

1. **Tailscale** (the F-Droid build has no Google dependency), logged in to the same tailnet. Check it reaches the
   server: open `http://ubuntuserver.tail1604aa.ts.net:30800/api/v1/health` in a browser; a 401 means it got there.
2. **Uninstall any older Audio Brain.** Builds before 21 Oct were debug-signed under the release's package name, and
   Android won't replace them (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`). Check Settings › Outbox says "all sent" first,
   or unsent verdicts are lost.
3. **Obtainium:** Add app › `https://github.com/George9Waller/audio-brain` › Add, then Install. (Or, over USB:
   `adb install audio-brain-v0.2.0.apk` from the Release; Obtainium can track it afterwards.)
4. **Connect it to the server.** Print the connect link from the API pod (the cluster's token lives there):
   ```sh
   kubectl exec deploy/audio-brain-api -c api -- python manage.py api_token --url http://ubuntuserver.tail1604aa.ts.net:30800
   ```
   Get the `audiobrain://connect?...` link onto the device (a note, an email or a chat with yourself), **copy** it, and
   in the app's Settings tap **Paste connect link** (or paste it into Address). Most apps won't make an `audiobrain://`
   link tappable, so pasting is the reliable way. Over USB, the `adb shell am start` line it prints does the same.
   Settings should then say "Connected".
5. On the DAPs, allow **notification access** in Settings, so it can see what Tidal or UAPP is playing.

## When something goes wrong

| Problem | Fix |
|---|---|
| CI: `No release keystore: set ANDROID_KEYSTORE_FILE` | The env var didn't reach Gradle; check the workflow wasn't edited. |
| CI: `Keystore was tampered with, or password was incorrect` | `ANDROID_KEYSTORE_PASSWORD` is wrong; set it again. |
| CI: `Failed to read key ... from store` or an empty or invalid keystore | `ANDROID_KEYSTORE_BASE64` is missing or truncated; redo the `base64 ... \| gh secret set` line. Also check `ANDROID_KEY_ALIAS` is `audiobrain`. |
| Device: `App not installed` / `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | A build signed with another key is installed: uninstall it first (step 6.2). |
| Device: update refused as a downgrade | The tag's version isn't higher than the installed one; tag the next number. |
| Obtainium sees no update | Check the Release exists and has the `.apk` attached, and that the tag is higher. |
| App says it can't reach the server | Tailscale off on the device, or the wrong address; the emulator needs `http://100.122.82.67:30800` (no MagicDNS). |

**Lost the keystore or its password:** there's no recovery. Make a new one (steps 1–3), release, and uninstall then
reinstall on every device. Settings and cached data on the device are lost; everything on the server is unaffected.
