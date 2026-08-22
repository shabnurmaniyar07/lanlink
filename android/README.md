# LanLink for Android — Phase 5

Status: **the protocol core and the app logic are written and tested. The
Android application exists but has NEVER BEEN COMPILED — see the warning
below.**

> ## The Android app has not been built
>
> Everything under `app/` was written in an environment with no Android SDK and
> no access to Google's Maven, so no Gradle sync, no compile and no APK has
> happened. Treat every version number in `gradle/libs.versions.toml` as a
> proposal until a real build confirms it.
>
> What *has* been checked: every call the app makes into `core` and `logic`
> compiles against them, verified by compiling an equivalent JVM file against
> the real classes. So the seam between the app and the tested code is sound;
> the Android half is not yet evidence of anything.

```
android/
  app/          the Android application: Compose UI, view models, NsdManager
                discovery, keystore-backed storage  (NOT YET COMPILED)
  core/src/     the client: JSON, models, path rules, resume arithmetic,
                certificate pinning, invites, LanLinkClient
  core/test/    its own suite, plus the interop run against a real node
  logic/src/    what the app does with the client: the paired-device store,
                the device list, the pairing state machine, reconnection,
                browse state, and the transfer queue
  logic/test/   61 checks, plus a real pair-store-reconnect-unpair run
                and a cross-check that Python and Kotlin read one invite alike
```

Neither `core` nor `logic` imports an Android API. That is not tidiness: it is
what lets both be compiled and run against a live Python node in CI, on Linux,
with no phone and no Android SDK. The Activities and Compose screens get to be
thin because everything worth testing already happened underneath them.

## Why the core comes first

The protocol core is where an Android client goes wrong in ways nobody notices
until a file is already corrupt: appending to a partial download the peer never
resumed, trusting a certificate that changed, building a local path out of a
remote listing. Those parts touch **no Android API at all**, so they compile and
run on a plain JVM — which means they can be tested here, now, rather than after
somebody has a phone in their hand.

Everything in `core/src` imports only the Kotlin standard library and the JDK:

- `java.net.HttpURLConnection` rather than OkHttp — present on every Android
  version, no dependency to keep current.
- `javax.net.ssl.X509ExtendedTrustManager` for pinning. Not `CertificatePinner`
  and not a Network Security Config, both of which pin against a hostname; here
  the pin is per device and learned at runtime (protocol §4).
- A ~200 line JSON reader rather than a serialization library. `org.json` exists
  on Android but not on a plain JVM, and the protocol returns a dozen flat
  objects.

A test enforces this: `test_the_core_uses_no_android_api` fails the build if an
`android.`, `androidx.` or third-party import appears.

## Running the tests

```bash
pip install kotlin-jupyter-kernel     # ships the Kotlin compiler; one time
python -m pytest tests/test_android_core.py -q
```

That compiles the core, runs its 41 offline checks, then starts a real Python
node with its own certificate and drives the **Kotlin** client against it over a
pinned TLS socket: pair, browse, create a folder, upload with an interrupted
resume and a checksum, download with byte ranges, read properties, rename, copy,
delete, and unpair. 18 further checks. The run cleans up after itself and a test
asserts the node is left exactly as it was found.

If the toolchain is missing the tests skip rather than fail. In Android Studio
you would use the bundled Kotlin instead; nothing here depends on this
particular compiler.

## The pairing order, enforced rather than documented

`PairingFlow` will not send a code before the certificate has been confirmed.
Not "should not" — the state machine returns null and says why, whatever the UI
asks it to do. The order matters:

1. fetch the certificate the device is presenting
2. show its fingerprint; the person compares it with the PC's
3. only then send the code, over a socket pinned to that certificate
4. store the token and the pin together, or store neither

A code sent first goes to whatever answered the address. A token stored without
its pin is a token usable against an impostor. The desktop client had this wrong
once, which is why it is a state machine here and not a comment.

## What is deliberately not here yet

The Android application itself — Gradle project, manifest, Compose UI,
`NsdManager` discovery, SAF storage, the foreground transfer service. None of it
can be compiled or run in this container: there is no Android SDK and Maven,
Google's Maven and the Gradle distribution are all unreachable. Writing it here
would mean shipping several thousand lines of Kotlin that have never been
through a compiler, which is not worth much to anybody.

