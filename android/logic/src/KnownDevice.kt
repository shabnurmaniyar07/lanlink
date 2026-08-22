package link.lan.app

import link.lan.core.Device
import link.lan.core.Json
import link.lan.core.Paths
import link.lan.core.long
import link.lan.core.objects
import link.lan.core.string

/**
 * A device this phone has paired with, and everything needed to talk to it again.
 *
 * The token and the certificate fingerprint live together on purpose. A token
 * without its pin would let the phone authenticate to whatever answers at that
 * address, which is exactly the attack pinning exists to stop (protocol §4).
 */
data class KnownDevice(
    val id: String,
    val name: String,
    val host: String,
    val port: Int,
    val fingerprint: String,
    val token: String,
    val certificatePem: String = "",
    val lastSeen: Long = 0,
) {
    init {
        require(id.isNotBlank()) { "a device without an id cannot be found again" }
        require(fingerprint.isNotBlank()) { "a paired device must carry the certificate it was pinned to" }
        require(token.isNotBlank()) { "a paired device must carry its token" }
    }

    val address: String get() = "$host:$port"

    fun seenAt(host: String, port: Int, moment: Long): KnownDevice =
        copy(host = host, port = port, lastSeen = moment)

    fun renamedTo(name: String): KnownDevice = if (name.isBlank()) this else copy(name = name)

    fun toMap(): Map<String, String> = mapOf(
        "id" to id,
        "name" to name,
        "host" to host,
        "port" to port.toString(),
        "fingerprint" to fingerprint,
        "token" to token,
        "certificate" to certificatePem,
        "last_seen" to lastSeen.toString(),
    )

    companion object {
        fun from(values: Map<String, Any?>): KnownDevice = KnownDevice(
            id = values.string("id"),
            name = values.string("name"),
            host = values.string("host"),
            port = values.long("port", link.lan.core.DEFAULT_PORT.toLong()).toInt(),
            fingerprint = values.string("fingerprint").lowercase(),
            token = values.string("token"),
            certificatePem = values.string("certificate"),
            lastSeen = values.long("last_seen"),
        )
    }
}

/**
 * A device seen on the network but not necessarily paired: what mDNS hands over,
 * or what an invite carries.
 */
data class SeenDevice(
    val id: String,
    val name: String,
    val host: String,
    val port: Int,
    val fingerprint: String = "",
) {
    companion object {
        fun of(device: Device, host: String, port: Int) =
            SeenDevice(device.id, device.name, host, port, device.fingerprint)
    }
}

/** How a row in the device list should read. */
enum class DeviceStanding {
    /** Paired and answering. */
    READY,

    /** Paired, but not on the network right now. */
    AWAY,

    /** On the network, never paired. */
    NEW,

    /** Paired, answering, and presenting a different certificate. Do not connect. */
    IMPOSTOR,
}

data class DeviceRow(
    val id: String,
    val name: String,
    val host: String,
    val port: Int,
    val standing: DeviceStanding,
    val known: KnownDevice? = null,
) {
    val isPaired: Boolean get() = known != null
    val canOpen: Boolean get() = standing == DeviceStanding.READY
    val address: String get() = "$host:$port"
}

/**
 * The paired devices, and the JSON they are stored as.
 *
 * The Android layer only ever handles the string: read it at start, hand back
 * whatever [asJson] returns after a change. Keeping the format here means the
 * phone and the tests agree by construction.
 */
class DeviceStore(devices: List<KnownDevice> = emptyList()) {
    private val byId = LinkedHashMap<String, KnownDevice>()

    init {
        devices.forEach { byId[it.id] = it }
    }

    fun all(): List<KnownDevice> = byId.values.sortedBy { it.name.lowercase() }

    fun find(id: String): KnownDevice? = byId[id]

    fun isPaired(id: String): Boolean = byId.containsKey(id)

    /** Add or replace. Re-pairing a device replaces its token and its pin together. */
    fun remember(device: KnownDevice): KnownDevice {
        byId[device.id] = device
        return device
    }

    /** Returns the device that was dropped, so the caller can also unpair remotely. */
    fun forget(id: String): KnownDevice? = byId.remove(id)

