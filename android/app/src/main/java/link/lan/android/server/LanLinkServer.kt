package link.lan.android.server

import android.content.Context
import android.net.Uri
import android.util.Log
import androidx.documentfile.provider.DocumentFile
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import link.lan.core.DEFAULT_PORT
import link.lan.core.Json
import link.lan.core.Paths
import link.lan.core.string
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import java.util.UUID
import java.util.concurrent.Executors
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLServerSocketFactory

data class PairedClient(
    val clientId: String,
    val deviceName: String,
    val token: String,
    val pairedAt: Long = System.currentTimeMillis(),
)

class LanLinkServer(
    private val context: Context,
    private val deviceId: String,
    private val deviceNameProvider: () -> String,
    private val identity: ServerIdentity,
    private val shareStore: LocalShareStore,
) {
    private var serverSocket: ServerSocket? = null
    private val executor = Executors.newCachedThreadPool()
    private val pairedClients = mutableMapOf<String, PairedClient>()

    private val _isArmed = MutableStateFlow(false)
    val isArmed: StateFlow<Boolean> = _isArmed.asStateFlow()

    private var pairingCode = ""
    private var pairingExpires = 0L

    var actualPort: Int = DEFAULT_PORT
        private set

    init {
        loadPairedClients()
    }

    fun armPairing(): String {
        val code = "%08d".format((10000000..99999999).random())
        pairingCode = code
        pairingExpires = System.currentTimeMillis() + 180_000L // 3 minutes
        _isArmed.value = true
        Log.i("LanLinkServer", "Pairing ARMED with code=$code, expires=$pairingExpires")
        return code
    }

    fun disarmPairing() {
        pairingCode = ""
        pairingExpires = 0L
        _isArmed.value = false
        Log.i("LanLinkServer", "Pairing DISARMED")
    }

    @Synchronized
    fun start(preferredPort: Int = DEFAULT_PORT): Int {
        if (serverSocket != null && !serverSocket!!.isClosed) return actualPort

        val sslFactory: SSLServerSocketFactory = identity.sslContext.serverSocketFactory
        var port = preferredPort
        var ss: ServerSocket? = null
        var attempts = 0
        while (ss == null && attempts < 10) {
            try {
                val socket = sslFactory.createServerSocket(port) as SSLServerSocket
                socket.needClientAuth = false
                ss = socket
            } catch (_: Exception) {
                port++
                attempts++
            }
        }

        val server = ss ?: throw IllegalStateException("Could not bind HTTPS server socket")
        actualPort = port
        serverSocket = server

        executor.submit {
            while (!server.isClosed) {
                try {
                    val clientSocket = server.accept()
                    executor.submit { handleConnection(clientSocket) }
                } catch (e: Exception) {
                    if (server.isClosed) break
                    Log.e("LanLinkServer", "Error accepting connection", e)
                }
            }
        }

        Log.i("LanLinkServer", "Server started on port $port")
        return port
    }

    @Synchronized
    fun stop() {
        try {
            serverSocket?.close()
        } catch (_: Exception) {}
        serverSocket = null
        disarmPairing()
        Log.i("LanLinkServer", "Server stopped")
    }

    private fun handleConnection(socket: Socket) {
        socket.use { s ->
            s.soTimeout = 30_000
            val input = BufferedInputStream(s.getInputStream())
            val output = BufferedOutputStream(s.getOutputStream())

            val reqLine = readLine(input) ?: return
            val parts = reqLine.split(' ')
            if (parts.size < 2) return
            val method = parts[0].uppercase()
            val rawUri = parts[1]

            val headers = mutableMapOf<String, String>()
            while (true) {
                val headerLine = readLine(input) ?: break
                if (headerLine.isEmpty()) break
                val colon = headerLine.indexOf(':')
                if (colon > 0) {
                    val key = headerLine.substring(0, colon).trim().lowercase()
                    val value = headerLine.substring(colon + 1).trim()
                    headers[key] = value
                }
            }

            val path = rawUri.substringBefore('?')
            val rawQuery = if (rawUri.contains('?')) rawUri.substringAfter('?') else ""

            when {
                path == "/health" -> {
                    sendJson(output, 200, """{"status":"healthy","uptime":1.0}""")
                }
                path == "/v1/device" && method == "GET" -> {
                    val devName = deviceNameProvider()
                    val body = """{"id":"$deviceId","name":${Json.quote(devName)},"platform":"Android","version":"0.1.2","api":"v1","fingerprint":"${identity.fingerprint}","scheme":"https","device":{"id":"$deviceId","name":${Json.quote(devName)},"platform":"Android","version":"0.1.2","api":"v1"}}"""
                    sendJson(output, 200, body)
                }
                path == "/v1/pair" && method == "POST" -> {
                    val contentLength = headers["content-length"]?.toIntOrNull() ?: 0
                    val bodyBytes = readExact(input, contentLength)
                    val bodyText = String(bodyBytes, StandardCharsets.UTF_8)
                    handlePair(output, bodyText)
                }
                path == "/v1/unpair" && method == "POST" -> {
                    val token = headers["x-lanlink-token"]
                    if (token == null || !pairedClients.containsKey(token)) {
                        sendJson(output, 401, """{"detail":"Pair this device before accessing files."}""")
                    } else {
                        pairedClients.remove(token)
                        savePairedClients()
                        sendJson(output, 200, """{"detail":"Unpaired."}""")
                    }
                }
                path == "/v1/shares" && method == "GET" -> {
                    val token = headers["x-lanlink-token"]
                    if (token == null || !pairedClients.containsKey(token)) {
                        sendJson(output, 401, """{"detail":"Pair this device before accessing files."}""")
                    } else {
                        val shares = shareStore.all()
                        val shareJsonList = shares.joinToString(",") { s ->
                            val perm = if (s.writable && s.removable) "rwd" else if (s.writable) "rw" else "r"
                            """{"id":"${s.id}","name":${Json.quote(s.name)},"path":${Json.quote(s.name)},"permissions":"$perm","writable":${s.writable},"removable":${s.removable},"available":true}"""
                        }
                        sendJson(output, 200, """{"shares":[$shareJsonList]}""")
                    }
                }
                path.startsWith("/v1/shares/") -> {
                    val token = headers["x-lanlink-token"]
                    if (token == null || !pairedClients.containsKey(token)) {
                        sendJson(output, 401, """{"detail":"Pair this device before accessing files."}""")
                        return
                    }
                    val after = path.removePrefix("/v1/shares/")
                    val shareId = after.substringBefore('/')
                    val action = after.substringAfter('/', "")
                    val share = shareStore.find(shareId)
                    if (share == null) {
                        sendJson(output, 404, """{"detail":"This shared folder is currently unavailable."}""")
                        return
                    }
                    val params = queryParams(rawQuery)
                    when (action) {
                        "list" -> handleList(output, share, params["path"] ?: "")
                        "browse" -> handleBrowse(output, share, params["path"] ?: "", method, headers["range"])
                        "folders" -> {
                            val contentLength = headers["content-length"]?.toIntOrNull() ?: 0
                            val bodyBytes = readExact(input, contentLength)
                            val bodyText = String(bodyBytes, StandardCharsets.UTF_8)
                            handleCreateFolder(output, share, bodyText)
                        }
                        else -> sendJson(output, 404, """{"detail":"Not found"}""")
                    }
                }
                path.startsWith("/v1/files/") -> {
                    val token = headers["x-lanlink-token"]
                    if (token == null || !pairedClients.containsKey(token)) {
                        sendJson(output, 401, """{"detail":"Pair this device before accessing files."}""")
                        return
                    }
                    val shareId = path.removePrefix("/v1/files/").substringBefore('/')
                    val share = shareStore.find(shareId)
                    if (share == null) {
                        sendJson(output, 404, """{"detail":"This shared folder is currently unavailable."}""")
                        return
                    }
                    val params = queryParams(rawQuery)
                    if (method == "GET" || method == "HEAD") {
                        handleBrowse(output, share, params["path"] ?: "", method, headers["range"])
                    } else if (method == "PUT") {
                        val isChunked = headers["transfer-encoding"]?.contains("chunked", ignoreCase = true) == true
                        val contentLength = headers["content-length"]?.toLongOrNull() ?: -1L
                        handleUpload(input, output, share, params, contentLength, isChunked)
                    } else if (method == "DELETE") {
                        handleDelete(output, share, params["path"] ?: "")
                    } else {
                        sendJson(output, 405, """{"detail":"Method not allowed"}""")
                    }
                }
                path == "/v1/clipboard" -> {
                    val token = headers["x-lanlink-token"]
                    if (token == null || !pairedClients.containsKey(token)) {
                        sendJson(output, 401, """{"detail":"Pair this device before accessing clipboard."}""")
                        return
                    }
                    if (method == "GET") {
                        val text = link.lan.android.service.ClipboardSyncCentre.getLastClipboard(context)
                        sendJson(output, 200, """{"text":${Json.quote(text)}}""")
                    } else if (method == "POST") {
                        val contentLength = headers["content-length"]?.toIntOrNull() ?: 0
                        val bodyBytes = readExact(input, contentLength)
                        val bodyText = String(bodyBytes, StandardCharsets.UTF_8)
                        val json = try { Json.parseObject(bodyText) } catch (_: Exception) { emptyMap() }
                        val text = json.string("text")
                        link.lan.android.service.ClipboardSyncCentre.setRemoteClipboard(context, text)
                        sendJson(output, 200, """{"status":"ok","length":${text.length}}""")
                    } else {
                        sendJson(output, 405, """{"detail":"Method not allowed"}""")
                    }
                }
                else -> {
                    sendJson(output, 404, """{"detail":"Not found"}""")
                }
            }
        }
    }

    private fun handlePair(output: OutputStream, bodyText: String) {
        val json = try { Json.parseObject(bodyText) } catch (_: Exception) { emptyMap() }
        val clientId = json.string("client_id").ifEmpty { json.string("id") }
        val clientName = json.string("client_name").ifEmpty { json.string("device_name") }
        val rawCode = json.string("pair_code").ifEmpty { json.string("code") }
        val code = rawCode.replace("-", "").replace(" ", "").trim()

        if (!_isArmed.value || System.currentTimeMillis() > pairingExpires) {
            disarmPairing()
            sendJson(output, 409, """{"detail":"This device is not in pairing mode. Enable pairing on it first."}""")
            return
        }

        val expectedCode = pairingCode.replace("-", "").replace(" ", "").trim()
        if (code != expectedCode) {
            sendJson(output, 403, """{"detail":"That pairing code is not correct."}""")
            return
        }

        disarmPairing()
        val token = UUID.randomUUID().toString()
        val client = PairedClient(clientId, clientName, token)
        pairedClients[token] = client
        savePairedClients()

        val devName = deviceNameProvider()
        val resp = """{"token":"$token","device":{"id":"$deviceId","name":${Json.quote(devName)},"platform":"Android","version":"0.1.2","api":"v1"},"device_id":"$deviceId","device_name":${Json.quote(devName)},"api":"v1"}"""
        sendJson(output, 200, resp)
    }

    private fun handleList(output: OutputStream, share: LocalShare, relPath: String) {
        try {
            val entries = shareStore.listFolder(share, relPath)
            val jsonEntries = entries.joinToString(",") { e ->
                val isDir = e["is_dir"] as Boolean
                val kind = if (isDir) "folder" else "file"
                val sizeVal = if (isDir) "null" else "${e["size"]}"
                """{"name":${Json.quote(e["name"] as String)},"path":${Json.quote(e["path"] as String)},"kind":"$kind","is_dir":$isDir,"size":$sizeVal,"modified_at":${e["modified"]},"modified":${e["modified"]},"extension":${Json.quote(e["extension"] as String)}}"""
            }
            sendJson(output, 200, """{"entries":[$jsonEntries]}""")
        } catch (e: Exception) {
            sendJson(output, 404, """{"detail":"${e.message ?: "Folder error"}"}""")
        }
    }

    private fun handleBrowse(output: OutputStream, share: LocalShare, relPath: String, method: String, rangeHeader: String?) {
        val file = shareStore.resolve(share, relPath)
        if (file == null || !file.exists() || file.isDirectory) {
            sendJson(output, 404, """{"detail":"The file was not found."}""")
            return
        }

        val totalLength = file.length()
        val range = parseRange(rangeHeader, totalLength)
        val mime = file.type ?: "application/octet-stream"

        if (method == "HEAD") {
            val sb = StringBuilder()
            if (range != null) {
                sb.append("HTTP/1.1 206 Partial Content\r\n")
                sb.append("Content-Range: bytes ${range.first}-${range.second}/$totalLength\r\n")
            } else {
                sb.append("HTTP/1.1 200 OK\r\n")
            }
            sb.append("Content-Type: $mime\r\n")
            sb.append("Content-Length: ${if (range != null) (range.second - range.first + 1) else totalLength}\r\n")
            sb.append("Accept-Ranges: bytes\r\n\r\n")
            output.write(sb.toString().toByteArray(StandardCharsets.US_ASCII))
            output.flush()
            return
        }

        if (range != null) {
            val (start, end) = range
            val length = end - start + 1
            val header = "HTTP/1.1 206 Partial Content\r\nContent-Type: $mime\r\nContent-Range: bytes $start-$end/$totalLength\r\nContent-Length: $length\r\nAccept-Ranges: bytes\r\n\r\n"
            output.write(header.toByteArray(StandardCharsets.US_ASCII))
            context.contentResolver.openInputStream(file.uri)?.use { stream ->
                skipFully(stream, start)
                copyLimited(stream, output, length)
            }
        } else {
            val header = "HTTP/1.1 200 OK\r\nContent-Type: $mime\r\nContent-Length: $totalLength\r\nAccept-Ranges: bytes\r\n\r\n"
            output.write(header.toByteArray(StandardCharsets.US_ASCII))
            context.contentResolver.openInputStream(file.uri)?.use { stream ->
                copyStream(stream, output)
            }
        }
        output.flush()
    }

    private fun handleUpload(input: InputStream, output: OutputStream, share: LocalShare, params: Map<String, String>, contentLength: Long, isChunked: Boolean) {
        if (!share.writable) {
            sendJson(output, 403, """{"detail":"This shared folder is read-only."}""")
            return
        }
        val relPath = params["path"] ?: ""
        val name = params["name"] ?: ""
        val finalize = params["finalize"] == "true"
        val offset = params["offset"]?.toLongOrNull() ?: 0L

        val nameProblem = Paths.nameProblem(name)
        if (nameProblem != null) {
            sendJson(output, 400, """{"detail":"$nameProblem"}""")
            return
        }

        val folder = shareStore.resolve(share, relPath)
        if (folder == null || !folder.isDirectory) {
            sendJson(output, 404, """{"detail":"The destination folder was not found."}""")
            return
        }

        val partName = "$name.lanlink-part"
        var partFile = folder.findFile(partName)
        if (partFile == null) {
            partFile = folder.createFile("application/octet-stream", partName)
        }

        if (partFile == null) {
            sendJson(output, 500, """{"detail":"Could not create file."}""")
            return
        }

        context.contentResolver.openOutputStream(partFile.uri, if (offset > 0) "wa" else "w")?.use { out ->
            if (isChunked) {
                readChunkedBody(input, out)
            } else if (contentLength >= 0) {
                copyLimited(input, out, contentLength)
            } else {
                copyStream(input, out)
            }
        }

        if (finalize) {
            folder.findFile(name)?.delete()
            partFile.renameTo(name)
        }

        sendJson(output, 200, """{"detail":"Uploaded.","name":${Json.quote(name)}}""")
    }

    private fun readChunkedBody(input: InputStream, output: OutputStream) {
        while (true) {
            val line = readLine(input) ?: break
            val hex = line.trim().substringBefore(';').trim()
            if (hex.isEmpty()) continue
            val chunkSize = hex.toIntOrNull(16) ?: break
            if (chunkSize == 0) {
                readLine(input) // consume trailing CRLF
                break
            }
            copyLimited(input, output, chunkSize.toLong())
            readLine(input) // consume CRLF after chunk data
        }
    }

    private fun handleDelete(output: OutputStream, share: LocalShare, relPath: String) {
        if (!share.removable) {
            sendJson(output, 403, """{"detail":"Deleting is not permitted in this shared folder."}""")
            return
        }
        val file = shareStore.resolve(share, relPath)
        if (file == null || !file.exists()) {
            sendJson(output, 404, """{"detail":"The file was not found."}""")
            return
        }
        val deleted = file.delete()
        if (deleted) {
            sendJson(output, 200, """{"detail":"Deleted."}""")
        } else {
            sendJson(output, 500, """{"detail":"Could not delete file."}""")
        }
    }

    private fun handleCreateFolder(output: OutputStream, share: LocalShare, bodyText: String) {
        if (!share.writable) {
            sendJson(output, 403, """{"detail":"This shared folder is read-only."}""")
            return
        }
        val json = try { Json.parseObject(bodyText) } catch (_: Exception) { emptyMap() }
        val relPath = json.string("path")
        val name = json.string("name")

        val nameProblem = Paths.nameProblem(name)
        if (nameProblem != null) {
            sendJson(output, 400, """{"detail":"$nameProblem"}""")
            return
        }

        val folder = shareStore.resolve(share, relPath)
        if (folder == null || !folder.isDirectory) {
            sendJson(output, 404, """{"detail":"The parent folder was not found."}""")
            return
        }

        val created = folder.createDirectory(name)
        if (created != null) {
            sendJson(output, 201, """{"detail":"Created.","name":${Json.quote(name)}}""")
        } else {
            sendJson(output, 500, """{"detail":"Could not create folder."}""")
        }
    }

    private fun sendJson(output: OutputStream, status: Int, json: String) {
        val statusText = when (status) {
            200 -> "OK"
            201 -> "Created"
            400 -> "Bad Request"
            401 -> "Unauthorized"
            403 -> "Forbidden"
            404 -> "Not Found"
            405 -> "Method Not Allowed"
            409 -> "Conflict"
            429 -> "Too Many Requests"
            else -> "Error"
        }
        val bytes = json.toByteArray(StandardCharsets.UTF_8)
        val header = "HTTP/1.1 $status $statusText\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n"
        output.write(header.toByteArray(StandardCharsets.US_ASCII))
        output.write(bytes)
        output.flush()
    }

    private fun readLine(input: InputStream): String? {
        val bos = ByteArrayOutputStream()
        var last = -1
        while (true) {
            val b = input.read()
            if (b == -1) {
                if (bos.size() == 0) return null
                break
            }
            if (b == '\n'.code) {
                if (last == '\r'.code) {
                    val arr = bos.toByteArray()
                    return String(arr, 0, arr.size - 1, StandardCharsets.UTF_8)
                }
                return bos.toString("UTF-8")
            }
            bos.write(b)
            last = b
        }
        return bos.toString("UTF-8")
    }

    private fun readExact(input: InputStream, length: Int): ByteArray {
        val bytes = ByteArray(length)
        var total = 0
        while (total < length) {
            val read = input.read(bytes, total, length - total)
            if (read == -1) break
            total += read
        }
        return if (total == length) bytes else bytes.copyOf(total)
    }

    private fun queryParams(raw: String): Map<String, String> {
        if (raw.isBlank()) return emptyMap()
        return raw.split('&').associate {
            val pair = it.split('=', limit = 2)
            val key = URLDecoder.decode(pair[0], "UTF-8")
            val value = if (pair.size > 1) URLDecoder.decode(pair[1], "UTF-8") else ""
            key to value
        }
    }

    private fun parseRange(header: String?, totalSize: Long): Pair<Long, Long>? {
        if (header.isNullOrBlank() || !header.startsWith("bytes=")) return null
        val parts = header.removePrefix("bytes=").split('-')
        val start = parts[0].toLongOrNull() ?: return null
        val end = if (parts.size > 1 && parts[1].isNotEmpty()) parts[1].toLongOrNull() ?: (totalSize - 1) else (totalSize - 1)
        if (start < 0 || end < start || start >= totalSize) return null
        return start to end.coerceAtMost(totalSize - 1)
    }

    private fun copyStream(from: InputStream, to: OutputStream) {
        val buf = ByteArray(64 * 1024)
        var read: Int
        while (from.read(buf).also { read = it } != -1) {
            to.write(buf, 0, read)
        }
    }

    private fun copyLimited(from: InputStream, to: OutputStream, limit: Long) {
        val buf = ByteArray(64 * 1024)
        var remaining = limit
        while (remaining > 0) {
            val toRead = remaining.coerceAtMost(buf.size.toLong()).toInt()
            val read = from.read(buf, 0, toRead)
            if (read == -1) break
            to.write(buf, 0, read)
            remaining -= read
        }
    }

    private fun skipFully(stream: InputStream, toSkip: Long) {
        var remaining = toSkip
        while (remaining > 0) {
            val skipped = stream.skip(remaining)
            if (skipped <= 0) {
                if (stream.read() == -1) break
                remaining -= 1
            } else {
                remaining -= skipped
            }
        }
    }

    private fun loadPairedClients() {
        val prefs = context.getSharedPreferences("lanlink_server_clients", Context.MODE_PRIVATE)
        val raw = prefs.getString("clients_json", null) ?: return
        try {
            val root = Json.parseObject(raw)
            val list = root["clients"] as? List<*> ?: return
            for (item in list) {
                val map = item as? Map<*, *> ?: continue
                val token = map["token"] as? String ?: continue
                val clientId = map["client_id"] as? String ?: ""
                val name = map["name"] as? String ?: ""
                pairedClients[token] = PairedClient(clientId, name, token)
            }
        } catch (_: Exception) {}
    }

    private fun savePairedClients() {
        val prefs = context.getSharedPreferences("lanlink_server_clients", Context.MODE_PRIVATE)
        val list = pairedClients.values.map {
            """{"client_id":${Json.quote(it.clientId)},"name":${Json.quote(it.deviceName)},"token":${Json.quote(it.token)}}"""
        }
        val json = """{"clients":[${list.joinToString(",")}]}"""
        prefs.edit().putString("clients_json", json).apply()
    }
}
