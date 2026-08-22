package link.lan.android.ui

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ActivityInfo
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Image
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.Fullscreen
import androidx.compose.material.icons.filled.FullscreenExit
import androidx.compose.material.icons.filled.Mouse
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Rotate90DegreesCw
import androidx.compose.material.icons.filled.ScreenRotation
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material.icons.filled.TouchApp
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
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
import kotlin.math.roundToInt

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

enum class MouseInputMode {
    TRACKPAD, // Relative movement (Cursor continues where left off - default)
    DIRECT_TOUCH // Absolute coordinate jump (Stylus/Direct tap)
}

enum class ScreenOrientationOption(val label: String, val orientation: Int) {
    LANDSCAPE("Landscape", ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE),
    PORTRAIT("Portrait", ActivityInfo.SCREEN_ORIENTATION_PORTRAIT),
    AUTO("Auto-Sensor", ActivityInfo.SCREEN_ORIENTATION_SENSOR)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MirrorScreen(
    currentDevice: KnownDevice?,
    allDevices: List<KnownDevice>,
    onSelectDevice: (KnownDevice) -> Unit,
    onBack: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var activeDevice by remember(currentDevice) { mutableStateOf(currentDevice) }
    var isStreaming by remember { mutableStateOf(true) }
    var currentBitmap by remember { mutableStateOf<Bitmap?>(null) }
    var fps by remember { mutableIntStateOf(0) }
    var latencyMs by remember { mutableIntStateOf(0) }
    var quality by remember { mutableIntStateOf(50) }
    var scaleWidth by remember { mutableIntStateOf(1280) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var deviceDropdownOpen by remember { mutableStateOf(false) }

    // Fullscreen & Orientation state
    var isFullscreen by remember { mutableStateOf(false) }
    var orientationMode by remember { mutableStateOf(ScreenOrientationOption.LANDSCAPE) }
    var rotationAngle by remember { mutableIntStateOf(0) } // 0, 90, 180, 270 degrees
    var mouseMode by remember { mutableStateOf(MouseInputMode.TRACKPAD) }
    var sensitivity by remember { mutableFloatStateOf(1.2f) }
    var showControlsOverlay by remember { mutableStateOf(true) }

    var containerSize by remember { mutableStateOf(IntSize.Zero) }

    // Handle Android back press in fullscreen
    BackHandler(enabled = isFullscreen) {
        isFullscreen = false
    }

    // Apply orientation change directly on Activity
    LaunchedEffect(orientationMode) {
        val activity = context.findActivity()
        activity?.requestedOrientation = orientationMode.orientation
    }

    // Apply system status bar & navigation bar hiding for True Fullscreen
    LaunchedEffect(isFullscreen) {
        val activity = context.findActivity() ?: return@LaunchedEffect
        val window = activity.window ?: return@LaunchedEffect
        val insetsController = WindowCompat.getInsetsController(window, window.decorView)
        if (isFullscreen) {
            insetsController.hide(WindowInsetsCompat.Type.systemBars())
            insetsController.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        } else {
            insetsController.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    // Reset orientation & system bars when leaving screen
    DisposableEffect(Unit) {
        onDispose {
            val activity = context.findActivity()
            activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
            activity?.window?.let { win ->
                val insetsController = WindowCompat.getInsetsController(win, win.decorView)
                insetsController.show(WindowInsetsCompat.Type.systemBars())
            }
        }
    }

    // Remote mouse event sender
    fun sendMousePayload(payload: String) {
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
                conn.connectTimeout = 800
                conn.readTimeout = 800
                conn.doOutput = true
                conn.outputStream.use { it.write(payload.toByteArray(StandardCharsets.UTF_8)) }
                conn.responseCode
                conn.disconnect()
            } catch (_: Exception) {}
        }
    }

    // Relative mouse move (Cursor starts from where it was left)
    fun sendRelativeMove(rawDx: Float, rawDy: Float) {
        // Sync delta vector with manual canvas rotation
        val (dx, dy) = when (rotationAngle % 360) {
            90, -270 -> Pair(rawDy, -rawDx)
            180, -180 -> Pair(-rawDx, -rawDy)
            270, -90 -> Pair(-rawDy, rawDx)
            else -> Pair(rawDx, rawDy)
        }
        val scaledDx = (dx * sensitivity).roundToInt()
        val scaledDy = (dy * sensitivity).roundToInt()
        if (scaledDx != 0 || scaledDy != 0) {
            sendMousePayload("""{"action":"move","dx":$scaledDx,"dy":$scaledDy}""")
        }
    }

    // Click without moving cursor
    fun sendClick(rclick: Boolean = false) {
        val action = if (rclick) "rclick" else "click"
        sendMousePayload("""{"action":"$action"}""")
    }

    // Absolute touch click (for direct touch mode)
    fun sendDirectTouch(xRatio: Float, yRatio: Float, click: Boolean = true, rclick: Boolean = false) {
        sendMousePayload("""{"action":"abs_move","x_ratio":$xRatio,"y_ratio":$yRatio,"click":$click,"rclick":$rclick}""")
    }

    // Stream screen loop
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

                delay(20)
            } catch (e: Exception) {
                errorMessage = "Connecting to display: ${e.message}"
                delay(1000)
            }
        }
    }

    Scaffold(
        topBar = {
            if (!isFullscreen) {
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
                        // Rotation toggle button
                        IconButton(
                            onClick = {
                                orientationMode = when (orientationMode) {
                                    ScreenOrientationOption.LANDSCAPE -> ScreenOrientationOption.PORTRAIT
                                    ScreenOrientationOption.PORTRAIT -> ScreenOrientationOption.AUTO
                                    ScreenOrientationOption.AUTO -> ScreenOrientationOption.LANDSCAPE
                                }
                            }
                        ) {
                            Icon(Icons.Filled.ScreenRotation, contentDescription = "Rotate Screen (${orientationMode.label})")
                        }
                        // Rotate Canvas 90deg button
                        IconButton(onClick = { rotationAngle = (rotationAngle + 90) % 360 }) {
                            Icon(Icons.Filled.Rotate90DegreesCw, contentDescription = "Rotate Canvas")
                        }
                        // Fullscreen button
                        IconButton(onClick = { isFullscreen = true }) {
                            Icon(Icons.Filled.Fullscreen, contentDescription = "Enter Fullscreen")
                        }
                        // Play / Pause
                        IconButton(onClick = { isStreaming = !isStreaming }) {
                            Icon(if (isStreaming) Icons.Filled.Pause else Icons.Filled.PlayArrow, contentDescription = "Pause/Resume")
                        }
                    }
                )
            }
        }
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black)
                .padding(if (isFullscreen) PaddingValues(0.dp) else padding)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(if (isFullscreen) 0.dp else 8.dp),
            ) {
                // Top control bar when not fullscreen
                if (!isFullscreen) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 4.dp, vertical = 2.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            FilterChip(
                                selected = mouseMode == MouseInputMode.TRACKPAD,
                                onClick = { mouseMode = MouseInputMode.TRACKPAD },
                                leadingIcon = { Icon(Icons.Filled.Mouse, contentDescription = null, modifier = Modifier.size(14.dp)) },
                                label = { Text("Trackpad Cursor", style = MaterialTheme.typography.labelSmall) },
                            )
                            FilterChip(
                                selected = mouseMode == MouseInputMode.DIRECT_TOUCH,
                                onClick = { mouseMode = MouseInputMode.DIRECT_TOUCH },
                                leadingIcon = { Icon(Icons.Filled.TouchApp, contentDescription = null, modifier = Modifier.size(14.dp)) },
                                label = { Text("Direct Touch", style = MaterialTheme.typography.labelSmall) },
                            )
                        }

                        Row(
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            FilterChip(
                                selected = quality == 40,
                                onClick = { quality = 40; scaleWidth = 800 },
                                label = { Text("800p") },
                            )
                            FilterChip(
                                selected = quality == 60,
                                onClick = { quality = 60; scaleWidth = 1280 },
                                label = { Text("HD") },
                            )
                            FilterChip(
                                selected = quality == 80,
                                onClick = { quality = 80; scaleWidth = 1920 },
                                label = { Text("Ultra") },
                            )
                        }
                    }
                    Spacer(Modifier.height(4.dp))
                }

                // Main Display Canvas
                Surface(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .clip(if (isFullscreen) RoundedCornerShape(0.dp) else RoundedCornerShape(16.dp)),
                    color = Color.Black,
                    shape = if (isFullscreen) RoundedCornerShape(0.dp) else RoundedCornerShape(16.dp),
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
                                    .rotate(rotationAngle.toFloat())
                                    .pointerInput(mouseMode, rotationAngle, sensitivity, containerSize) {
                                        if (mouseMode == MouseInputMode.TRACKPAD) {
                                            // TRACKPAD MODE: Relative drag movement (Cursor starts where left off)
                                            detectTapGestures(
                                                onTap = {
                                                    sendClick(rclick = false)
                                                },
                                                onDoubleTap = {
                                                    sendClick(rclick = false)
                                                    sendClick(rclick = false)
                                                },
                                                onLongPress = {
                                                    sendClick(rclick = true)
                                                }
                                            )
                                        } else {
                                            // DIRECT TOUCH MODE: Absolute point jump
                                            detectTapGestures(
                                                onTap = { offset ->
                                                    val xRatio = (offset.x / containerSize.width).coerceIn(0f, 1f)
                                                    val yRatio = (offset.y / containerSize.height).coerceIn(0f, 1f)
                                                    sendDirectTouch(xRatio, yRatio, click = true)
                                                },
                                                onLongPress = { offset ->
                                                    val xRatio = (offset.x / containerSize.width).coerceIn(0f, 1f)
                                                    val yRatio = (offset.y / containerSize.height).coerceIn(0f, 1f)
                                                    sendDirectTouch(xRatio, yRatio, rclick = true)
                                                }
                                            )
                                        }
                                    }
                                    .pointerInput(mouseMode, rotationAngle, sensitivity, containerSize) {
                                        if (mouseMode == MouseInputMode.TRACKPAD) {
                                            detectDragGestures { change, dragAmount ->
                                                change.consume()
                                                sendRelativeMove(dragAmount.x, dragAmount.y)
                                            }
                                        } else {
                                            detectDragGestures { change, _ ->
                                                change.consume()
                                                val xRatio = (change.position.x / containerSize.width).coerceIn(0f, 1f)
                                                val yRatio = (change.position.y / containerSize.height).coerceIn(0f, 1f)
                                                sendDirectTouch(xRatio, yRatio, click = false)
                                            }
                                        }
                                    },
                            )
                        } else {
                            Column(
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.Center,
                            ) {
                                CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
                                Spacer(Modifier.height(12.dp))
                                Text(
                                    errorMessage ?: "Connecting to laptop display stream...",
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = Color.White.copy(alpha = 0.8f),
                                )
                            }
                        }
                    }
                }

                if (!isFullscreen) {
                    Spacer(Modifier.height(6.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Filled.Speed, contentDescription = null, modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
                            Spacer(Modifier.width(4.dp))
                            Text("Speed: ${(sensitivity * 10).roundToInt() / 10f}x", style = MaterialTheme.typography.labelSmall, color = Color.White.copy(alpha = 0.7f))
                        }
                        Slider(
                            value = sensitivity,
                            onValueChange = { sensitivity = it },
                            valueRange = 0.5f..3.0f,
                            modifier = Modifier.width(160.dp)
                        )
                        IconButton(onClick = { rotationAngle = (rotationAngle + 90) % 360 }) {
                            Icon(Icons.Filled.Rotate90DegreesCw, contentDescription = "Rotate Canvas", tint = Color.White)
                        }
                    }
                }
            }

            // Floating Fullscreen Toolbar Overlay
            if (isFullscreen) {
                AnimatedVisibility(
                    visible = showControlsOverlay,
                    enter = fadeIn(),
                    exit = fadeOut(),
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(16.dp)
                ) {
                    Surface(
                        shape = RoundedCornerShape(24.dp),
                        color = Color.Black.copy(alpha = 0.65f),
                        tonalElevation = 6.dp,
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                "${fps} FPS",
                                style = MaterialTheme.typography.labelSmall,
                                color = Color.White,
                                modifier = Modifier.padding(horizontal = 6.dp)
                            )
                            // Toggle Orientation
                            FilledTonalIconButton(
                                onClick = {
                                    orientationMode = when (orientationMode) {
                                        ScreenOrientationOption.LANDSCAPE -> ScreenOrientationOption.PORTRAIT
                                        ScreenOrientationOption.PORTRAIT -> ScreenOrientationOption.AUTO
                                        ScreenOrientationOption.AUTO -> ScreenOrientationOption.LANDSCAPE
                                    }
                                },
                                modifier = Modifier.size(36.dp),
                                shape = CircleShape,
                            ) {
                                Icon(Icons.Filled.ScreenRotation, contentDescription = "Orientation", modifier = Modifier.size(18.dp))
                            }
                            // Rotate Canvas 90deg
                            FilledTonalIconButton(
                                onClick = { rotationAngle = (rotationAngle + 90) % 360 },
                                modifier = Modifier.size(36.dp),
                                shape = CircleShape,
                            ) {
                                Icon(Icons.Filled.Rotate90DegreesCw, contentDescription = "Rotate Canvas", modifier = Modifier.size(18.dp))
                            }
                            // Mouse mode toggle
                            FilledTonalIconButton(
                                onClick = {
                                    mouseMode = if (mouseMode == MouseInputMode.TRACKPAD) MouseInputMode.DIRECT_TOUCH else MouseInputMode.TRACKPAD
                                },
                                modifier = Modifier.size(36.dp),
                                shape = CircleShape,
                            ) {
                                Icon(
                                    if (mouseMode == MouseInputMode.TRACKPAD) Icons.Filled.Mouse else Icons.Filled.TouchApp,
                                    contentDescription = "Mouse Mode",
                                    modifier = Modifier.size(18.dp)
                                )
                            }
                            // Exit Fullscreen
                            FilledTonalIconButton(
                                onClick = { isFullscreen = false },
                                modifier = Modifier.size(36.dp),
                                shape = CircleShape,
                            ) {
                                Icon(Icons.Filled.FullscreenExit, contentDescription = "Exit Fullscreen", modifier = Modifier.size(18.dp))
                            }
                        }
                    }
                }
            }
        }
    }
}
