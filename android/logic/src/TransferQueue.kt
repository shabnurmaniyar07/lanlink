package link.lan.app

import link.lan.core.Paths
import link.lan.core.Progress

enum class Direction { DOWNLOAD, UPLOAD }

enum class TransferState { WAITING, RUNNING, DONE, FAILED, CANCELLED }

/**
 * One file on its way somewhere, and everything the screen needs to say so.
 *
 * [transferred] is what has actually landed, not what has been read: a phone
 * that loses Wi-Fi mid-file must resume from the byte the other end confirmed,
 * never from the byte this end hoped for.
 */
data class Transfer(
    val id: Long,
    val direction: Direction,
    val deviceId: String,
    val deviceName: String,
    val shareId: String,
    val remoteFolder: String,
    val name: String,
    val size: Long?,
    val state: TransferState = TransferState.WAITING,
    val transferred: Long = 0,
    val problem: String? = null,
    val localUri: String = "",
) {
    val progress: Progress get() = Progress(transferred, size)

    val fraction: Float?
        get() = size?.takeIf { it > 0 }?.let { (transferred.toDouble() / it).toFloat().coerceIn(0f, 1f) }

    val isFinished: Boolean
        get() = state == TransferState.DONE || state == TransferState.FAILED || state == TransferState.CANCELLED

    val isActive: Boolean get() = state == TransferState.WAITING || state == TransferState.RUNNING

    /** Only an interrupted transfer with something already moved is worth resuming. */
    val canResume: Boolean
        get() = state == TransferState.FAILED && transferred > 0

    val canRetry: Boolean get() = state == TransferState.FAILED || state == TransferState.CANCELLED

    val summary: String
        get() = when (state) {
            TransferState.WAITING -> "Waiting"
            TransferState.RUNNING -> describeProgress()
            TransferState.DONE -> "Finished"
            TransferState.FAILED -> problem ?: "Failed"
            TransferState.CANCELLED -> "Cancelled"
        }

    private fun describeProgress(): String {
        val total = size
        return if (total == null || total <= 0) {
            "${bytes(transferred)} so far"
        } else {
            "${bytes(transferred)} of ${bytes(total)}"
        }
    }
}

fun bytes(count: Long): String {
    if (count < 1024) return "$count B"
    var value = count.toDouble()
    for (unit in listOf("KB", "MB", "GB")) {
        value /= 1024
        if (value < 1024 || unit == "GB") {
            return String.format("%.1f %s", value, unit)
        }
    }
    return "$count B"
}

/**
 * The transfer list: what is queued, what is running, what went wrong.
 *
 * Deliberately not a thread pool. One transfer at a time per device keeps the
 * ordering obvious, keeps a phone's radio from thrashing, and means a failure
 * has one cause rather than four. The Android layer runs [next] on a worker and
 * reports back through [progress], [finished] and [failed].
 */
class TransferQueue(private val maxConcurrent: Int = 1) {
    private val items = LinkedHashMap<Long, Transfer>()
    private var nextId = 1L
    private val cancelled = HashSet<Long>()

    fun all(): List<Transfer> = items.values.toList()

    fun active(): List<Transfer> = items.values.filter { it.isActive }

    fun find(id: Long): Transfer? = items[id]

    val busy: Boolean get() = items.values.any { it.state == TransferState.RUNNING }

    /**
     * Queue a download. The name is sanitised here rather than at the point it
     * touches storage, so nothing downstream ever sees the peer's version.
     */
    fun enqueueDownload(
        device: KnownDevice,
        shareId: String,
        remoteFolder: String,
        name: String,
        size: Long?,
        taken: Set<String> = emptySet(),
    ): Transfer = add(
        Transfer(
            id = nextId++,
            direction = Direction.DOWNLOAD,
            deviceId = device.id,
            deviceName = device.name,
            shareId = shareId,
            remoteFolder = remoteFolder,
            name = downloadName(name, taken),
            size = size,
        )
    )

