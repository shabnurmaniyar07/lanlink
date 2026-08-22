package link.lan.android.service

import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.provider.MediaStore
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import link.lan.app.KnownDevice
import link.lan.core.Pinning
import java.net.URL
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection

data class BackupState(
    val isRunning: Boolean = false,
    val totalCount: Int = 0,
    val completedCount: Int = 0,
    val currentFileName: String = "",
    val error: String? = null,
)

object CameraBackupCentre {
    private const val TAG = "CameraBackup"
    private const val PREFS_NAME = "lanlink_camera_backup"
    private const val KEY_BACKED_UP_IDS = "backed_up_media_ids"
    private const val KEY_AUTO_BACKUP_ENABLED = "auto_backup_enabled"

    private val scope = CoroutineScope(Dispatchers.IO)
    private var backupJob: Job? = null
    private lateinit var prefs: SharedPreferences

    private val _state = MutableStateFlow(BackupState())
    val state: StateFlow<BackupState> = _state.asStateFlow()

    var isAutoBackupEnabled: Boolean
        get() = if (::prefs.isInitialized) prefs.getBoolean(KEY_AUTO_BACKUP_ENABLED, false) else false
        set(value) {
            if (::prefs.isInitialized) {
                prefs.edit().putBoolean(KEY_AUTO_BACKUP_ENABLED, value).apply()
            }
        }

    fun init(context: Context) {
        prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    fun triggerBackup(context: Context, device: KnownDevice) {
        if (backupJob?.isActive == true) return
        backupJob = scope.launch {
            runBackup(context.applicationContext, device)
        }
    }

    fun stopBackup() {
        backupJob?.cancel()
        _state.value = _state.value.copy(isRunning = false, currentFileName = "Backup stopped")
    }

    private suspend fun runBackup(context: Context, device: KnownDevice) {
        _state.value = BackupState(isRunning = true, currentFileName = "Scanning media...")

        val backedUpIds = prefs.getStringSet(KEY_BACKED_UP_IDS, emptySet())?.toMutableSet() ?: mutableSetOf()
        val mediaList = mutableListOf<MediaItem>()

        val projection = arrayOf(
            MediaStore.MediaColumns._ID,
            MediaStore.MediaColumns.DISPLAY_NAME,
            MediaStore.MediaColumns.DATE_MODIFIED,
            MediaStore.MediaColumns.SIZE,
        )

        // Query Images
        val imageUri = MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        context.contentResolver.query(
            imageUri,
            projection,
            null,
            null,
            " DESC"
        )?.use { cursor ->
            val idCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
            val nameCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
            val dateCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_MODIFIED)
            val sizeCol = cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE)

            while (cursor.moveToNext()) {
                val id = cursor.getLong(idCol)
                val idStr = "img_"
                if (!backedUpIds.contains(idStr)) {
                    val name = cursor.getString(nameCol) ?: "photo_.jpg"
                    val dateModified = cursor.getLong(dateCol)
                    val size = cursor.getLong(sizeCol)
                    val contentUri = Uri.withAppendedPath(imageUri, id.toString())
                    mediaList.add(MediaItem(idStr, name, dateModified, size, contentUri))
                }
            }
        }

        val total = mediaList.size
        if (total == 0) {
            _state.value = BackupState(isRunning = false, totalCount = 0, completedCount = 0, currentFileName = "All photos already backed up")
            return
        }

        _state.value = BackupState(isRunning = true, totalCount = total, completedCount = 0, currentFileName = "Starting upload...")

        var completed = 0
        for (item in mediaList) {
            _state.value = _state.value.copy(
                completedCount = completed,
                currentFileName = item.name,
            )

            val success = uploadFile(context, device, item)
            if (success) {
                completed++
                backedUpIds.add(item.id)
                prefs.edit().putStringSet(KEY_BACKED_UP_IDS, backedUpIds).apply()
                _state.value = _state.value.copy(completedCount = completed)
            } else {
                Log.w(TAG, "Failed to upload: ")
            }
        }

        _state.value = BackupState(
            isRunning = false,
            totalCount = total,
            completedCount = completed,
            currentFileName = "Backup complete: / synced"
        )
    }

    private fun uploadFile(context: Context, device: KnownDevice, item: MediaItem): Boolean {
        return try {
            val url = URL("https://:/v1/backup/camera")
            val conn = url.openConnection() as HttpsURLConnection
            if (device.certificatePem.isNotBlank()) {
                conn.sslSocketFactory = Pinning.socketFactoryForPem(device.certificatePem)
            }
            conn.hostnameVerifier = HostnameVerifier { _, _ -> true }
            conn.requestMethod = "POST"
            conn.setRequestProperty("x-lanlink-token", device.token)
            conn.setRequestProperty("x-file-name", item.name)
            conn.setRequestProperty("x-modified-at", item.dateModified.toString())
            conn.setRequestProperty("Content-Type", "application/octet-stream")
            conn.connectTimeout = 8000
            conn.readTimeout = 30000
            conn.doOutput = true

            context.contentResolver.openInputStream(item.uri)?.use { input ->
                conn.outputStream.use { output ->
                    input.copyTo(output, bufferSize = 64 * 1024)
                }
            }

            val code = conn.responseCode
            conn.disconnect()
            code in 200..299
        } catch (e: Exception) {
            Log.e(TAG, "Upload error for : ")
            false
        }
    }

    private data class MediaItem(
        val id: String,
        val name: String,
        val dateModified: Long,
        val size: Long,
        val uri: Uri,
    )
}
