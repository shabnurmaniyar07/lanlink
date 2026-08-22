package link.lan.android.data

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import android.util.Log
import androidx.documentfile.provider.DocumentFile
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import link.lan.android.net.Session
import link.lan.android.net.TransferCancelled
import link.lan.app.KnownDevice
import link.lan.app.Transfer
import link.lan.app.TransferQueue
import link.lan.app.Direction
import link.lan.app.partialName
import link.lan.app.speedOf
import link.lan.app.transferFailure
import java.io.InputStream
import java.io.OutputStream

/**
 * The transfer queue, and the one worker that drains it.
 *
 * Application-scoped on purpose: a queue owned by a screen would be emptied by
 * a rotation, and a transfer that dies because somebody turned their phone is
 * not a transfer anyone will trust.
 *
 * Every rule about ordering, progress, resume, cancellation and what a device
 * leaving means belongs to [TransferQueue] in android/logic, which is tested.
 * What lives here is the part that cannot be: Android's Storage Access
 * Framework, and moving bytes through it.
 */
object TransferCentre {

    private const val TAG = "LanLinkTransfer"

    private val queue = TransferQueue()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val _items = MutableStateFlow<List<Transfer>>(emptyList())
    val items: StateFlow<List<Transfer>> = _items.asStateFlow()

    private val _summary = MutableStateFlow("No transfers")
    val summary: StateFlow<String> = _summary.asStateFlow()

    /** Where a download's bytes actually went, so it can be opened afterwards. */
    private val landed = HashMap<Long, Uri>()

    /** The rate of each running transfer, which the queue itself has no opinion about. */
    private val speeds = HashMap<Long, Long?>()

    private var sessions: (KnownDevice) -> Session = { Session.open(it) }
    private var running = false

    fun speedOf(id: Long): Long? = speeds[id]

    fun uriOf(id: Long): Uri? = landed[id]

    // ------------------------------------------------------------- queueing

    fun enqueueDownload(
        context: Context,
        device: KnownDevice,
        shareId: String,
        folder: String,
        name: String,
        size: Long?,
    ): Transfer {
        val taken = existingNames(context)
        val item = queue.enqueueDownload(device, shareId, folder, name, size, taken)
        Log.i(TAG, "queued download ${item.name} from ${device.name}")
        publish()
        start(context)
        return item
    }

    fun enqueueUpload(
        context: Context,
        device: KnownDevice,
        shareId: String,
        folder: String,
        uri: Uri,
    ): Transfer? {
        val document = DocumentFile.fromSingleUri(context, uri) ?: return null
        val item = queue.enqueueUpload(
            device = device,
            shareId = shareId,
            remoteFolder = folder,
            name = document.name ?: "upload",
            size = document.length().takeIf { it > 0 },
            localUri = uri.toString(),
        )
        Log.i(TAG, "queued upload ${item.name} to ${device.name}")
        publish()
        start(context)
        return item
    }

    fun cancel(id: Long) {
        queue.cancel(id)
        publish()
    }

    fun retry(context: Context, id: Long) {
        queue.retry(id)
        publish()
        start(context)
    }

    fun clearFinished() {
        queue.clearFinished()
        publish()
    }

    fun deviceLost(deviceId: String) {
        val hit = queue.deviceLost(deviceId)
        if (hit > 0) {
            Log.i(TAG, "$hit transfer(s) failed because the device left")
            publish()
        }
    }

    val hasActiveWork: Boolean get() = queue.active().isNotEmpty()

    // --------------------------------------------------------------- worker

    /**
     * One at a time, because [TransferQueue] hands out one at a time. That is a
     * deliberate choice in the tested logic, not an oversight to fix here.
     */
    private fun start(context: Context) {
        if (running) return
        running = true
        val application = context.applicationContext
        scope.launch {
            try {
                while (true) {
                    val next = queue.next()
                    if (next == null) {
                        if (queue.active().isEmpty()) break
                        delay(200)
                        continue
                    }
                    publish()
                    run(application, next)
                    publish()
                }
            } finally {
                running = false
            }
        }
    }

