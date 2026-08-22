package link.lan.android.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import link.lan.app.DeviceStore
import link.lan.app.KnownDevice
import java.util.UUID

/**
 * Where the paired devices live on the phone.
 *
 * The file holds access tokens, so it is encrypted with a key the hardware
 * keystore holds and this process can use but not read. Everything about the
 * *shape* of that file — what a device record is, what makes one valid, what a
 * damaged record costs — already lives in [DeviceStore] and is tested there.
 * This class only moves a string in and out.
 *
 * If the keystore cannot be used, [open] throws rather than quietly falling
 * back to an unencrypted file. Somewhere to put tokens is not a feature to
 * degrade silently.
 */
class SecureStore private constructor(private val preferences: SharedPreferences) {

    /** Read the store. A missing or unreadable file is an empty store. */
    fun load(): DeviceStore = DeviceStore.fromJson(preferences.getString(DEVICES_KEY, null))

    /** Write the store back. Called after every pair, forget or address change. */
    fun save(store: DeviceStore) {
        preferences.edit().putString(DEVICES_KEY, store.asJson()).apply()
    }

    fun remember(device: KnownDevice): DeviceStore {
        val store = load()
        store.remember(device)
        save(store)
        return store
    }

    fun forget(id: String): DeviceStore {
        val store = load()
        store.forget(id)
        save(store)
        return store
    }

    /**
     * This phone's identity to the other side: a client id it keeps for good.
     *
     * The PC's pairing list is keyed on it, so a new id on every launch would
     * fill that list with strangers and leave the phone unable to unpair any of
     * them.
     */
    fun clientId(): String = preferences.getString(CLIENT_ID_KEY, null) ?: run {
        val minted = "android-${UUID.randomUUID()}"
        preferences.edit().putString(CLIENT_ID_KEY, minted).apply()
        minted
    }

    /** The name shown on the PC when this phone pairs. */
    var deviceName: String
        get() = preferences.getString(DEVICE_NAME_KEY, null) ?: defaultDeviceName()
        set(value) {
            preferences.edit().putString(DEVICE_NAME_KEY, value.trim()).apply()
        }

    /** Whether to accept an invite that asks for an unencrypted connection. */
    var allowInsecureInvites: Boolean
        get() = preferences.getBoolean(ALLOW_INSECURE_KEY, false)
        set(value) {
            preferences.edit().putBoolean(ALLOW_INSECURE_KEY, value).apply()
        }

    /** The SAF tree downloads are written into, or null until one is chosen. */
    var downloadTree: String?
        get() = preferences.getString(DOWNLOAD_TREE_KEY, null)
        set(value) {
            preferences.edit().putString(DOWNLOAD_TREE_KEY, value).apply()
        }

    companion object {
        private const val FILE_NAME = "lanlink-devices"
        private const val DEVICES_KEY = "devices"
        private const val CLIENT_ID_KEY = "client_id"
        private const val DEVICE_NAME_KEY = "device_name"
        private const val ALLOW_INSECURE_KEY = "allow_insecure"
        private const val DOWNLOAD_TREE_KEY = "download_tree"

        fun open(context: Context): SecureStore {
            val key = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            val preferences = EncryptedSharedPreferences.create(
                context,
                FILE_NAME,
                key,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
            return SecureStore(preferences)
        }

        fun defaultDeviceName(): String {
            val manufacturer = android.os.Build.MANUFACTURER.replaceFirstChar { it.uppercase() }
            val model = android.os.Build.MODEL
            return if (model.startsWith(manufacturer, ignoreCase = true)) model else "$manufacturer $model"
        }
    }
}
