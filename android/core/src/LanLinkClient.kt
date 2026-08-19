package link.lan.core

import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLSocketFactory

/**
 * The v1 client, over HttpURLConnection so it runs unchanged on Android and on
 * a plain JVM. No OkHttp, no serialization library, nothing to keep up to date.
 *
 * Every call is blocking. Callers put it on a background thread — on Android a
 * coroutine on Dispatchers.IO — because none of this may run on the UI thread.
 */
class LanLinkClient(
    val baseUrl: String,
    var token: String? = null,
    private val socketFactory: SSLSocketFactory? = null,
    private val connectTimeoutMillis: Int = 8000,
    private val readTimeoutMillis: Int = 60000,
) {

    constructor(invite: Invite, pinnedPem: String?, token: String? = null) : this(
        baseUrl = invite.baseUrl,
        token = token,
        socketFactory = pinnedPem?.let { Pinning.socketFactoryForPem(it) },
    )

    // ------------------------------------------------------------ unauthenticated

    /** §7. Cheap reachability check; touches no file. */
    fun health(): Device = Device.from(getJson("/health", authenticated = false).child("device"))

    /** §7. `pairing_armed` tells a waiting client when the owner switched pairing on. */
    fun deviceInfo(): Pair<Device, Boolean> {
        val body = getJson("/v1/device", authenticated = false)
        return Device.from(body.child("device")) to body.boolean("pairing_armed")
    }

    /**
     * §5. The client owns its id: generate one at install time and keep it, so
     * the pairing can be revoked later.
     */
    fun pair(clientId: String, clientName: String, code: String): PairOutcome {
        val body = Json.encodeObject(
            mapOf("client_id" to clientId, "client_name" to clientName, "pair_code" to code)
        )
        val response = request("POST", "/v1/pair", authenticated = false) { connection ->
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
        }
        if (response.status == 200) {
            val values = Json.parseObject(response.text)
            val outcome = PairOutcome.Paired(values.string("token"), Device.from(values.child("device")))
            token = outcome.token
            return outcome
        }
        val detail = ProtocolError.of(response.status, response.text).detail
        // §5: the code distinguishes these, and each says something different
        // to the person holding the phone.
        return when (response.status) {
            409 -> PairOutcome.NotArmed
            403 -> if (detail.contains("declined", ignoreCase = true)) {
                PairOutcome.Declined
            } else {
                PairOutcome.WrongCode
            }
            429 -> if (detail.contains("incorrect", ignoreCase = true)) {
                PairOutcome.LockedOut
            } else {
                PairOutcome.RateLimited
            }
            else -> PairOutcome.Failed(response.status, detail)
        }
    }

    /** §6. A device may only remove its own pairing. */
    fun unpair(clientId: String): Boolean =
        requestJson("DELETE", "/v1/pairings/${encode(clientId)}").boolean("revoked")

    // -------------------------------------------------------------------- browsing

    /** §8. */
    fun shares(): List<Share> = getJson("/v1/shares").objects("shares").map(Share::from)

    /** §9. Returned paths go back verbatim as the next `path`. */
    fun list(shareId: String, path: String = ""): List<Entry> =
        getJson("/v1/shares/${encode(shareId)}/list", "path" to path)
            .objects("entries")
            .map(Entry::from)

    /** §10. `path = ""` describes the share root. */
    fun properties(shareId: String, path: String = ""): Properties =
        Properties.from(getJson("/v1/shares/${encode(shareId)}/properties", "path" to path))

    /** §10. Computed on demand; allow a generous read timeout for a big file. */
    fun checksum(shareId: String, path: String): String =
        getJson("/v1/shares/${encode(shareId)}/checksum", "path" to path).string("sha256")

    /** §12c. Where a previous upload stopped. */
    fun partialStatus(shareId: String, folder: String, name: String): PartialStatus =
        PartialStatus.from(
            getJson("/v1/shares/${encode(shareId)}/partial", "path" to folder, "name" to name)
        )

    // ------------------------------------------------------------------- transfers

    /**
     * §11. Open a download, resuming from [offset], and hand the body to
     * [receive] along with what the reply means.
     *
     * The decision is made here rather than by the caller because getting it
     * wrong corrupts files quietly: a 200 in reply to a Range request means the
     * peer ignored it, and whatever is already on disk has to go.
     */
    fun <T> download(
        shareId: String,
        path: String,
        offset: Long = 0,
        receive: (decision: ResumeDecision, body: InputStream) -> T,
    ): T {
        val connection = open("GET", url("/v1/files/${encode(shareId)}", "path" to path))
        Downloads.rangeHeader(offset)?.let { connection.setRequestProperty("Range", it) }
        connection.connect()
        val status = connection.responseCode
        if (status !in 200..299) {
            val detail = readError(connection)
            connection.disconnect()
            throw ProtocolError.of(status, detail)
        }
        val decision = Downloads.decide(
            status = status,
            requestedOffset = offset,
            contentRange = connection.getHeaderField("Content-Range"),
            contentLength = connection.contentLengthLong.takeIf { it >= 0 },
        )
        return try {
            connection.inputStream.use { receive(decision, it) }
        } finally {
            connection.disconnect()
        }
    }

    /**
     * §12a. Stream bytes in, resumable and checksum-verified.
     *
     * [write] is handed the raw output stream so a phone can copy straight from
     * a ContentResolver stream: the file is never held in memory.
     */
    fun upload(
        shareId: String,
        folder: String,
        name: String,
        offset: Long = 0,
        finalize: Boolean = true,
        sha256: String? = null,
        write: (OutputStream) -> Unit,
    ): UploadResult {
        val parameters = mutableListOf(
            "path" to folder,
            "name" to name,
            "offset" to offset.toString(),
            "finalize" to finalize.toString().lowercase(),
        )
        sha256?.let { parameters.add("sha256" to it) }
        val connection = open("PUT", url("/v1/files/${encode(shareId)}", *parameters.toTypedArray()))
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/octet-stream")
        connection.setChunkedStreamingMode(CHUNK_SIZE)
        try {
            connection.outputStream.use(write)
            val status = connection.responseCode
            if (status !in 200..299) {
                throw UploadConflict(
                    status = status,
                    detail = ProtocolError.of(status, readError(connection)).detail,
                    resumeFrom = Uploads.offsetFromConflict(
                        status, connection.getHeaderField(RECEIVED_HEADER)
                    ),
                )
            }
            return UploadResult.from(Json.parseObject(connection.readBody()))
        } finally {
            connection.disconnect()
        }
    }

    /** §12d. Publish a body that was sent with `finalize=false`. */
    fun finalizeUpload(shareId: String, folder: String, name: String, sha256: String? = null): UploadResult {
        val parameters = mutableListOf("path" to folder, "name" to name)
        sha256?.let { parameters.add("sha256" to it) }
        return UploadResult.from(
            requestJson("POST", "/v1/shares/${encode(shareId)}/finalize", *parameters.toTypedArray())
        )
    }

    // ------------------------------------------------------------------ operations

    /** §13. One level at a time; the parent must already exist. */
    fun createFolder(shareId: String, parent: String, name: String): String =
        postJson("/v1/shares/${encode(shareId)}/folders", mapOf("path" to parent, "name" to name))
            .string("name")

    /** §14. Renames in place; it cannot move an item between folders. */
    fun rename(shareId: String, path: String, newName: String): String =
        postJson("/v1/shares/${encode(shareId)}/rename", mapOf("path" to path, "new_name" to newName))
            .string("name")

    /** §15. A non-empty folder needs [recursive]; without it the peer answers 409. */
    fun delete(shareId: String, path: String, recursive: Boolean = false): String =
        requestJson(
            "DELETE",
            "/v1/shares/${encode(shareId)}/entries",
            "path" to path,
            "recursive" to recursive.toString().lowercase(),
        ).string("kind")

    /** §17 and §18. Both shares belong to the device being asked. */
    fun copyOrMove(
        sourceShareId: String,
        sourcePath: String,
        destinationShareId: String,
        destinationPath: String = "",
        move: Boolean = false,
    ): String = postJson(
        "/v1/operations",
        mapOf(
            "source_share_id" to sourceShareId,
            "source_path" to sourcePath,
            "destination_share_id" to destinationShareId,
            "destination_path" to destinationPath,
            "operation" to if (move) "move" else "copy",
        ),
    ).string("path")

    // ------------------------------------------------------------------- plumbing

    private data class Response(val status: Int, val text: String)

    private fun getJson(path: String, vararg params: Pair<String, String>): Map<String, Any?> =
        requestJson("GET", path, *params)

    private fun postJson(path: String, body: Map<String, String>): Map<String, Any?> {
        val encoded = Json.encodeObject(body)
        val response = request("POST", path) { connection ->
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(encoded.toByteArray(Charsets.UTF_8)) }
        }
        return response.requireOk()
    }

    private fun requestJson(
        method: String,
        path: String,
        vararg params: Pair<String, String>,
    ): Map<String, Any?> = request(method, path, params = params).requireOk()

    private fun Response.requireOk(): Map<String, Any?> {
        if (status !in 200..299) throw ProtocolError.of(status, text)
        return if (text.isBlank()) emptyMap() else Json.parseObject(text)
    }

    private fun getJson(
        path: String,
        authenticated: Boolean,
        vararg params: Pair<String, String>,
    ): Map<String, Any?> = request("GET", path, authenticated, params).requireOk()

    private fun request(
        method: String,
        path: String,
        authenticated: Boolean = true,
        params: Array<out Pair<String, String>> = emptyArray(),
        prepare: (HttpURLConnection) -> Unit = {},
    ): Response {
        val connection = open(method, url(path, *params), authenticated)
        try {
            prepare(connection)
            val status = connection.responseCode
            val text = if (status in 200..299) connection.readBody() else readError(connection)
            return Response(status, text)
        } finally {
            connection.disconnect()
        }
    }

    private fun open(method: String, target: URL, authenticated: Boolean = true): HttpURLConnection {
        val connection = target.openConnection() as HttpURLConnection
        if (connection is HttpsURLConnection && socketFactory != null) {
            // §4: the pin is the identity, so the hostname is not checked — safe
            // only because this factory trusts exactly one certificate.
            connection.sslSocketFactory = socketFactory
            connection.hostnameVerifier = Pinning.hostnameVerifier()
        }
        connection.requestMethod = method
        connection.connectTimeout = connectTimeoutMillis
        connection.readTimeout = readTimeoutMillis
        connection.instanceFollowRedirects = false
        connection.setRequestProperty("Accept", "application/json")
        if (authenticated) {
            val current = token ?: throw ProtocolError(401, "Pair with this device first.")
            connection.setRequestProperty(TOKEN_HEADER, current)
        }
        return connection
    }

    private fun url(path: String, vararg params: Pair<String, String>): URL {
        val query = params.joinToString("&") { (key, value) -> "$key=${encode(value)}" }
        return URL(baseUrl.trimEnd('/') + path + if (query.isEmpty()) "" else "?$query")
    }

    private fun encode(value: String): String = URLEncoder.encode(value, "UTF-8")

    private fun HttpURLConnection.readBody(): String =
        inputStream.use { it.readBytes().toString(Charsets.UTF_8) }

    private fun readError(connection: HttpURLConnection): String = try {
        connection.errorStream?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty()
    } catch (_: IOException) {
        ""
    }

    companion object {
        const val CHUNK_SIZE = 512 * 1024
    }
}

/**
 * §12a. A 409 that carries [resumeFrom] is an instruction, not a failure: send
 * again from that offset. A 409 without one means the name is taken.
 */
class UploadConflict(val status: Int, val detail: String, val resumeFrom: Long?) :
    RuntimeException(detail) {
    val canResume: Boolean get() = resumeFrom != null
}