    /** Queue an upload. [localUri] is whatever the phone needs to reopen the file. */
    fun enqueueUpload(
        device: KnownDevice,
        shareId: String,
        remoteFolder: String,
        name: String,
        size: Long?,
        localUri: String,
    ): Transfer = add(
        Transfer(
            id = nextId++,
            direction = Direction.UPLOAD,
            deviceId = device.id,
            deviceName = device.name,
            shareId = shareId,
            remoteFolder = remoteFolder,
            name = Paths.sanitiseForPeer(name),
            size = size,
            localUri = localUri,
        )
    )

    /** The next transfer to run, or null while the queue is full or empty. */
    fun next(): Transfer? {
        if (items.values.count { it.state == TransferState.RUNNING } >= maxConcurrent) return null
        val waiting = items.values.firstOrNull { it.state == TransferState.WAITING } ?: return null
        return update(waiting.copy(state = TransferState.RUNNING, problem = null))
    }

    fun progress(id: Long, transferred: Long): Transfer? {
        val item = items[id] ?: return null
        // Never let a progress report walk backwards: a resumed transfer reports
        // absolute positions and a fresh one reports from zero.
        val moved = maxOf(item.transferred, transferred)
        return update(item.copy(transferred = moved))
    }

    fun finished(id: Long): Transfer? {
        val item = items[id] ?: return null
        return update(
            item.copy(
                state = TransferState.DONE,
                transferred = item.size ?: item.transferred,
                problem = null,
            )
        )
    }

    /** [reached] is what the other end confirmed, which is what a resume must use. */
    fun failed(id: Long, reason: String, reached: Long? = null): Transfer? {
        val item = items[id] ?: return null
        if (id in cancelled) {
            cancelled.remove(id)
            return update(item.copy(state = TransferState.CANCELLED, problem = null))
        }
        return update(
            item.copy(
                state = TransferState.FAILED,
                problem = reason.ifBlank { "The transfer stopped." },
                transferred = reached ?: item.transferred,
            )
        )
    }

    /**
     * Ask a transfer to stop. A running one is marked when its worker reports
     * back, so the file is closed properly rather than abandoned mid-write.
     */
    fun cancel(id: Long): Transfer? {
        val item = items[id] ?: return null
        return when (item.state) {
            TransferState.WAITING -> update(item.copy(state = TransferState.CANCELLED))
            TransferState.RUNNING -> {
                cancelled.add(id)
                item
            }

            else -> item
        }
    }

    fun isCancelling(id: Long): Boolean = id in cancelled

    /** Queue it again, keeping what already moved when that is worth something. */
    fun retry(id: Long): Transfer? {
        val item = items[id] ?: return null
        if (!item.canRetry) return item
        val keep = if (item.canResume) item.transferred else 0L
        return update(item.copy(state = TransferState.WAITING, transferred = keep, problem = null))
    }

    /** Clear the finished rows. Anything still moving is left alone. */
    fun clearFinished(): Int {
        val done = items.values.filter { it.isFinished }.map { it.id }
        done.forEach { items.remove(it) }
        return done.size
    }

    /**
     * Everything queued for a device that has just gone away.
     *
     * Failing them immediately beats letting each one time out in turn, and it
     * tells the person the truth: the device left, the files did not arrive.
     */
    fun deviceLost(deviceId: String, reason: String = "That device left the network."): Int {
        val hit = items.values.filter { it.deviceId == deviceId && it.isActive }
        hit.forEach { update(it.copy(state = TransferState.FAILED, problem = reason)) }
        return hit.size
    }

    /** One line for a notification: what is happening across the whole queue. */
    fun overallSummary(): String {
        val running = items.values.count { it.state == TransferState.RUNNING }
        val waiting = items.values.count { it.state == TransferState.WAITING }
        val failed = items.values.count { it.state == TransferState.FAILED }
        return when {
            running == 0 && waiting == 0 && failed > 0 -> "$failed transfer(s) failed"
            running == 0 && waiting == 0 -> "No transfers"
            waiting == 0 -> "$running in progress"
            else -> "$running in progress, $waiting waiting"
        }
    }

    private fun add(transfer: Transfer): Transfer {
        items[transfer.id] = transfer
        return transfer
    }

    private fun update(transfer: Transfer): Transfer {
        items[transfer.id] = transfer
        return transfer
    }
}
