package link.lan.core

import java.net.URLDecoder
import java.net.URLEncoder

/**
 * §5: `lanlink://pair?...` — the string behind the QR code.
 *
 * It carries the full certificate fingerprint, so the phone pins the right
 * identity rather than trusting whatever answers on that address.
 */
data class Invite(
    val host: String,
    val port: Int,
    val code: String = "",
    val deviceId: String = "",
    val name: String = "",
    val fingerprint: String = "",
    val scheme: String = "https",
) {
    val baseUrl: String get() = "$scheme://$host:$port"

    /** §4: a full 64-character fingerprint is a pin; anything shorter is a hint. */
    val hasPin: Boolean get() = fingerprint.length == 64 && fingerprint.all { it.isHexDigit() }

    val isSecure: Boolean get() = scheme == "https"

    fun toUrl(): String {
        val fields = linkedMapOf(
            "host" to host,
            "port" to port.toString(),
            "code" to code,
            "id" to deviceId,
            "name" to name,
            "fp" to fingerprint,
            "scheme" to scheme,
        ).filterValues { it.isNotEmpty() }
        val query = fields.entries.joinToString("&") { (key, value) ->
            "$key=${URLEncoder.encode(value, "UTF-8")}"
        }
        return "lanlink://pair?$query"
    }

    companion object {
        /** Accept an invite URL, or a bare `host:port` somebody typed in. */
        fun parse(text: String): Invite {
            val candidate = text.trim()
            if (candidate.isEmpty()) throw InvalidInvite("Paste a LanLink invite or an address first.")
            return if (candidate.lowercase().startsWith("lanlink://")) {
                fromUrl(candidate)
            } else {
                fromAddress(candidate)
            }
        }

        private fun fromUrl(url: String): Invite {
            val withoutScheme = url.substring("lanlink://".length)
            val action = withoutScheme.substringBefore('?').trim('/').lowercase()
            if (action.isNotEmpty() && action != "pair") {
                throw InvalidInvite("That LanLink link is not a pairing invite.")
            }
            val values = HashMap<String, String>()
            val query = withoutScheme.substringAfter('?', "")
            for (pair in query.split('&')) {
                if (pair.isEmpty()) continue
                val key = pair.substringBefore('=')
                val raw = pair.substringAfter('=', "")
                if (key.isNotEmpty()) {
                    values[key] = runCatching { URLDecoder.decode(raw, "UTF-8") }.getOrDefault(raw)
                }
            }
            val host = values["host"].orEmpty().trim()
            val port = values["port"].orEmpty().trim().toIntOrNull()
            if (host.isEmpty() || port == null) {
                throw InvalidInvite("That invite is missing the device address.")
            }
            return Invite(
                host = host,
                port = port,
                code = values["code"].orEmpty().trim(),
                deviceId = values["id"].orEmpty().trim(),
                name = values["name"].orEmpty().trim(),
                fingerprint = values["fp"].orEmpty().trim().lowercase(),
                scheme = values["scheme"].orEmpty().trim().ifEmpty { "https" },
            )
        }

        private fun fromAddress(text: String): Invite {
            var candidate = text
            var scheme = "https"
            if (candidate.startsWith("http://") || candidate.startsWith("https://")) {
                scheme = candidate.substringBefore("://")
                candidate = candidate.substringAfter("://")
            }
            candidate = candidate.trim('/')
            val host = candidate.substringBefore(':').trim()
            if (host.isEmpty()) throw InvalidInvite("That does not look like a device address.")
            val portText = candidate.substringAfter(':', "")
            val port = if (portText.isEmpty()) DEFAULT_PORT else portText.toIntOrNull()
                ?: throw InvalidInvite("The port must be a number.")
            return Invite(host = host, port = port, scheme = scheme)
        }
    }
}

class InvalidInvite(message: String) : IllegalArgumentException(message)

private fun Char.isHexDigit(): Boolean = this in '0'..'9' || this in 'a'..'f' || this in 'A'..'F'