    private fun run(context: Context, item: Transfer) {
        val device = deviceFor(context, item) ?: run {
            queue.failed(item.id, "That device is no longer paired with this phone.")
            return
        }

        val session = sessions(device)
        if (!session.isUsable) {
            queue.failed(item.id, session.connection.message)
            return
        }

        val startedAt = System.currentTimeMillis()
        session.onProgress = { moved ->
            speeds[item.id] = speedOf(moved - item.transferred, System.currentTimeMillis() - startedAt)
            queue.progress(item.id, moved)
            publish()
        }

        try {
            when (item.direction) {
                Direction.DOWNLOAD -> runDownload(context, session, item)
                Direction.UPLOAD -> runUpload(context, session, item)
            }
            queue.finished(item.id)
            Log.i(TAG, "completed ${item.name}")
        } catch (cancelled: TransferCancelled) {
            queue.failed(item.id, "cancelled")
            Log.i(TAG, "cancelled ${item.name}")
        } catch (error: Throwable) {
            val reached = queue.find(item.id)?.transferred ?: 0
            val message = transferFailure(device.name, reached, error)
            queue.failed(item.id, message, reached = reached)
            Log.w(TAG, "failed ${item.name}: ${error::class.java.simpleName}", error)
        } finally {
            session.onProgress = null
            speeds.remove(item.id)
        }
    }

    /**
     * Download into the chosen folder, through a `.lanlink-part` file.
     *
     * The partial name is the protocol's own (§25), which is also what the
     * browser hides — so an interrupted download never shows up as a file
     * somebody might open.
     */
    private fun runDownload(context: Context, session: Session, item: Transfer) {
        val tree = treeOf(context) ?: error("No download folder has been chosen.")
        val partialName = partialName(item.name)
        var partial = tree.findFile(partialName)
        var have = partial?.length() ?: 0

        if (queue.isCancelling(item.id)) throw TransferCancelled()

        val path = if (item.remoteFolder.isEmpty()) item.name else "${item.remoteFolder}/${item.name}"
        val moved = session.download(
            shareId = item.shareId,
            path = path,
            alreadyHave = have,
            onRestart = {
                partial?.delete()
                partial = null
                have = 0
            },
            onTotal = { },
            isCancelled = { queue.isCancelling(item.id) },
            sink = {
                val file = partial ?: tree.createFile("application/octet-stream", partialName)
                    ?: error("The download folder could not be written to.")
                partial = file
                openAppending(context, file.uri, append = have > 0)
            },
        )

        val finished = partial ?: error("The download produced no file.")
        DocumentsContract.renameDocument(context.contentResolver, finished.uri, item.name)
            ?.let { landed[item.id] = it }
        queue.progress(item.id, moved)
        Log.i(TAG, "saved ${item.name}")
    }

    private fun runUpload(context: Context, session: Session, item: Transfer) {
        val uri = Uri.parse(item.localUri)
        session.upload(
            shareId = item.shareId,
            folder = item.remoteFolder,
            name = item.name,
            size = item.size,
            offset = item.transferred,
            isCancelled = { queue.isCancelling(item.id) },
            source = { skip -> openSkipping(context, uri, skip) },
        )
    }

    // ---------------------------------------------------------------- files

    private fun treeOf(context: Context): DocumentFile? {
        val stored = SecureStore.open(context).downloadTree ?: return null
        return DocumentFile.fromTreeUri(context, Uri.parse(stored))
    }

    private fun existingNames(context: Context): Set<String> =
        treeOf(context)?.listFiles()?.mapNotNull { it.name }?.toSet() ?: emptySet()

    private fun openAppending(context: Context, uri: Uri, append: Boolean): OutputStream =
        context.contentResolver.openOutputStream(uri, if (append) "wa" else "w")
            ?: error("The download folder could not be written to.")

    private fun openSkipping(context: Context, uri: Uri, skip: Long): InputStream {
        val stream = context.contentResolver.openInputStream(uri)
            ?: error("That file could not be read.")
        var left = skip
        while (left > 0) {
            val jumped = stream.skip(left)
            if (jumped <= 0) break
            left -= jumped
        }
        return stream
    }

    private fun deviceFor(context: Context, item: Transfer): KnownDevice? =
        SecureStore.open(context).load().find(item.deviceId)

    private fun publish() {
        _items.value = queue.all()
        _summary.value = queue.overallSummary()
    }

    /** For tests and for a future service: swap how a session is opened. */
    fun useSessions(factory: (KnownDevice) -> Session) {
        sessions = factory
    }
}