What is left for the Activities is layout and lifecycle. Everything a bug
could hide in already lives in `logic/`:

- `BrowseState` — where you are, what to draw, what the share allows, and every
  navigation refusing a path the protocol would refuse. Half-finished uploads
  are never offered as files.
- `TransferQueue` — one transfer at a time, progress that cannot walk backwards,
  a resume that uses the byte *the other end confirmed* rather than the byte
  this end sent, cancellation that closes the file rather than abandoning it,
  and a device leaving failing its transfers at once instead of one timeout at
  a time.
- `downloadName` — an incoming name is sanitised and then numbered, so nothing
  arriving over the network overwrites anything already on the phone.

- `examineInvite` — a scanned QR, a pasted `lanlink://` link or a typed
  address, judged before any socket opens. An invite carrying the full
  fingerprint is checked by the phone rather than by a person reading 64 hex
  characters off a screen; a plaintext invite is refused, because the pairing
  code and the token would both cross the network in the open.

Both sides parse invites, and a Python test now compares its own parse against
the Kotlin one field by field. Two implementations that only ever agree with
themselves are not interoperable.

The next step is to build that shell in Android Studio, where it can be
compiled, and drop `core/src` in unchanged as the networking layer.

## Using the core from the app

```kotlin
// Pairing: look at the certificate first, show the owner a fingerprint to
// compare, and only then send the code.
val invite = Invite.parse(scannedQrText)
val certificate = CertificateProbe.fetch(invite.host, invite.port)
val fingerprint = Pinning.fingerprintOf(certificate)
showToUser(Pinning.shortFingerprint(fingerprint))   // "A1B2 C3D4 E5F6 0789"
if (invite.hasPin && invite.fingerprint != fingerprint) refuse()

val client = LanLinkClient(invite.baseUrl, socketFactory = Pinning.socketFactory(certificate))
when (val outcome = client.pair(installationId, Build.MODEL, invite.code)) {
    is PairOutcome.Paired -> store(outcome.token, Pinning.toPem(certificate))
    else -> if (outcome.isRetryable) keepWaiting() else show(outcome.message)
}

// Downloading: the decision matters more than the bytes.
client.download(shareId, entry.path, offset = bytesAlreadyOnDisk) { decision, body ->
    when (decision) {
        is ResumeDecision.Append -> appendFrom(decision.offset, body)
        is ResumeDecision.StartOver -> { discardPartial(); writeFresh(body) }
        ResumeDecision.Restart -> startAgainFromZero()
    }
}
```

Store the token with `EncryptedSharedPreferences`, keep the pinned PEM beside
it, and put every call on `Dispatchers.IO` — all of this blocks.

Read `docs/protocol/v1.md` before changing anything here. The section numbers in
the Kotlin comments point at it.


## Building the application

The app module does not copy `core` and `logic` — `app/build.gradle.kts` adds
their directories as source sets, so the phone runs exactly the Kotlin the CI
suite ran and a change to either is picked up by both.

```
cd android
gradlew.bat assembleDebug
```

Then `app\build\outputs\apk\debug\app-debug.apk`.

### Versions that a real build must confirm

| | Proposed | Why it might be wrong |
|---|---|---|
| Android Gradle Plugin | 8.7.3 | must match the Android Studio in use |
| Kotlin | 2.0.21 | must equal the `kotlin-compose` plugin version |
| Gradle (wrapper) | 8.14.3 | AGP 8.7 needs 8.9+; generated here, not downloaded |
| compileSdk / targetSdk | 35 | needs SDK Platform 35 installed |
| minSdk | 26 | a choice, not a constraint |
| Compose BOM | 2024.12.01 | pins every Compose artifact |
| security-crypto | 1.1.0-alpha06 | the stable line is 1.0.0; alpha is what supports modern API levels |

### What is Android-only, and therefore untested

`app/src/main/java/link/lan/android/net/Discovery.kt` (NsdManager) and
`data/SecureStore.kt` (EncryptedSharedPreferences) are the only files that touch
an Android API in a way CI cannot reach. Both are deliberately thin: discovery
turns callbacks into a flow of `SeenDevice` and decides nothing, and the store
moves one string in and out of `DeviceStore`. Everything either of them hands
to the rest of the app has already been through the tested logic.
