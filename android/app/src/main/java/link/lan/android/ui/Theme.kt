package link.lan.android.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

/**
 * Light and dark, following the phone.
 *
 * On Android 12 and later the colours come from the wallpaper, which is what a
 * OnePlus running 14 will do and what people expect of a Material 3 app. The
 * fixed palette below is the fallback, and is the desktop application's blue so
 * the two halves of LanLink look related.
 */
private val LanLinkBlue = Color(0xFF1B4D8C)
private val LanLinkBlueLight = Color(0xFF9CC6FF)

private val LightColours = lightColorScheme(
    primary = LanLinkBlue,
    onPrimary = Color.White,
    secondary = Color(0xFF4A6488),
    surfaceTint = LanLinkBlue,
)

private val DarkColours = darkColorScheme(
    primary = LanLinkBlueLight,
    onPrimary = Color(0xFF00325B),
    secondary = Color(0xFFB3C8E8),
    surfaceTint = LanLinkBlueLight,
)

@Composable
fun LanLinkTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColour: Boolean = true,
    content: @Composable () -> Unit,
) {
    val context = LocalContext.current
    val colours = when {
        dynamicColour && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)

        darkTheme -> DarkColours
        else -> LightColours
    }

    MaterialTheme(colorScheme = colours, content = content)
}
