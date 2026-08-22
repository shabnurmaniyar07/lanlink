package link.lan.check

// Mirrors exactly what the Android application calls into android/core and
// android/logic — every method, with the same argument shapes.
//
// The app itself cannot be compiled in CI: that needs the Android SDK and
// Google's Maven. This file can, because it imports no androidx. So the seam
// between the app and the tested code stays under test even though the app is
// not, and a rename in the logic layer fails here rather than on a phone.
//
// It is never compiled into the APK: app/build.gradle.kts lists src/main/java,
// ../core/src and ../logic/src as its sources, and this directory is not one.

import link.lan.app.DeviceRow
import link.lan.app.DeviceStanding
import link.lan.app.DeviceStore
import link.lan.app.InviteCheck
import link.lan.app.KnownDevice
import link.lan.app.PairingFlow
import link.lan.app.PairingStep
import link.lan.app.Presented
import link.lan.app.Reconnection
import link.lan.app.SeenDevice
import link.lan.app.examineInvite
import link.lan.app.inviteFor
import link.lan.app.mergeDevices
import link.lan.app.reconnect
import link.lan.core.DEFAULT_PORT
import link.lan.core.SERVICE_TYPE
import link.lan.app.BrowseState
import link.lan.app.Connection
import link.lan.app.Crumb
import link.lan.app.Direction
import link.lan.app.Sorting
import link.lan.app.Standing
import link.lan.app.Transfer
import link.lan.app.TransferQueue
import link.lan.app.TransferState
import link.lan.app.bytes
import link.lan.app.connectTo
import link.lan.app.describeRemaining
import link.lan.app.describeSpeed
import link.lan.app.failureOf
import link.lan.app.partialName
import link.lan.app.secondsRemaining
import link.lan.app.speedOf
import link.lan.app.transferFailure
import link.lan.core.Entry
import link.lan.core.Pinning
import link.lan.core.ResumeDecision
import link.lan.core.Share
import link.lan.core.UploadConflict
import java.io.InputStream
import java.io.OutputStream

fun secureStoreCalls(json: String?): String {
    val store: DeviceStore = DeviceStore.fromJson(json)
    val device: KnownDevice? = store.find("id")
    store.seen("id", "h", DEFAULT_PORT, 1L)
    device?.let { store.remember(it) }
    store.forget("id")
    val all: List<KnownDevice> = store.all()
    check(all.size >= 0)
    return store.asJson()
}

fun discoveryCalls(): List<SeenDevice> {
    check(SERVICE_TYPE.isNotEmpty())
    return listOf(SeenDevice(id = "i", name = "n", host = "h", port = DEFAULT_PORT, fingerprint = "f"))
}

fun devicesViewModelCalls(store: DeviceStore, seen: List<SeenDevice>): String {
    val rows: List<DeviceRow> = mergeDevices(store.all(), seen)
    val row: DeviceRow = rows.firstOrNull() ?: return "none"
    val standing: DeviceStanding = row.standing
    val label = "${row.id} ${row.name} ${row.host} ${row.port} ${row.address} ${row.isPaired} ${row.canOpen} $standing"
    val known: KnownDevice = row.known ?: return label

    return when (val outcome = reconnect(known)) {
        is Reconnection.Ready -> "${outcome.client.shares().size} ${outcome.client.unpair("client")}"
        is Reconnection.Changed -> outcome.message
        is Reconnection.Unreachable -> outcome.reason
    }
}

fun pairingViewModelCalls(text: String, store: DeviceStore, allowInsecure: Boolean): String {
    return when (val checked = examineInvite(text, store, allowInsecure)) {
        is InviteCheck.Rejected -> checked.reason
        is InviteCheck.AlreadyPaired -> checked.message
        is InviteCheck.Ready -> {
            val flow = PairingFlow(
                host = checked.invite.host,
                port = checked.invite.port,
                clientId = "client",
                clientName = "Phone",
            )
            val shown: Presented? = flow.probeCertificate()
            val agreed: Boolean = flow.confirmAgainst(checked.pinnedFingerprint)
            flow.confirmFingerprint(true)
            val paired: KnownDevice? = flow.submitCode(checked.codeFromInvite)
            val step: PairingStep = flow.step
            flow.cancel()
            "${shown?.short} $agreed ${paired?.name} $step ${flow.problem} ${checked.label} ${checked.needsCodeTyped}"
        }
    }
}

