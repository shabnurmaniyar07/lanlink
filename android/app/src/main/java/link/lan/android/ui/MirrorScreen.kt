package link.lan.android.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
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
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.TouchApp
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import link.lan.app.KnownDevice
import link.lan.core.Pinning
import java.net.URL
import java.nio.charset.StandardCharsets
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MirrorScreen(
    currentDevice: KnownDevice?,
    allDevices: List<KnownDevice>,
    onSelectDevice: (KnownDevice) -> Unit,
    onBack: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var activeDevice by remember(currentDevice) { mutableStateOf(currentDevice) }
    var isStreaming by remember { mutableStateOf(true) }
    var currentBitmap by remember { mutableStateOf<Bitmap?>(null) }
    var fps by remember { mutableIntStateOf(0) }
    var latencyMs by remember { mutableIntStateOf(0) }
    var quality by remember { mutableIntStateOf(50) }
    var scaleWidth by remember { mutableIntStateOf(1080) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var deviceDropdownOpen by remember { mutableStateOf(false) }

    var containerSize by remember { mutableStateOf(IntSize.Zero) }

    fun sendTouchClick(xRatio: Float, yRatio: Float, click: Boolean = true, rclick: Boolean = false) {
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
                conn.connectTimeout = 1000
                conn.readTimeout = 1000
                conn.doOutput = true
                val payload = """{"action":"abs_move","x_ratio":$xRatio,"y_ratio":$yRatio,"click":$click,"rclick":$rclick}"""
                conn.outputStream.use { it.write(payload.toByteArray(StandardCharsets.UTF_8)) }
                conn.responseCode
                conn.disconnect()
            } catch (_: Exception) {}
        }
    }

    LaunchedEffect(activeDevice, isStreaming, quality, scaleWidth) {
        val dev = activeDevice ?: return@LaunchedEffect
        var frameCounter = 0
        var lastFpsTime = System.currentTimeMillis()

        while (isActive && isStreaming) {
            val start = System.currentTimeMillis()
            try {
                val url = URL("https://${dev.host}:${dev.port}/v1/screen/frame?quality=$quality&width=$scaleWidth")
                val conn = withContext(Dispatchers.IO) {
                    val c = url.openConnection() as HttpsURLConnection
                    if (dev.certificatePem.isNotBlank()) {
                        c.sslSocketFactory = Pinning.socketFactoryForPem(dev.certificatePem)
                    }
                    c.hostnameVerifier = HostnameVerifier { _, _ -> true }
                    c.setRequestProperty("x-lanlink-token", dev.token)
                    c.connectTimeout = 2000
                    c.readTimeout = 2000
                    c
                }

                val bytes = withContext(Dispatchers.IO) {
                    if (conn.responseCode == 200) {
                        conn.inputStream.use { it.readBytes() }
                    } else {
                        null
                    }
                }
                conn.disconnect()

                if (bytes != null && bytes.isNotEmpty()) {
                    val bmp = withContext(Dispatchers.Default) {
                        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    }
                    if (bmp != null) {
                        currentBitmap = bmp
                        errorMessage = null
                    }
                } else {
                    errorMessage = "Screen mirror disabled or unavailable on host"
                }

                latencyMs = (System.currentTimeMillis() - start).toInt()
                frameCounter++
                if (System.currentTimeMillis() - lastFpsTime >= 1000) {
                    fps = frameCounter
                    frameCounter = 0
                    lastFpsTime = System.currentTimeMillis()
                }

                delay(30)
            } catch (e: Exception) {
                errorMessage = "Connecting to display: ${e.message}"
                delay(1000)
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("Screen Mirror", fontWeight = FontWeight.Bold)
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
                            text = if (errorMessage != null) errorMessage!! else "${fps} FPS • ${latencyMs}ms latency",
                            style = MaterialTheme.typography.labelSmall,
                            color = if (errorMessage != null) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    IconButton(onClick = { isStreaming = !isStreaming }) {
                        Icon(if (isStreaming) Icons.Filled.Pause else Icons.Filled.PlayArrow, contentDescription = "Pause/Resume")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 12.dp, vertical = 8.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Quality:", style = MaterialTheme.typography.labelMedium)
                FilterChip(
                    selected = quality == 40,
                    onClick = { quality = 40; scaleWidth = 800 },
                    label = { Text("Fast (800p)") },
                )
                FilterChip(
                    selected = quality == 60,
                    onClick = { quality = 60; scaleWidth = 1280 },
                    label = { Text("HD (1280p)") },
                )
                FilterChip(
                    selected = quality == 80,
                    onClick = { quality = 80; scaleWidth = 1920 },
                    label = { Text("Ultra") },
                )
            }

            Spacer(Modifier.height(8.dp))

            Surface(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(16.dp)),
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(16.dp),
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .onSizeChanged { containerSize = it },
                    contentAlignment = Alignment.Center,
                ) {
                    val bmp = currentBitmap
                    if (bmp != null) {
                        Image(
                            bitmap = bmp.asImageBitmap(),
                            contentDescription = "Mirrored Laptop Screen",
                            contentScale = ContentScale.Fit,
                            modifier = Modifier
                                .fillMaxSize()
                                .pointerInput(Unit) {
                                    detectTapGestures(
                                        onTap = { offset ->
                                            val xRatio = (offset.x / containerSize.width).coerceIn(0f, 1f)
                                            val yRatio = (offset.y / containerSize.height).coerceIn(0f, 1f)
                                            sendTouchClick(xRatio, yRatio, click = true)
                                        },
                                        onLongPress = { offset ->
                                            val xRatio = (offset.x / containerSize.width).coerceIn(0f, 1f)
                                            val yRatio = (offset.y / containerSize.height).coerceIn(0f, 1f)
                                            sendTouchClick(xRatio, yRatio, rclick = true)
                                        }
                                    )
                                }
                                .pointerInput(Unit) {
                                    detectDragGestures { change, _ ->
                                        change.consume()
                                        val xRatio = (change.position.x / containerSize.width).coerceIn(0f, 1f)
                                        val yRatio = (change.position.y / containerSize.height).coerceIn(0f, 1f)
                                        sendTouchClick(xRatio, yRatio, click = false)
                                    }
                                },
                        )
                    } else {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.Center,
                        ) {
                            CircularProgressIndicator()
                            Spacer(Modifier.height(12.dp))
                            Text(
                                errorMessage ?: "Connecting to laptop display stream...",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }

            Spacer(Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Filled.TouchApp, contentDescription = null, modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(6.dp))
                Text(
                    "Tap to click • Drag to move cursor • Long press to right click",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