    fun seen(id: String, host: String, port: Int, moment: Long, name: String = ""): KnownDevice? {
        val existing = byId[id] ?: return null
        var updated = existing.seenAt(host, port, moment)
        if (name.isNotBlank() && name != existing.name) {
            updated = updated.renamedTo(name)
        }
        byId[id] = updated
        return updated
    }

    fun asJson(): String {
        val entries = all().joinToString(",") { Json.encodeObject(it.toMap()) }
        return """{"version":1,"devices":[$entries]}"""
    }

    companion object {
        /**
         * A missing, empty or damaged store is an empty store, never a crash —
         * and one damaged record costs one pairing, not all of them.
         *
         * Losing every device because a single entry lost its token would mean
         * re-pairing every PC by hand, which is a much worse afternoon than
         * re-pairing one.
         */
        fun fromJson(text: String?): DeviceStore {
            if (text.isNullOrBlank()) return DeviceStore()
            val records = try {
                Json.parseObject(text).objects("devices")
            } catch (_: Exception) {
                return DeviceStore()
            }
            return DeviceStore(
                records.mapNotNull { record ->
                    try {
                        KnownDevice.from(record)
                    } catch (_: Exception) {
                        // A device missing its id, token or pin is not usable and
                        // not repairable. Drop that one and keep the rest.
                        null
                    }
                }
            )
        }
    }
}

/**
 * What the device list should show: everything paired, everything discovered,
 * neither hiding the other.
 *
 * A paired device presenting an unexpected fingerprint is [DeviceStanding.IMPOSTOR]
 * rather than merely offline — silence is safer than a quiet reconnection to
 * whatever has taken the address.
 */
fun mergeDevices(known: List<KnownDevice>, seen: List<SeenDevice>): List<DeviceRow> {
    val live = seen.associateBy { it.id }
    val rows = ArrayList<DeviceRow>()

    for (device in known) {
        val here = live[device.id]
        val standing = when {
            here == null -> DeviceStanding.AWAY
            here.fingerprint.isNotBlank() &&
                !pinMatches(device.fingerprint, here.fingerprint) -> DeviceStanding.IMPOSTOR
            else -> DeviceStanding.READY
        }
        rows.add(
            DeviceRow(
                id = device.id,
                name = here?.name?.takeIf { it.isNotBlank() } ?: device.name,
                host = here?.host ?: device.host,
                port = here?.port ?: device.port,
                standing = standing,
                known = device,
            )
        )
    }

    for (device in seen) {
        if (known.any { it.id == device.id }) continue
        rows.add(DeviceRow(device.id, device.name, device.host, device.port, DeviceStanding.NEW))
    }

    return rows.sortedWith(compareBy({ it.standing.ordinal }, { it.name.lowercase() }))
}

/**
 * Does what mDNS advertised match the certificate we pinned?
 *
 * A TXT record has a small budget, so the desktop publishes only the first 32
 * characters of its fingerprint — a hint, not a pin. Comparing that with `==`
 * against the stored 64 would mark every paired device an impostor and nothing
 * would ever connect. A prefix is the right comparison, and 32 hex characters
 * is 128 bits, which nobody is colliding by accident.
 *
 * Anything shorter than 32 is not evidence of anything, and is treated as no
 * claim at all rather than as a match. The real pin is still checked at the TLS
 * handshake by [reconnect]; this only decides what the list says.
 */
internal fun pinMatches(stored: String, advertised: String): Boolean {
    if (advertised.length < 32) return true
    if (advertised.length > stored.length) return false
    return stored.startsWith(advertised, ignoreCase = true)
}

/** A display name that is safe to use as a folder on the phone. */
fun folderNameFor(device: DeviceRow): String = Paths.sanitiseForPeer(device.name, fallback = "device")

/** True when the listing is worth offering at all. */
fun hasAnything(rows: List<DeviceRow>, includeAway: Boolean = true): Boolean =
    rows.any { it.standing != DeviceStanding.IMPOSTOR && (includeAway || it.standing != DeviceStanding.AWAY) }
