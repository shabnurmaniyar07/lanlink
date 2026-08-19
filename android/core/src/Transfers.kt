package link.lan.core

/**
 * §11 and §12: what a resuming transfer has to get right.
 *
 * These are the rules that quietly corrupt a file when they are wrong, so they
 * live in one place with tests rather than inline in a download loop.
 */

/** A parsed `Content-Range: bytes first-last/total`. */
data class ContentRange(val first: Long, val last: Long, val total: Long) {
    val length: Long get() = last - first + 1

    companion object {
        private val PATTERN = Regex("""bytes\s+(\d+)-(\d+)/(\d+)""", RegexOption.IGNORE_CASE)

        fun parse(header: String?): ContentRange? {
            val match = PATTERN.find(header ?: return null) ?: return null
            val (first, last, total) = match.destructured
            val start = first.toLongOrNull() ?: return null
            val end = last.toLongOrNull() ?: return null
            val size = total.toLongOrNull() ?: return null
            if (end < start || size <= end) return null
            return ContentRange(start, end, size)
        }
    }
}

/** What a client must do with a download reply it asked to resume. */
sealed class ResumeDecision {
    /** Append the body to what is already on disk, starting at [offset]. */
    data class Append(val offset: Long, val total: Long?) : ResumeDecision()

    /**
     * §11: 200 in reply to a Range request means the peer ignored it. Throw the
     * partial file away — appending would silently corrupt the result.
     */
    data class StartOver(val total: Long?) : ResumeDecision()

    /** §23: the file changed underneath us. Restart from zero. */
    object Restart : ResumeDecision()
}

object Downloads {

    /** The `Range` header for resuming at [offset], or null when starting fresh. */
    fun rangeHeader(offset: Long): String? = if (offset > 0) "bytes=$offset-" else null

    /**
     * Decide what to do with the reply, given how many bytes we already hold.
     *
     * [contentLength] is the body length the peer announced, which for a 206 is
     * the length of the *remaining* part, not of the whole file.
     */
    fun decide(
        status: Int,
        requestedOffset: Long,
        contentRange: String? = null,
        contentLength: Long? = null,
    ): ResumeDecision = when {
        status == 416 -> ResumeDecision.Restart
        status == 206 -> {
            val range = ContentRange.parse(contentRange)
            when {
                range == null -> ResumeDecision.StartOver(contentLength)
                // Honour what the peer says it sent, not what we asked for.
                range.first != requestedOffset -> ResumeDecision.Append(range.first, range.total)
                else -> ResumeDecision.Append(range.first, range.total)
            }
        }
        status == 200 -> ResumeDecision.StartOver(contentLength)
        else -> ResumeDecision.Restart
    }

    /** Total size of the file being fetched, from a whole or partial reply. */
    fun totalSize(status: Int, contentRange: String?, contentLength: Long?): Long? = when (status) {
        206 -> ContentRange.parse(contentRange)?.total ?: contentLength
        else -> contentLength
    }
}

object Uploads {

    /**
     * Where to resume a streaming upload, given what the peer reports holding.
     *
     * Never past the end of our own file: a peer claiming more bytes than exist
     * would otherwise make us send nothing and finalise a short file.
     */
    fun resumeOffset(reported: Long, localSize: Long): Long =
        reported.coerceIn(0, localSize.coerceAtLeast(0))

    /**
     * §12a: a 409 carrying X-LanLink-Received says exactly where to continue.
     * Returns null when the conflict is something else — a name already taken,
     * which asking again will not fix.
     */
    fun offsetFromConflict(status: Int, receivedHeader: String?): Long? {
        if (status != 409) return null
        return receivedHeader?.trim()?.toLongOrNull()?.takeIf { it >= 0 }
    }

    /** Bytes still to send from [offset] of a file of [size]. */
    fun remaining(size: Long, offset: Long): Long = (size - offset).coerceAtLeast(0)
}

/** Progress that survives a resume: the offset counts as already transferred. */
data class Progress(val transferred: Long, val total: Long?) {
    val fraction: Double?
        get() = total?.takeIf { it > 0 }?.let { (transferred.toDouble() / it).coerceIn(0.0, 1.0) }

    fun advanced(by: Long): Progress = copy(transferred = transferred + by)

    companion object {
        fun startingAt(offset: Long, total: Long?) = Progress(offset, total)
    }
}
