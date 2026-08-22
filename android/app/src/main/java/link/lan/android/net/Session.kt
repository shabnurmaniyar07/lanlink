package link.lan.android.net

import android.util.Log
import link.lan.app.Connection
import link.lan.app.KnownDevice
import link.lan.app.Standing
import link.lan.app.connectTo
import link.lan.app.failureOf
import link.lan.core.Entry
import link.lan.core.LanLinkClient
import link.lan.core.ResumeDecision
import link.lan.core.Share
import link.lan.core.UploadConflict
import java.io.InputStream
import java.io.OutputStream

/**
 * One connected device, for as long as it stays connected.
 *
 * Every network call in the app goes through here, and every one of them is
 * blocking — callers are on Dispatchers.IO. The class adds logging and nothing
 * else: the connection rules are [connectTo], the resume arithmetic is
 * `Downloads`/`Uploads` in the core, and the path rules are `Paths`. Adding a
 * second opinion about any of those here is how the two implementations drift.
 */
class Session private constructor(
    val device: KnownDevice,
    val connection: Connection,
) {
    val client: LanLinkClient? get() = connection.client
    val shares: List<Share> get() = connection.shares
    val standing: Standing get() = connection.standing
    val isUsable: Boolean get() = connection.isUsable

    /** §9. The listing of one folder inside one share. */
    fun list(shareId: String, path: String): List<Entry> {
        val client = client ?: error("not connected")
        Log.i(BROWSE, "listing ${path.ifEmpty { "(root)" }} in share $shareId")
        val entries = client.list(shareId, path)
        Log.i(BROWSE, "listed ${entries.size} entr(ies)")
        return entries
    }

    /**
     * §11. Download, resuming from [alreadyHave], writing through [sink].
     *
     * The decision about what a reply *means* belongs to the core: a 200 in
     * answer to a Range request means the peer ignored it, and appending to the
     * partial file would corrupt it silently. [onRestart] is called when that
     * happens so the caller can throw away what it holds.
     */
    fun download(
        shareId: String,
        path: String,
        alreadyHave: Long,
        onRestart: () -> Unit,
        onTotal: (Long?) -> Unit,
        isCancelled: () -> Boolean,
        sink: () -> OutputStream,
    ): Long {
        val client = client ?: error("not connected")
        Log.i(TRANSFER, "download started: $path (have $alreadyHave bytes)")

        return client.download(shareId, path, alreadyHave) { decision, body ->
            var written = when (decision) {
                is ResumeDecision.Append -> {
                    onTotal(decision.total)
                    alreadyHave
                }

                is ResumeDecision.StartOver -> {
                    Log.i(TRANSFER, "peer ignored the range; starting over")
                    onRestart()
                    onTotal(decision.total)
                    0L
                }

                ResumeDecision.Restart -> {
                    Log.i(TRANSFER, "the file changed; starting over")
                    onRestart()
                    onTotal(null)
                    0L
                }
            }

            sink().use { out ->
                val buffer = ByteArray(CHUNK)
                while (true) {
                    if (isCancelled()) {
                        Log.i(TRANSFER, "download cancelled at $written bytes")
                        throw TransferCancelled()
                    }
                    val read = body.read(buffer)
                    if (read <= 0) break
                    out.write(buffer, 0, read)
                    written += read
                    reportEvery(written) { moved -> onProgress?.invoke(moved) }
                }
                out.flush()
            }
            Log.i(TRANSFER, "download finished: $written bytes")
            written
        }
    }

    /**
     * §12a. Upload, streaming from [source] so a large file is never held in
     * memory, and resuming from the offset a 409 hands back.
     */
    fun upload(
        shareId: String,
        folder: String,
        name: String,
        size: Long?,
        offset: Long,
        isCancelled: () -> Boolean,
        source: (Long) -> InputStream,
    ): Long {
        val client = client ?: error("not connected")
        Log.i(TRANSFER, "upload started: $name (from $offset)")
        var start = offset

        while (true) {
            try {
                var sent = start
                client.upload(shareId, folder, name, offset = start, finalize = true) { out ->
                    source(start).use { input ->
                        val buffer = ByteArray(CHUNK)
                        while (true) {
                            if (isCancelled()) {
                                Log.i(TRANSFER, "upload cancelled at $sent bytes")
                                throw TransferCancelled()
                            }
                            val read = input.read(buffer)
                            if (read <= 0) break
                            out.write(buffer, 0, read)
                            sent += read
                            reportEvery(sent) { moved -> onProgress?.invoke(moved) }
                        }
                        out.flush()
                    }
                }
                Log.i(TRANSFER, "upload finished: $sent bytes")
                return sent
            } catch (conflict: UploadConflict) {
                // §12a: a 409 carrying an offset is an instruction, not a failure.
                val resume = conflict.resumeFrom
                if (resume == null || resume == start) {
                    Log.w(TRANSFER, "upload refused: ${conflict.status}")
                    throw conflict
                }
                Log.i(TRANSFER, "peer already holds $resume bytes; continuing from there")
                start = resume
            }
        }
    }

    /** Set by the caller before a transfer; called with the running total. */
    var onProgress: ((Long) -> Unit)? = null

    private var lastReport = 0L

    private inline fun reportEvery(moved: Long, report: (Long) -> Unit) {
        if (moved - lastReport >= REPORT_EVERY) {
            lastReport = moved
            report(moved)
        }
    }

    companion object {
        const val TAG = "LanLink"
        const val BROWSE = "LanLinkBrowse"
        const val TRANSFER = "LanLinkTransfer"

        private const val CHUNK = 64 * 1024

        /** Report often enough to look alive, rarely enough not to flood the UI. */
        private const val REPORT_EVERY = 256 * 1024

        /**
         * Connect: pin, then token, then shares. Failures come back as a
         * [Session] whose standing says what went wrong — never as an exception
         * for a caller to guess at.
         */
        fun open(device: KnownDevice): Session {
            Log.i(TAG, "connecting to ${device.address}")
            val connection = try {
                connectTo(device)
            } catch (error: Throwable) {
                Log.w(TAG, "connection to ${device.address} failed", error)
                failureOf(device.name, error)
            }
            when (connection.standing) {
                Standing.CONNECTED -> Log.i(
                    TAG,
                    "TLS certificate verified, authenticated, ${connection.shares.size} share(s)",
                )

                Standing.IMPOSTOR -> Log.w(TAG, "REFUSED: certificate does not match the stored pin")
                else -> Log.i(TAG, "not connected: ${connection.standing}")
            }
            return Session(device, connection)
        }
    }
}

/** Thrown to unwind a transfer the person cancelled. Not a failure. */
class TransferCancelled : RuntimeException("cancelled")