fun mainActivityCalls(row: DeviceRow): String =
    inviteFor(host = row.host, port = row.port, name = row.name)

// ------------------------------------------------ phase 5D: the new surfaces


fun sessionCalls(client: link.lan.core.LanLinkClient, device: KnownDevice): String {
    val connection: Connection = connectTo(device)
    val standing: Standing = connection.standing
    check(connection.isUsable || !connection.isUsable)
    check(connection.worthRetrying || !connection.worthRetrying)
    val shares: List<Share> = connection.shares

    val entries: List<Entry> = client.list("share", "path")

    val moved: Long = client.download("share", "path", 10L) { decision: ResumeDecision, body: InputStream ->
        when (decision) {
            is ResumeDecision.Append -> decision.offset + (decision.total ?: 0)
            is ResumeDecision.StartOver -> decision.total ?: 0
            ResumeDecision.Restart -> 0L
        }.also { body.read(ByteArray(8)) }
    }

    val result = try {
        client.upload("share", "folder", "name", offset = 0, finalize = true) { out: OutputStream ->
            out.write(ByteArray(8))
        }
    } catch (conflict: UploadConflict) {
        check(conflict.resumeFrom != null || !conflict.canResume)
        null
    }

    return "$standing ${shares.size} ${entries.size} $moved ${result?.received} " +
        failureOf(device.name, RuntimeException("x")).message +
        transferFailure(device.name, 10, RuntimeException("x"))
}

fun transferCentreCalls(device: KnownDevice): String {
    val queue = TransferQueue()
    val download: Transfer = queue.enqueueDownload(device, "s", "folder", "a.txt", 10L, setOf("taken"))
    val upload: Transfer = queue.enqueueUpload(device, "s", "folder", "b.txt", 10L, "content://x")
    queue.next()
    queue.progress(download.id, 5)
    queue.finished(download.id)
    queue.failed(upload.id, "why", reached = 2)
    queue.retry(upload.id)
    queue.cancel(upload.id)
    queue.clearFinished()
    queue.deviceLost(device.id)

    val active: List<Transfer> = queue.active()
    val all: List<Transfer> = queue.all()
    val found: Transfer? = queue.find(download.id)
    val cancelling: Boolean = queue.isCancelling(download.id)
    val state: TransferState = download.state
    val direction: Direction = download.direction

    return "${active.size} ${all.size} ${found?.name} $cancelling $state $direction " +
        "${download.fraction} ${download.isActive} ${download.isFinished} ${download.canRetry} " +
        "${download.canResume} ${download.summary} ${queue.overallSummary()} ${partialName("a.txt")} " +
        "${bytes(1024)} ${describeSpeed(speedOf(100, 1000))} " +
        describeRemaining(secondsRemaining(100, 10, 5))
}

fun browseCalls(share: Share, entry: Entry): String {
    var browse = BrowseState()
    browse = browse.openShare(share)
    browse = browse.loaded(listOf(entry))
    browse = browse.into(entry)
    browse = browse.up() ?: browse
    val crumbs: List<Crumb> = browse.crumbs
    browse = browse.jumpTo(crumbs.first())
    browse = browse.searching("q")
    browse = browse.sortedBy(Sorting.NEWEST)
    browse = browse.failed("nope")
    browse = browse.copy(loading = true)

    return "${browse.title} ${browse.visible.size} ${browse.isEmpty} ${browse.emptyMessage} " +
        "${browse.canWrite} ${browse.canDelete} ${browse.atRoot} ${browse.backLeavesTheScreen} " +
        "${browse.path} ${browse.query} ${browse.sorting} ${browse.problem} ${browse.loading} " +
        "${browse.share?.id} ${browse.entries.size} ${crumbs.first().label}"
}

fun infoCalls(device: KnownDevice): String = Pinning.shortFingerprint(device.fingerprint)

fun relocationCalls(device: KnownDevice): String =
    when (val outcome = link.lan.app.relocate(device, "192.168.1.16", 8765)) {
        is link.lan.app.Relocation.Moved -> outcome.device.address
        is link.lan.app.Relocation.WrongDevice -> outcome.message
        is link.lan.app.Relocation.Unreachable -> outcome.message
    }
