package link.lan.android.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.FastForward
import androidx.compose.material.icons.filled.FastRewind
import androidx.compose.material.icons.filled.Mouse
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.VolumeDown
import androidx.compose.material.icons.filled.VolumeMute
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import link.lan.app.KnownDevice
import link.lan.core.Pinning
import java.net.URL
import java.nio.charset.StandardCharsets

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TrackpadScreen(
    device: KnownDevice?,
    onBack: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var isPlaying by remember { mutableStateOf(true) }

    fun sendMouse(action: String, dx: Float = 0f, dy: Float = 0f, scroll: Int = 0) {
        if (device == null) return
        scope.launch(Dispatchers.IO) {
            try {
                val url = URL("https://${device.host}:${device.port}/v1/remote/mouse")
                val conn = url.openConnection() as javax.net.ssl.HttpsURLConnection
                if (device.certificatePem.isNotBlank()) {
                    conn.sslSocketFactory = Pinning.socketFactoryForPem(device.certificatePem)
                }
                conn.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }
                conn.requestMethod = "POST"
                conn.setRequestProperty("x-lanlink-token", device.token)
                conn.setRequestProperty("Content-Type", "application/json")
                conn.connectTimeout = 1500
                conn.readTimeout = 1500
                conn.doOutput = true
                val payload = """{"action":"$action","dx":${dx * 2.2f},"dy":${dy * 2.2f},"scroll":$scroll}"""
                conn.outputStream.use { it.write(payload.toByteArray(StandardCharsets.UTF_8)) }
                conn.responseCode
                conn.disconnect()
            } catch (_: Exception) {}
        }
    }

    fun sendMedia(action: String) {
        if (device == null) return
        scope.launch(Dispatchers.IO) {
            try {
                val url = URL("https://${device.host}:${device.port}/v1/remote/media")
                val conn = url.openConnection() as javax.net.ssl.HttpsURLConnection
                if (device.certificatePem.isNotBlank()) {
                    conn.sslSocketFactory = Pinning.socketFactoryForPem(device.certificatePem)
                }
                conn.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }
                conn.requestMethod = "POST"
                conn.setRequestProperty("x-lanlink-token", device.token)
                conn.setRequestProperty("Content-Type", "application/json")
                conn.connectTimeout = 1500
                conn.readTimeout = 1500
                conn.doOutput = true
                val payload = """{"action":"$action"}"""
                conn.outputStream.use { it.write(payload.toByteArray(StandardCharsets.UTF_8)) }
                conn.responseCode
                conn.disconnect()
            } catch (_: Exception) {}
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Virtual Trackpad & Remote", fontWeight = FontWeight.Bold)
                        Text(
                            text = device?.name?.let { "Controlling $it" } ?: "No laptop connected",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
        ) {
            // Media Control Toolbar Card
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 8.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.SpaceEvenly,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(onClick = { sendMedia("prev") }) {
                        Icon(Icons.Filled.FastRewind, contentDescription = "Previous")
                    }
                    IconButton(onClick = {
                        isPlaying = !isPlaying
                        sendMedia("play_pause")
                    }) {
                        Icon(if (isPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow, contentDescription = "Play/Pause")
                    }
                    IconButton(onClick = { sendMedia("next") }) {
                        Icon(Icons.Filled.FastForward, contentDescription = "Next")
                    }
                    IconButton(onClick = { sendMedia("volume_down") }) {
                        Icon(Icons.Filled.VolumeDown, contentDescription = "Volume -")
                    }
                    IconButton(onClick = { sendMedia("volume_up") }) {
                        Icon(Icons.Filled.VolumeUp, contentDescription = "Volume +")
                    }
                    IconButton(onClick = { sendMedia("mute") }) {
                        Icon(Icons.Filled.VolumeMute, contentDescription = "Mute")
                    }
                }
            }

            Spacer(Modifier.height(14.dp))

            // Main Touchpad Area
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(16.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f))
                    .pointerInput(Unit) {
                        detectTapGestures(
                            onTap = { sendMouse("click") },
                            onLongPress = { sendMouse("rclick") },
                        )
                    }
                    .pointerInput(Unit) {
                        detectDragGestures { change, dragAmount ->
                            change.consume()
                            sendMouse("move", dx = dragAmount.x, dy = dragAmount.y)
                        }
                    },
                contentAlignment = Alignment.Center,
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(
                        Icons.Filled.Mouse,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.35f),
                        modifier = Modifier.size(54.dp),
                    )
                    Spacer(Modifier.height(10.dp))
                    Text(
                        "Slide finger to move mouse pointer\nTap to left click • Hold to right click",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                    )
                }
            }

            Spacer(Modifier.height(14.dp))

            // Physical Left & Right Click Bar
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(64.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                FilledTonalButton(
                    onClick = { sendMouse("click") },
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier
                        .weight(1.3f)
                        .fillMaxSize(),
                ) {
                    Text("Left Click", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.bodyLarge)
                }

                FilledTonalButton(
                    onClick = { sendMouse("rclick") },
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxSize(),
                ) {
                    Text("Right Click", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.bodyLarge)
                }
            }
        }
    }
}