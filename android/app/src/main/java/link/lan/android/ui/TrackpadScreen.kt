package link.lan.android.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.FastForward
import androidx.compose.material.icons.filled.FastRewind
import androidx.compose.material.icons.filled.Keyboard
import androidx.compose.material.icons.filled.Mouse
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material.icons.filled.VolumeDown
import androidx.compose.material.icons.filled.VolumeMute
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
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
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TrackpadScreen(
    currentDevice: KnownDevice?,
    allDevices: List<KnownDevice> = emptyList(),
    onSelectDevice: (KnownDevice) -> Unit = {},
    onBack: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var activeDevice by remember(currentDevice) { mutableStateOf(currentDevice) }
    var isPlaying by remember { mutableStateOf(true) }
    var sensitivity by remember { mutableFloatStateOf(1.8f) }
    var showKeyboardSheet by remember { mutableStateOf(false) }
    var textInput by remember { mutableStateOf("") }
    var deviceDropdownOpen by remember { mutableStateOf(false) }

    fun sendMouse(action: String, dx: Float = 0f, dy: Float = 0f, scroll: Int = 0) {
        val dev = activeDevice ?: return
        scope.launch(Dispatchers.IO) {
            try {
                val url = URL("https://${dev.host}:${dev.port}/v1/remote/mouse")
                val conn = url.openConnection() as HttpsURLConnection
                if (dev.certificatePem.isNotBlank()) {
                    conn.sslSocketFactory = Pinning.socketFactoryForPem(dev.certificatePem)
                }
                conn.hostnameVerifier = HostnameVerifier { _, _ -> true }
                conn.requestMethod = "POST"
                conn.setRequestProperty("x-lanlink-token", dev.token)
                conn.setRequestProperty("Content-Type", "application/json")
                conn.connectTimeout = 1500
                conn.readTimeout = 1500
                conn.doOutput = true
                val payload = """{"action":"$action","dx":${dx * sensitivity},"dy":${dy * sensitivity},"scroll":$scroll}"""
                conn.outputStream.use { it.write(payload.toByteArray(StandardCharsets.UTF_8)) }
                conn.responseCode
                conn.disconnect()
            } catch (_: Exception) {}
        }
    }

    fun sendKey(key: String) {
        val dev = activeDevice ?: return
        scope.launch(Dispatchers.IO) {
            try {
                val url = URL("https://${dev.host}:${dev.port}/v1/remote/keyboard")
                val conn = url.openConnection() as HttpsURLConnection
                if (dev.certificatePem.isNotBlank()) {
                    conn.sslSocketFactory = Pinning.socketFactoryForPem(dev.certificatePem)
                }
                conn.hostnameVerifier = HostnameVerifier { _, _ -> true }
                conn.requestMethod = "POST"
                conn.setRequestProperty("x-lanlink-token", dev.token)
                conn.setRequestProperty("Content-Type", "application/json")
                conn.connectTimeout = 1500
                conn.readTimeout = 1500
                conn.doOutput = true
                val payload = """{"action":"key","key":"$key"}"""
                conn.outputStream.use { it.write(payload.toByteArray(StandardCharsets.UTF_8)) }
                conn.responseCode
                conn.disconnect()
            } catch (_: Exception) {}
        }
    }

    fun sendText(text: String) {
        val dev = activeDevice ?: return
        if (text.isEmpty()) return
        scope.launch(Dispatchers.IO) {
            try {
                val url = URL("https://${dev.host}:${dev.port}/v1/remote/keyboard")
                val conn = url.openConnection() as HttpsURLConnection
                if (dev.certificatePem.isNotBlank()) {
                    conn.sslSocketFactory = Pinning.socketFactoryForPem(dev.certificatePem)
                }
                conn.hostnameVerifier = HostnameVerifier { _, _ -> true }
                conn.requestMethod = "POST"
                conn.setRequestProperty("x-lanlink-token", dev.token)
                conn.setRequestProperty("Content-Type", "application/json")
                conn.connectTimeout = 1500
                conn.readTimeout = 1500
                conn.doOutput = true
                val obj = org.json.JSONObject()
                obj.put("action", "text")
                obj.put("text", text)
                val payload = obj.toString()
                conn.outputStream.use { it.write(payload.toByteArray(StandardCharsets.UTF_8)) }
                conn.responseCode
                conn.disconnect()
            } catch (_: Exception) {}
        }
    }

    fun sendMedia(action: String) {
        val dev = activeDevice ?: return
        scope.launch(Dispatchers.IO) {
            try {
                val url = URL("https://${dev.host}:${dev.port}/v1/remote/media")
                val conn = url.openConnection() as HttpsURLConnection
                if (dev.certificatePem.isNotBlank()) {
                    conn.sslSocketFactory = Pinning.socketFactoryForPem(dev.certificatePem)
                }
                conn.hostnameVerifier = HostnameVerifier { _, _ -> true }
                conn.requestMethod = "POST"
                conn.setRequestProperty("x-lanlink-token", dev.token)
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
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("Virtual Trackpad & Remote", fontWeight = FontWeight.Bold)
                            Spacer(Modifier.width(8.dp))
                            if (allDevices.size > 1) {
                                Box {
                                    FilledTonalButton(
                                        onClick = { deviceDropdownOpen = true },
                                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 2.dp),
                                    ) {
                                        Icon(Icons.Filled.Computer, contentDescription = null, modifier = Modifier.size(16.dp))
                                        Spacer(Modifier.width(4.dp))
                                        Text(activeDevice?.name ?: "Select", style = MaterialTheme.typography.labelMedium)
                                    }
                                    DropdownMenu(
                                        expanded = deviceDropdownOpen,
                                        onDismissRequest = { deviceDropdownOpen = false }
                                    ) {
                                        allDevices.forEach { dev ->
                                            DropdownMenuItem(
                                                text = { Text(dev.name) },
                                                onClick = {
                                                    activeDevice = dev
                                                    onSelectDevice(dev)
                                                    deviceDropdownOpen = false
                                                }
                                            )
                                        }
                                    }
                                }
                            }
                        }
                        Text(
                            text = activeDevice?.name?.let { "Controlling $it" } ?: "No laptop connected",
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
                actions = {
                    IconButton(onClick = { showKeyboardSheet = !showKeyboardSheet }) {
                        Icon(Icons.Filled.Keyboard, contentDescription = "Keypad & Keyboard")
                    }
                }
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 14.dp, vertical = 8.dp),
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
                        .padding(horizontal = 6.dp, vertical = 4.dp),
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

            // Keyboard & Keypad Panel if toggled
            if (showKeyboardSheet) {
                Spacer(Modifier.height(8.dp))
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(14.dp),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.9f)),
                ) {
                    Column(modifier = Modifier.padding(10.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            OutlinedTextField(
                                value = textInput,
                                onValueChange = { textInput = it },
                                placeholder = { Text("Type text to send to laptop…") },
                                singleLine = true,
                                modifier = Modifier.weight(1f),
                            )
                            Spacer(Modifier.width(8.dp))
                            IconButton(onClick = {
                                sendText(textInput)
                                textInput = ""
                            }) {
                                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send Text")
                            }
                        }

                        Spacer(Modifier.height(8.dp))

                        // Keypad Action Keys
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            FilledTonalButton(onClick = { sendKey("enter") }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) {
                                Text("↵ Enter")
                            }
                            FilledTonalButton(onClick = { sendKey("backspace") }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) {
                                Text("⌫ Back")
                            }
                            FilledTonalButton(onClick = { sendKey("tab") }, modifier = Modifier.weight(0.8f), contentPadding = PaddingValues(2.dp)) {
                                Text("⇥ Tab")
                            }
                            FilledTonalButton(onClick = { sendKey("escape") }, modifier = Modifier.weight(0.8f), contentPadding = PaddingValues(2.dp)) {
                                Text("⎋ Esc")
                            }
                            FilledTonalButton(onClick = { sendKey("space") }, modifier = Modifier.weight(0.9f), contentPadding = PaddingValues(2.dp)) {
                                Text("␣ Space")
                            }
                        }

                        Spacer(Modifier.height(6.dp))

                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            FilledTonalButton(onClick = { sendKey("left") }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) {
                                Text("◄")
                            }
                            FilledTonalButton(onClick = { sendKey("up") }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) {
                                Text("▲")
                            }
                            FilledTonalButton(onClick = { sendKey("down") }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) {
                                Text("▼")
                            }
                            FilledTonalButton(onClick = { sendKey("right") }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) {
                                Text("►")
                            }
                            FilledTonalButton(onClick = { sendKey("ctrl_c") }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) {
                                Text("Copy")
                            }
                            FilledTonalButton(onClick = { sendKey("ctrl_v") }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(2.dp)) {
                                Text("Paste")
                            }
                        }
                    }
                }
            }

            Spacer(Modifier.height(8.dp))

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
                        modifier = Modifier.size(50.dp),
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Slide finger to move mouse • Tap to click • Hold to right click",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                    )
                }
            }

            Spacer(Modifier.height(8.dp))

            // Sensitivity Slider Row
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Filled.Speed, contentDescription = null, modifier = Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(6.dp))
                Text("Speed: ${"%.1f".format(sensitivity)}x", style = MaterialTheme.typography.labelSmall)
                Spacer(Modifier.width(8.dp))
                Slider(
                    value = sensitivity,
                    onValueChange = { sensitivity = it },
                    valueRange = 0.5f..3.5f,
                    modifier = Modifier.weight(1f),
                )
            }

            Spacer(Modifier.height(4.dp))

            // Physical Left & Right Click Bar
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(58.dp),
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
