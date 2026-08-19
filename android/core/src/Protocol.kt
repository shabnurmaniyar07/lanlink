package link.lan.core

/**
 * The v1 wire model, transcribed from docs/protocol/v1.md.
 *
 * Nothing here touches an Android API, so it builds and is tested on a plain
 * JVM. Unknown JSON members are ignored on purpose (§32): a newer peer adding a
 * field must not break an older client.
 */

const val PROTOCOL_API = "v1"
const val TOKEN_HEADER = "X-LanLink-Token"
const val RECEIVED_HEADER = "X-LanLink-Received"
const val PART_SUFFIX = ".lanlink-part"
const val DEFAULT_PORT = 8765
const val SERVICE_TYPE = "_lanlink._tcp"

/** §2. Public identity of a node. Never carries a token or a key. */
data class Device(
    val id: String,
    val name: String,
    val hostname: String = "",
    val platform: String = "",
    val version: String = "",
    val fingerprint: String = "",
) {
    companion object {
        fun from(values: Map<String, Any?>) = Device(
            id = values.string("id"),
            name = values.string("name"),
            hostname = values.string("hostname"),
            platform = values.string("platform"),
            version = values.string("version"),
            fingerprint = values.string("fingerprint").lowercase(),
        )
    }
}

/** §8. One folder the owner explicitly shared. The real path is never sent. */
data class Share(
    val id: String,
    val name: String,
    val permissions: String,
    val available: Boolean,
) {
    val canRead: Boolean get() = permissions.contains('r')
    val canWrite: Boolean get() = permissions.contains('w')
    val canDelete: Boolean get() = permissions.contains('d')

    companion object {
        fun from(values: Map<String, Any?>) = Share(
            id = values.string("id"),
            name = values.string("name"),
            permissions = values.string("permissions"),
            available = values.boolean("available", true),
        )
    }
}

/** §9. One row of a folder listing. */
data class Entry(
    val name: String,
    val path: String,
    val isFolder: Boolean,
    val size: Long?,
    val modifiedAt: Double?,
) {
    val isFile: Boolean get() = !isFolder

    companion object {
        fun from(values: Map<String, Any?>) = Entry(
            name = values.string("name"),
            path = values.string("path"),
            isFolder = values.string("kind") == "folder",
            size = values.longOrNull("size"),
            modifiedAt = values.doubleOrNull("modified_at"),
        )
    }
}

/** §10. */
data class Properties(
    val name: String,
    val path: String,
    val isFolder: Boolean,
    val size: Long?,
    val modifiedAt: Double?,
    val createdAt: Double?,
    val extension: String,
    val readOnly: Boolean,
    val share: String,
    val sharePermissions: String,
    val folderCount: Long?,
    val fileCount: Long?,
) {
    companion object {
        fun from(values: Map<String, Any?>): Properties {
            val counts = values.child("item_count")
            return Properties(
                name = values.string("name"),
                path = values.string("path"),
                isFolder = values.string("kind") == "folder",
                size = values.longOrNull("size"),
                modifiedAt = values.doubleOrNull("modified_at"),
                createdAt = values.doubleOrNull("created_at"),
                extension = values.string("extension"),
                readOnly = values.boolean("read_only"),
                share = values.string("share"),
                sharePermissions = values.string("share_permissions"),
                folderCount = counts.longOrNull("folders"),
                fileCount = counts.longOrNull("files"),
            )
        }
    }
}

/** §12c. How much of an interrupted upload the other device still holds. */
data class PartialStatus(val received: Long, val complete: Boolean, val size: Long?) {
    companion object {
        fun from(values: Map<String, Any?>) = PartialStatus(
            received = values.long("received"),
            complete = values.boolean("complete"),
            size = values.longOrNull("size"),
        )
    }
}

/** §12a. The reply to a streaming upload. */
data class UploadResult(val path: String, val received: Long, val complete: Boolean, val bytes: Long?) {
    companion object {
        fun from(values: Map<String, Any?>) = UploadResult(
            path = values.string("path"),
            received = values.long("received", values.long("bytes")),
            complete = values.boolean("complete", true),
            bytes = values.longOrNull("bytes"),
        )
    }
}

/** §5. What the other device answered when we tried to pair. */
sealed class PairOutcome {
    data class Paired(val token: String, val device: Device) : PairOutcome()

    /** The other device is not in pairing mode yet. §5 says this one may be retried. */
    object NotArmed : PairOutcome()
    object WrongCode : PairOutcome()
    object Declined : PairOutcome()
    object RateLimited : PairOutcome()
    object LockedOut : PairOutcome()
    data class Failed(val status: Int, val detail: String) : PairOutcome()

    val isRetryable: Boolean get() = this is NotArmed

    val message: String
        get() = when (this) {
            is Paired -> "Paired with ${device.name}."
            NotArmed -> "That device is not in pairing mode yet, or its code expired."
            WrongCode -> "That pairing code is not correct."
            Declined -> "The other device declined the request."
            RateLimited -> "Too many attempts. Wait a moment and try again."
            LockedOut -> "Too many wrong codes; pairing was switched off on that device."
            is Failed -> detail.ifBlank { "Pairing failed ($status)." }
        }
}

/** Any non-2xx reply. §26: show `detail`, never parse it — branch on the status. */
class ProtocolError(val status: Int, val detail: String) : RuntimeException(
    if (detail.isBlank()) "The other device answered $status." else detail
) {
    val isUnauthorised: Boolean get() = status == 401
    val isForbidden: Boolean get() = status == 403
    val isMissing: Boolean get() = status == 404
    val isConflict: Boolean get() = status == 409
    val isTooLarge: Boolean get() = status == 413

    /** §23. Whether trying the same request again could ever succeed. */
    val isWorthRetrying: Boolean get() = status >= 500 || status == 408 || status == 429

    companion object {
        /** §26: a JSON body carries `detail`; a bare body is used as-is. */
        fun of(status: Int, body: String): ProtocolError {
            val detail = try {
                val values = Json.parseObject(body)
                when (val raw = values["detail"]) {
                    is String -> raw
                    is List<*> -> raw.filterIsInstance<Map<*, *>>()
                        .joinToString("; ") { it["msg"]?.toString() ?: "invalid request" }
                    else -> body
                }
            } catch (_: JsonError) {
                body
            }
            return ProtocolError(status, detail.trim().take(400))
        }
    }
}
